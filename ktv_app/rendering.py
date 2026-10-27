from pathlib import Path
import re
import unicodedata
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .media import run


class RenderOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    width: int | None = Field(default=None, ge=160, le=7680)
    height: int | None = Field(default=None, ge=120, le=4320)
    crf: int = Field(default=20, ge=0, le=40)
    preset: str = Field(default="medium", pattern=r"^(ultrafast|fast|medium|slow)$")
    output_dir: str = ""

    @model_validator(mode="after")
    def even_dimensions(self):
        if bool(self.width) != bool(self.height) or any(
            v is not None and v % 2 for v in (self.width, self.height)
        ):
            raise ValueError("分辨率宽高必须同时提供且为偶数")
        return self


def video_filename(song, artist, video):
    title = str(song or "").strip() or Path(video).stem or "歌曲"
    name = title + (" - " + str(artist).strip() if str(artist or "").strip() else "")
    name = unicodedata.normalize("NFC", name)
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    name = "".join(c for c in name if not unicodedata.category(c).startswith("C"))
    name = re.sub(r"\s+", " ", name).strip(" .")[:140].rstrip(" .") or "歌曲"
    return name + "_KTV.mp4"


def render(video, bundle, info, settings, options, output_name=None):
    folder = Path(bundle).resolve()
    target = folder / (output_name or video_filename("", "", video))
    if target.parent != folder or target.suffix.lower() != ".mp4":
        raise ValueError("成片文件名无效")
    cmd = [settings.tool("ffmpeg"), "-nostdin", "-v", "warning", "-y", "-i", video]
    filters = ["subtitles=filename='lyrics.ass'"]
    if options.width:
        filters.append(
            f"scale={options.width}:{options.height}:force_original_aspect_ratio=decrease"
        )
        filters.append(f"pad={options.width}:{options.height}:(ow-iw)/2:(oh-ih)/2")
    else:
        filters.append("pad=ceil(iw/2)*2:ceil(ih/2)*2")
    cmd += [
        "-vf",
        ",".join(filters),
        "-map",
        "0:v:0",
        "-map",
        "0:a",
        "-map_metadata",
        "0",
    ]
    codecs = [
        s["codec_name"] for s in info["raw"]["streams"] if s["codec_type"] == "audio"
    ]
    for i, codec in enumerate(codecs):
        cmd += [
            f"-c:a:{i}",
            "copy" if codec in ("aac", "mp3", "ac3", "eac3", "alac") else "aac",
        ]
    cmd += [
        "-c:v",
        "libx264",
        "-crf",
        str(options.crf),
        "-preset",
        options.preset,
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        target,
    ]
    run(cmd, log=folder / "render.log", cwd=folder)
    return target
