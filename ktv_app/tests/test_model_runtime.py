import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from ktv_app.config import Settings
from ktv_app.model_runtime import probe_device, resolve_device, worker_environment
from ktv_app.workflow import AlignmentOptions


def fake_torch(available):
    return SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: available))


@pytest.mark.parametrize("device", ["cpu", "auto", "mps", "", None])
def test_unsupported_devices_are_rejected_at_all_entry_points(device):
    for call in (
        lambda: Settings(device=device),
        lambda: AlignmentOptions(device=device),
        lambda: resolve_device(fake_torch(True), device),
        lambda: worker_environment(device),
        lambda: probe_device("not-a-python", device),
    ):
        with pytest.raises(ValueError):
            call()


def test_cuda_unavailable_never_falls_back():
    with pytest.raises(RuntimeError, match="CUDA"):
        resolve_device(fake_torch(False))
    assert resolve_device(fake_torch(True)) == "cuda"


def test_old_environment_preferences_cannot_enable_cpu(monkeypatch):
    monkeypatch.setenv("KTV_DEVICE", "cpu")
    monkeypatch.setenv("TIFA_DEVICE", "auto")
    assert Settings().device == "cuda"
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "1")
    assert worker_environment()["CUDA_VISIBLE_DEVICES"] == "1"


@pytest.mark.parametrize("bf16,precision", [(True, "bf16"), (False, "fp16")])
def test_cuda_probe_uses_selected_environment_and_actual_result(
    tmp_path, monkeypatch, bf16, precision
):
    import ktv_app.model_runtime as module

    captured = []

    def launch(args, **kwargs):
        captured.append((args, kwargs))
        kwargs["stdout"].write(
            (
                module.PROBE_PREFIX + json.dumps({"cuda": True, "bf16": bf16}) + "\n"
            ).encode()
        )
        kwargs["stdout"].flush()
        return SimpleNamespace(returncode=0, poll=lambda: 0)

    monkeypatch.setattr(module.subprocess, "Popen", launch)
    python = tmp_path / "环境 with spaces/Scripts/python.exe"
    events = []
    assert probe_device(python, event=events.append) == {
        "device": "cuda",
        "precision": precision,
    }
    assert captured[0][0][0] == str(python.resolve())
    assert captured[0][1]["cwd"] == python.parent
    assert events[0].startswith("检查") and "就绪" in events[-1]


@pytest.mark.parametrize(
    "output,code",
    [
        ("", 0),
        ('{"cuda": false, "bf16": false}', 0),
        ('{"cuda": true}', 0),
        ('{"cuda": true, "bf16": true}', 1),
    ],
)
def test_failed_or_malformed_probe_is_actionable_without_raw_command(
    monkeypatch, output, code
):
    import ktv_app.model_runtime as module

    def launch(args, **kwargs):
        kwargs["stdout"].write((module.PROBE_PREFIX + output).encode())
        kwargs["stdout"].flush()
        return SimpleNamespace(returncode=code, poll=lambda: code)

    monkeypatch.setattr(module.subprocess, "Popen", launch)
    with pytest.raises(RuntimeError, match="install.cmd") as error:
        probe_device("python")
    assert "Command '['" not in str(error.value)


@pytest.mark.parametrize("phase,limit", [("import", 300), ("cuda", 60)])
def test_probe_timeout_reports_stage_and_cleans_up_only_its_process(
    tmp_path, monkeypatch, phase, limit
):
    import ktv_app.model_runtime as module

    clock = [0.0]
    stopped, events = [], []
    process = SimpleNamespace(returncode=None, poll=lambda: None, pid=987654)

    def launch(args, **kwargs):
        kwargs["stdout"].write(
            (module.PROBE_PREFIX + json.dumps({"stage": phase}) + "\n").encode()
        )
        kwargs["stdout"].flush()
        return process

    monkeypatch.setattr(module.subprocess, "Popen", launch)
    monkeypatch.setattr(module, "_stop_probe", stopped.append)
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        module.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds)
    )
    with pytest.raises(RuntimeError, match="超时") as error:
        probe_device(tmp_path / "separation/Scripts/python.exe", event=events.append)
    assert clock[0] == limit
    assert stopped == [process]
    assert ("加载 PyTorch" if phase == "import" else "初始化 CUDA") in str(error.value)
    assert len(events) >= 4


def test_windows_probe_cleanup_targets_its_process_tree(monkeypatch):
    import ktv_app.model_runtime as module

    calls = []
    state = [None]

    class Process:
        pid = 123456

        def poll(self):
            return state[0]

        def kill(self):
            state[0] = -9

        def wait(self, timeout):
            return state[0]

    def terminate(args, **kwargs):
        calls.append(args)
        state[0] = -1

    monkeypatch.setattr(module.subprocess, "run", terminate)
    # This test runs on Windows; the POSIX branch kills the direct process.
    module._stop_probe(Process())
    if module.os.name == "nt":
        assert calls == [["taskkill", "/PID", "123456", "/T", "/F"]]
    assert state[0] is not None


def test_asr_request_records_cuda_runtime(tmp_path, monkeypatch):
    import ktv_app.aligners.recognition as module
    from ktv_app.config import Settings
    from ktv_app.timeline import Line

    audio, model, asr, python = (
        tmp_path / "audio.wav",
        tmp_path / "tifa/model.pt",
        tmp_path / "asr",
        tmp_path / "python.exe",
    )
    audio.write_bytes(b"audio")
    model.parent.mkdir()
    model.write_bytes(b"tifa")
    model.with_name("config.yaml").write_text("config")
    asr.mkdir()
    (asr / "config.json").write_text("{}")
    python.write_bytes(b"python")
    monkeypatch.setattr(
        module,
        "probe_device",
        lambda py, requested, **kwargs: {
            "device": requested,
            "precision": "bf16",
        },
    )
    calls = []

    def run(args, **kwargs):
        request = json.loads(Path(args[-1]).read_text())
        calls.append(request)
        assert request["device"] == "cuda"
        assert request["precision"] == "bf16"
        Path(request["work"], "error.json").write_text('{"message":"expected fixture"}')
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(module.subprocess, "run", run)
    for device in ("cuda",):
        settings = Settings(
            device=device,
            locator_python=python,
            tifa_python=python,
            tifa_model=model,
            asr_model=asr,
        )
        module.locate(
            audio,
            [Line(id="l", text="hello", language="en")],
            settings,
            tmp_path / "work",
        )
    assert [item["device"] for item in calls] == ["cuda"]


@pytest.mark.parametrize("device,accelerator", [("cuda", "gpu")])
def test_tifa_predictor_explicitly_selects_one_device(monkeypatch, device, accelerator):
    from ktv_app.aligners.worker import run_inference_on_device

    settings, predictions = [], []

    class Trainer:
        def __init__(self, **kwargs):
            settings.append(kwargs)

        def predict(self, module, dataloader):
            predictions.append((module, dataloader))

    lightning = SimpleNamespace(pytorch=SimpleNamespace(Trainer=Trainer))
    monkeypatch.setitem(sys.modules, "lightning", lightning)
    monkeypatch.setitem(sys.modules, "lightning.pytorch", lightning.pytorch)
    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(
            utils=SimpleNamespace(
                data=SimpleNamespace(
                    DataLoader=lambda dataset, **kwargs: (dataset, kwargs)
                )
            )
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "inference.module",
        SimpleNamespace(
            ForcedAlignmentInferenceModule=lambda backend: ("upstream_decoder", backend)
        ),
    )
    run_inference_on_device("backend", "dataset", ["callback"], device)
    assert settings[0]["accelerator"] == accelerator
    assert settings[0]["devices"] == 1
    assert settings[0]["precision"] == "32-true"
    assert predictions[0][0] == ("upstream_decoder", "backend")
    assert predictions[0][1][1]["batch_size"] == 1
    assert predictions[0][1][1]["num_workers"] == 0
