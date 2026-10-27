"""Kim vocals for inference; the original video always supplies export audio."""

import json
from pathlib import Path
import subprocess
import time

import numpy as np
import soundfile as sf

from .media import fingerprint, run
from .model_runtime import probe_device, worker_environment

KIM_SHA256 = "87201f4d31afb5bc79993230fc49446918425574db48c01c405e44f365c7559e"


def validate_audio(path, original):
    actual, reference = sf.info(path), sf.info(original)
    if (actual.samplerate, actual.channels, actual.subtype, actual.frames) != (
        16000,
        1,
        "FLOAT",
        reference.frames,
    ):
        raise ValueError("人声对齐输入必须为与原音轨等长的16kHz单声道浮点音频")
    for block in sf.blocks(path, blocksize=65536, dtype="float32"):
        if not np.isfinite(block).all():
            raise ValueError("人声包含无效采样值")


def prepare_alignment_audio(
    video, original, media, folder, method, settings, event=lambda message: None
):
    original = Path(original)
    if method == "none":
        return original, dict(
            method="original_mix",
            origin=media["audio_offset"],
            sha256=fingerprint(original),
            final_audio="original video",
        )
    if method != "kim":
        raise ValueError("未知人声分离方式")
    config = settings.kim_source / "configs/config_vocals_mel_band_roformer.yaml"
    for path in (
        settings.separation_python,
        settings.kim_model,
        config,
        settings.kim_source / "utils.py",
    ):
        if not path.is_file():
            raise FileNotFoundError(f"Kim 分离组件未就绪：{path}")
    checkpoint_hash = fingerprint(settings.kim_model)
    if checkpoint_hash != KIM_SHA256:
        raise ValueError("Kim 权重与已验证版本不一致")
    runtime = probe_device(settings.separation_python, settings.device, event=event)
    # The official Kim demixer uses CUDA autocast FP16, regardless of BF16 support.
    runtime["precision"] = "fp16"
    identity = dict(
        video_sha256=media["sha256"],
        mixture_sha256=fingerprint(original),
        checkpoint_sha256=checkpoint_hash,
        config_sha256=fingerprint(config),
        source_sha256={
            p.relative_to(settings.kim_source).as_posix(): fingerprint(p)
            for p in sorted(settings.kim_source.rglob("*.py"))
            if "__pycache__" not in p.parts
        },
        origin=media["audio_offset"],
        runtime={
            **runtime,
            "requested_device": settings.device,
            "cpu_threads": getattr(settings, "cpu_threads", 8),
        },
        worker_sha256=fingerprint(Path(__file__).with_name("separation_worker.py")),
        protocol=3,
    )
    work = Path(folder) / "separation/kim"
    work.mkdir(parents=True, exist_ok=True)
    target, metadata = work / "alignment.wav", work / "alignment_audio.json"
    if metadata.exists() and target.exists():
        saved = json.loads(metadata.read_text(encoding="utf-8"))
        if saved["identity"] == identity and saved["sha256"] == fingerprint(target):
            validate_audio(target, original)
            event("复用已核验的 Kim 人声缓存；最终视频仍保留原音轨")
            return target, saved
    event(
        f"Kim 开始提取人声（{runtime['device'].upper()} / {runtime['precision'].upper()}）；完成后开始 ASR"
    )
    mixture, stem = work / "mixture.wav", work / "vocals.wav"
    run(
        [
            settings.tool("ffmpeg"),
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-i",
            video,
            "-map",
            "0:a:0",
            "-vn",
            "-af",
            "asetpts=PTS-STARTPTS",
            "-ac",
            "2",
            "-ar",
            "44100",
            "-c:a",
            "pcm_f32le",
            mixture,
        ],
        log=work / "extract.log",
    )
    request = work / "request.json"
    request.write_text(
        json.dumps(
            dict(
                source=str(settings.kim_source),
                checkpoint=str(settings.kim_model),
                mixture=str(mixture),
                output=str(stem),
                **runtime,
                cpu_threads=getattr(settings, "cpu_threads", 8),
            ),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    env = worker_environment(runtime["device"])
    with (work / "infer.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [
                str(settings.separation_python),
                str(Path(__file__).with_name("separation_worker.py")),
                str(request),
            ],
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        started = time.monotonic()
        deadline = started + 7200
        next_notice = started + 30
        try:
            while process.poll() is None:
                if time.monotonic() > deadline:
                    raise subprocess.TimeoutExpired(process.args, 7200)
                if time.monotonic() >= next_notice:
                    event(
                        f"Kim 正在提取人声（已运行 {time.monotonic() - started:.0f} 秒），完成后自动进入歌词识别"
                    )
                    next_notice = time.monotonic() + 30
                time.sleep(1)
        except BaseException:
            process.kill()
            process.wait(timeout=30)
            raise
    if process.returncode or not stem.exists():
        raise RuntimeError(f"Kim 推理失败，请查看 {work / 'infer.log'}")
    if sf.info(stem).frames != sf.info(mixture).frames:
        raise ValueError("Kim 输出长度发生变化；停止使用，避免字幕整体错移")
    temporary = work / "alignment.tmp.wav"
    run(
        [
            settings.tool("ffmpeg"),
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-i",
            stem,
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_f32le",
            temporary,
        ]
    )
    # Resampling endpoint rounding may differ by at most two samples.
    samples, sr = sf.read(temporary, dtype="float32")
    frames = sf.info(original).frames
    delta = frames - len(samples)
    if abs(delta) > 2:
        raise ValueError("重采样人声长度与原推理音轨不一致")
    if delta > 0:
        samples = np.pad(samples, (0, delta))
    elif delta < 0:
        samples = samples[:frames]
    sf.write(temporary, samples, sr, subtype="FLOAT")
    validate_audio(temporary, original)
    temporary.replace(target)
    evidence = dict(
        method="kim",
        identity=identity,
        sha256=fingerprint(target),
        origin=media["audio_offset"],
        frames=frames,
        resampling_endpoint_samples=delta,
        final_audio="original video",
        model_info=json.loads(stem.with_suffix(".json").read_text(encoding="utf-8")),
    )
    saved = metadata.with_suffix(".json.tmp")
    saved.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    saved.replace(metadata)
    event("Kim 人声已核验：长度、起点一致；开始对齐")
    return target, evidence
