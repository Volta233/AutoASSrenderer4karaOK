"""Match independent, timed ASR evidence to a subsequence of source lyrics."""

import hashlib

import json

from pathlib import Path

import subprocess


from ..media import fingerprint
from ..model_runtime import probe_device, worker_environment

from ..timeline import canonical
from ..text_normalization import normalize_lyrics


def match_text(text):
    text = canonical(normalize_lyrics(text)[0])
    return "".join(chr(ord(c) - 0x60) if "\u30a1" <= c <= "\u30f6" else c for c in text)


def japanese_reading_table(lines, recognition, settings, work):
    """The TIFA environment owns the dictionary; display and ASR prompts stay intact."""
    if not any(line.language == "ja" for line in lines):
        return {}, {"available": False, "reason": "not_japanese"}
    folder = Path(work) / "readings"
    folder.mkdir(parents=True, exist_ok=True)
    texts = list(
        dict.fromkeys(
            [line.text for line in lines]
            + [
                token["text"]
                for key in ("tokens", "verification_tokens")
                for token in recognition.get(key, [])
            ]
            + [chunk["text"] for chunk in recognition.get("chunks", [])]
        )
    )
    output = folder / "results.json"
    request = folder / "request.json"
    request.write_text(
        json.dumps(
            {
                "texts": texts,
                "output": str(output.resolve()),
                "dictionary": str(
                    settings.tifa_model.parents[1] / "resources/UniDic/unidic"
                ),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    try:
        completed = subprocess.run(
            [
                str(settings.tifa_python),
                str(Path(__file__).with_name("reading_worker.py")),
                str(request),
            ],
            capture_output=True,
            timeout=60,
        )
        if completed.returncode or not output.exists():
            return {}, {"available": False, "reason": "reading_worker_failed"}
        result = json.loads(output.read_text(encoding="utf-8"))
        return result["readings"], {
            key: value for key, value in result.items() if key != "readings"
        }
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return {}, {"available": False, "reason": "reading_worker_unavailable"}


def locate(audio, lines, settings, work, profile=None, event=lambda message: None):
    folder = Path(work) / "locator"
    folder.mkdir(parents=True, exist_ok=True)
    for path in (
        settings.locator_python,
        settings.asr_model / "config.json",
    ):
        if not path.is_file():
            return (
                None,
                "未安装音频识别组件；正式流程需要独立 ASR 文本和 TIFA 词定位。",
            )
    # Cache independent audio recognition, including model configuration identity.
    languages = list(dict.fromkeys(l.language for l in lines))
    language = languages[0] if len(languages) == 1 else None
    runtime = probe_device(settings.locator_python, settings.device, event=event)
    timing_runtime = probe_device(settings.tifa_python, settings.device, event=event)
    timing_runtime["precision"] = "fp32"
    signature = {
        "audio": fingerprint(audio),
        "language": language,
        "device": settings.device,
        "runtime": runtime,
        "version": 5,
        "models": [
            (str(p.resolve()), (p / "config.json").stat().st_mtime_ns)
            for p in (settings.asr_model,)
        ],
    }
    signature["timing"] = {
        "runtime": timing_runtime,
        "model": str(settings.tifa_model.resolve()),
        "weight_identity": [
            settings.tifa_model.stat().st_size,
            settings.tifa_model.stat().st_mtime_ns,
        ],
        "config_sha256": fingerprint(settings.tifa_model.with_name("config.yaml")),
        "worker_sha256": fingerprint(Path(__file__).with_name("locator_worker.py")),
        "timing_sha256": fingerprint(Path(__file__).with_name("asr_timing.py")),
        "adapter_sha256": fingerprint(Path(__file__).with_name("tifa.py")),
        "local_g2p_sha256": fingerprint(settings.tifa_model.with_name("g2p.local.yaml"))
        if settings.tifa_model.with_name("g2p.local.yaml").exists()
        else None,
        "g2p": settings.g2p,
    }
    if profile:
        signature["profile"] = profile
        if profile == "asr_fine_blocks_v2":
            signature["version"] = 5
    key = hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()[
        :24
    ]
    cache = Path(audio).parent / "recognition_cache" / key
    cache.mkdir(parents=True, exist_ok=True)
    result_file = cache / "recognition.json"
    asr_file = cache / "recognition.asr.json"
    if not result_file.exists() and not asr_file.exists():
        req = {
            "audio": str(Path(audio).resolve()),
            "work": str(cache.resolve()),
            "asr_model": str(settings.asr_model),
            "language": language,
            **runtime,
            "cpu_threads": getattr(settings, "cpu_threads", 8),
        }
        if profile:
            req.update(chunk_seconds=30, overlap_seconds=5, verification_pass=True)
        if profile == "asr_fine_blocks_v2":
            req.update(chunk_seconds=12, overlap_seconds=2, verification_pass=True)
        request = cache / "request.json"
        request.write_text(json.dumps(req, ensure_ascii=False), encoding="utf-8")
        env = worker_environment(runtime["device"])
        try:
            with (cache / "infer.log").open("w", encoding="utf-8") as log:
                result = subprocess.run(
                    [
                        str(settings.locator_python),
                        str(Path(__file__).with_name("locator_worker.py")),
                        str(request),
                    ],
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    timeout=7200,
                )
            if result.returncode:
                message = (
                    json.loads((cache / "error.json").read_text(encoding="utf-8"))[
                        "message"
                    ]
                    if (cache / "error.json").exists()
                    else "详见定位日志"
                )
                return (
                    {},
                    "音频识别失败，请检查定位日志：" + message[:600],
                )
        except (OSError, subprocess.TimeoutExpired) as error:
            return {}, "音频识别未完成：" + str(error)[:600]
    if not result_file.exists():
        from .asr_timing import time_asr_chunks

        try:
            result = time_asr_chunks(
                audio,
                json.loads(asr_file.read_text(encoding="utf-8")),
                settings,
                cache / "asr_tifa",
            )
            result_file.write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired) as error:
            return {}, "ASR 文本的 TIFA 定位失败；请查看定位日志：" + str(error)[:600]
    result = json.loads(result_file.read_text(encoding="utf-8"))
    (folder / "recognition.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result, None
