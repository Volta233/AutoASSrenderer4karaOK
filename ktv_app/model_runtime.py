"""CUDA requirements and bounded startup checks for isolated model environments."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

PROBE_PREFIX = "LYRIFRAME_GPU="
IMPORT_TIMEOUT = 300
CUDA_TIMEOUT = 60


def require_cuda(requested):
    if requested != "cuda":
        raise ValueError("此版本仅支持 NVIDIA CUDA 推理，请使用 CUDA 版环境")


def resolve_device(torch, requested="cuda"):
    require_cuda(requested)
    if not torch.cuda.is_available():
        raise RuntimeError(
            "当前模型环境无法使用 NVIDIA CUDA；请检查显卡驱动并运行 install.cmd 修复环境"
        )
    return "cuda"


def worker_environment(device="cuda"):
    require_cuda(device)
    env = os.environ.copy()
    env.update(PYTHONUTF8="1", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    return env


def _probe_records(output):
    if isinstance(output, bytes):
        output = output.decode("utf-8", errors="replace")
    records = []
    for line in (output or "").splitlines():
        if line.startswith(PROBE_PREFIX):
            try:
                record = json.loads(line[len(PROBE_PREFIX) :])
                if isinstance(record, dict):
                    records.append(record)
            except ValueError:
                pass  # A reader may observe a partially flushed final line.
    return records


def _stop_probe(process):
    """Windows venv launchers may have a child holding stdout open."""
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
    if process.poll() is None:
        process.kill()
    process.wait(timeout=10)


def probe_device(python, requested="cuda", event=lambda message: None):
    """Allow cold DLL loading, then check actual CUDA operations with progress.

    Import and CUDA initialization have separate budgets. An unavailable GPU or
    expired check fails explicitly; it never starts CPU inference.
    """
    require_cuda(requested)
    python = Path(python).resolve()
    label = python.parent.parent.name
    phases = {"import": "加载 PyTorch", "cuda": "初始化 CUDA 并检查显卡计算"}
    phase = "import"
    started = time.monotonic()
    deadline = started + IMPORT_TIMEOUT
    next_notice = started + 15
    event(f"检查 {label} 显卡环境：{phases[phase]}（首次加载可能较慢）")
    process = None
    try:
        # File output exposes progress during Windows DLL imports without a
        # pipe-reader thread that could remain blocked in a venv child process.
        with tempfile.TemporaryDirectory(prefix="lyriframe-gpu-") as temporary:
            output = Path(temporary) / "probe.log"
            with output.open("wb") as log:
                process = subprocess.Popen(
                    [str(python), "-u", str(Path(__file__).with_name("gpu_probe.py"))],
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    env=worker_environment(),
                    cwd=python.parent,
                )
                try:
                    while True:
                        records = _probe_records(output.read_bytes())
                        now = time.monotonic()
                        if phase == "import" and any(
                            r.get("stage") == "cuda" for r in records
                        ):
                            phase = "cuda"
                            deadline = now + CUDA_TIMEOUT
                        if process.poll() is not None:
                            records = _probe_records(output.read_bytes())
                            result = next(
                                (r for r in reversed(records) if "cuda" in r), None
                            )
                            if (
                                process.returncode
                                or not result
                                or result.get("cuda") is not True
                                or not isinstance(result.get("bf16"), bool)
                            ):
                                raise RuntimeError(
                                    f"{label} 无法完成 CUDA 计算检查；请确认 NVIDIA 驱动正常，"
                                    "并运行 install.cmd 安装或修复 CUDA 依赖。"
                                )
                            event(f"{label} 显卡环境就绪（{now - started:.1f} 秒）")
                            return {
                                "device": "cuda",
                                "precision": "bf16" if result["bf16"] else "fp16",
                            }
                        if now >= deadline:
                            raise RuntimeError(
                                f"{label} 显卡环境检查超时，停在“{phases[phase]}”。"
                                "请重试；若再次超时，运行 check.cmd 检查环境与驱动。"
                            )
                        if now >= next_notice:
                            event(
                                f"检查 {label} 显卡环境：{phases[phase]}"
                                f"（已等待 {now - started:.0f} 秒）"
                            )
                            next_notice = now + 15
                        time.sleep(min(1, deadline - now))
                finally:
                    _stop_probe(process)
    except OSError as error:
        raise RuntimeError(
            f"无法启动 {label} 模型环境，请运行 install.cmd 修复"
        ) from error
