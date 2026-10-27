import hashlib
import json
import os
import subprocess
from pathlib import Path


def run(command, timeout=1800, log=None, cwd=None):
    result = subprocess.run(
        [str(x) for x in command],
        cwd=cwd,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if log:
        Path(log).write_text(result.stdout + "\n" + result.stderr, encoding="utf-8")
    if result.returncode:
        raise RuntimeError(
            result.stderr[-5000:]
            or result.stdout[-3000:]
            or f"进程失败: {result.returncode}"
        )
    return result.stdout


def fingerprint(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def probe(path, settings):
    raw = json.loads(
        run(
            [
                settings.tool("ffprobe"),
                "-v",
                "error",
                "-show_format",
                "-show_streams",
                "-of",
                "json",
                path,
            ]
        )
    )
    video = next(
        (
            s
            for s in raw["streams"]
            if s["codec_type"] == "video"
            and not s.get("disposition", {}).get("attached_pic")
        ),
        None,
    )
    audio = next((s for s in raw["streams"] if s["codec_type"] == "audio"), None)
    if not video or not audio:
        raise ValueError("输入必须包含视频画面和音轨")
    base = float(raw["format"].get("start_time", 0))
    return {
        "duration": float(raw["format"]["duration"]),
        "width": video["width"],
        "height": video["height"],
        "fps": video.get("avg_frame_rate", "0/1"),
        "format_start": base,
        "video_offset": float(video.get("start_time", base)) - base,
        "audio_offset": float(audio.get("start_time", base)) - base,
        "audio_codec": audio["codec_name"],
        "raw": raw,
    }


def extract(video, folder, settings):
    folder = Path(folder)
    info = probe(video, settings)
    key = fingerprint(video)
    cache = folder / "media.json"
    wav = folder / "audio.wav"
    if (
        cache.exists()
        and wav.exists()
        and json.loads(cache.read_text(encoding="utf-8")).get("sha256") == key
    ):
        return json.loads(cache.read_text(encoding="utf-8"))
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
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            wav,
        ],
        log=folder / "extract.log",
    )
    import numpy as np
    import soundfile as sf

    samples, sr = sf.read(wav)
    blocks = np.array_split(samples, min(1800, len(samples)))
    peaks = [round(float(np.max(np.abs(b))), 4) for b in blocks]
    waveform = folder / "waveform.json"
    temporary = waveform.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(
            {
                "peaks": peaks,
                "duration": len(samples) / sr,
                "origin": info["audio_offset"],
            }
        ),
        encoding="utf-8",
    )
    os.replace(temporary, waveform)
    info.update(sha256=key, audio_duration=len(samples) / sr)
    temporary = cache.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(temporary, cache)
    return info


def crop(wav, out, start, end, settings):
    run(
        [
            settings.tool("ffmpeg"),
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-i",
            wav,
            "-ss",
            str(start),
            "-t",
            str(end - start),
            "-c:a",
            "pcm_s16le",
            out,
        ]
    )


def browser_proxy(video, out, settings):
    """Browser-compatible view only; never used for alignment or final export."""
    out = Path(out)
    run(
        [
            settings.tool("ffmpeg"),
            "-nostdin",
            "-v",
            "warning",
            "-y",
            "-i",
            video,
            "-map",
            "0:v:0",
            "-map",
            "0:a:0",
            "-vf",
            "scale=w='min(960,iw)':h=-2",
            "-c:v",
            "libvpx-vp9",
            "-crf",
            "34",
            "-b:v",
            "0",
            "-deadline",
            "realtime",
            "-cpu-used",
            "6",
            "-c:a",
            "libopus",
            "-b:a",
            "128k",
            out,
        ],
        log=out.with_suffix(".log"),
    )
    return out
