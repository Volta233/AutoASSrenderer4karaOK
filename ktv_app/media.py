import hashlib
import json
import os
import subprocess
from pathlib import Path

VIDEO_SUFFIXES = (".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v")
AUDIO_SUFFIXES = (".mp3", ".m4a", ".wav", ".flac", ".aac", ".ogg", ".opus")
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".bmp")

# A still background image is encoded at a fixed cadence so downstream stages
# see a normal constant-frame-rate video; the picture never changes between frames.
STILL_FPS = 25
STILL_MAX_EDGE = 1920
STILL_MIN_EDGE = 360


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


def audio_media(audio, settings):
    """Duration and codec of a bare audio file; embedded cover art is ignored."""
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
                audio,
            ]
        )
    )
    stream = next((s for s in raw["streams"] if s["codec_type"] == "audio"), None)
    if not stream:
        raise ValueError("音频文件没有可用的音轨")
    duration = float(raw["format"].get("duration") or 0)
    if duration <= 0:
        raise ValueError("音频文件时长为空")
    return {"duration": duration, "codec": stream["codec_name"]}


def image_size(image, settings):
    raw = json.loads(
        run(
            [
                settings.tool("ffprobe"),
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height",
                "-of",
                "json",
                image,
            ]
        )
    )
    stream = next((s for s in raw["streams"] if s.get("width")), None)
    if not stream:
        raise ValueError("无法读取背景图尺寸，请换用 JPG/PNG")
    return int(stream["width"]), int(stream["height"])


def even(value):
    return max(2, int(value) // 2 * 2)


def still_canvas(width, height):
    """Keep the picture's aspect ratio, capped on the long edge and never too small."""
    long_edge, short_edge = max(width, height), min(width, height)
    scale = min(1.0, STILL_MAX_EDGE / long_edge)
    if short_edge * scale < STILL_MIN_EDGE:
        scale = STILL_MIN_EDGE / short_edge
    return even(round(width * scale)), even(round(height * scale))


def still_video(audio, image, out, settings, width=None, height=None):
    """Compose a background image plus an audio file into an ordinary MP4.

    The result carries the audio track re-encoded to AAC and a static picture, so
    probing, alignment, playback and export need no special cases.
    """
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    duration = audio_media(audio, settings)["duration"]
    if width and height:
        canvas = (even(width), even(height))
    else:
        canvas = still_canvas(*image_size(image, settings))
    run(
        [
            settings.tool("ffmpeg"),
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-loop",
            "1",
            "-framerate",
            str(STILL_FPS),
            "-i",
            image,
            "-i",
            audio,
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-t",
            f"{duration:.3f}",
            "-vf",
            (
                f"scale={canvas[0]}:{canvas[1]}:"
                "force_original_aspect_ratio=increase:flags=lanczos,"
                f"crop={canvas[0]}:{canvas[1]},setsar=1"
            ),
            "-r",
            str(STILL_FPS),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-tune",
            "stillimage",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            out,
        ],
        log=out.with_suffix(".log"),
    )
    return out


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
