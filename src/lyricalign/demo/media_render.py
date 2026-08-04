from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from .karaoke import ass_escape, ass_time

RENDER_SCHEMA_VERSION = "qwen_fa_media_render_v3_lower_band_no_meta"


@dataclass(frozen=True)
class VideoGeometry:
    width: int
    source_height: int
    canvas_height: int
    subtitle_band_height: int
    fps: float


@dataclass(frozen=True)
class SubtitleStyle:
    karaoke_primary: str = "&H0000E8FF"
    karaoke_secondary: str = "&H00909090"
    preview: str = "&H00A0A0A0"
    outline: str = "&H00101010"
    meta: str = "&H00D0D0D0"
    back: str = "&H00000000"


def normalize_ass_color(value: str) -> str:
    stripped = value.strip()
    if re.fullmatch(r"&H[0-9A-Fa-f]{6}", stripped):
        return "&H00" + stripped[2:].upper()
    if re.fullmatch(r"&H[0-9A-Fa-f]{8}", stripped):
        return "&H" + stripped[2:].upper()
    match = re.fullmatch(r"#?([0-9A-Fa-f]{6})", stripped)
    if match:
        rgb = match.group(1).upper()
        return f"&H00{rgb[4:6]}{rgb[2:4]}{rgb[0:2]}"
    raise ValueError(
        f"invalid ASS color {value!r}; use #RRGGBB, RRGGBB, &HBBGGRR, or &HAABBGGRR"
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_hash(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _register_matplotlib_font(font_path: Path) -> str | None:
    """Register one concrete, non-collection font face for Matplotlib."""
    try:
        from matplotlib import font_manager

        font_manager.fontManager.addfont(str(font_path))
        name = font_manager.FontProperties(fname=str(font_path)).get_name().strip()
        return name or None
    except (ImportError, OSError, RuntimeError, ValueError):
        return None


def _font_family_from_ttfont(font: Any) -> str | None:
    """Read the typographic/family name from a fontTools TTFont instance."""
    name_table = font.get("name")
    if name_table is None:
        return None
    for name_id in (16, 1):
        value = name_table.getDebugName(name_id)
        if value and value.strip():
            return value.strip()
    return None


def _extract_collection_face_for_matplotlib(
    collection_path: Path,
    *,
    face_index: int,
    expected_family: str,
) -> Path:
    """Extract the exact TTC/OTC face selected by fontconfig.

    Matplotlib's ``fontManager.addfont(path_to_ttc)`` registers only the first
    face of a collection in affected versions.  Debian's Noto CJK collection is
    ordered JP, KR, SC, TC, HK, so registering the TTC path without its
    fontconfig index silently turns an SC request into JP.  Matplotlib does not
    expose a collection-index argument; extracting only the selected face is
    therefore required.
    """
    try:
        import matplotlib
        from fontTools.ttLib import TTCollection
    except ImportError as exc:
        raise RuntimeError(
            "An indexed TTC/OTC font was selected, but fontTools is unavailable. "
            "Install the Matplotlib dependency 'fonttools'; do not substitute a different CJK region."
        ) from exc

    collection_path = collection_path.resolve()
    stat = collection_path.stat()
    cache_root = Path(matplotlib.get_cachedir()) / "lyricalign_font_faces"
    cache_root.mkdir(parents=True, exist_ok=True)
    safe_family = re.sub(r"[^A-Za-z0-9._-]+", "_", expected_family).strip("_") or "font"

    collection = TTCollection(str(collection_path), lazy=False)
    try:
        if face_index < 0 or face_index >= len(collection.fonts):
            raise RuntimeError(
                f"fontconfig selected invalid face index {face_index} for {collection_path}; "
                f"collection has {len(collection.fonts)} faces"
            )
        font = collection.fonts[face_index]
        actual_family = _font_family_from_ttfont(font)
        if actual_family and actual_family.casefold() != expected_family.casefold():
            raise RuntimeError(
                "fontconfig/TTC face mismatch: "
                f"requested face {face_index} should be {expected_family!r}, "
                f"but the collection contains {actual_family!r}"
            )
        suffix = ".otf" if getattr(font, "sfntVersion", None) == "OTTO" else ".ttf"
        output = cache_root / (
            f"{safe_family}.index{face_index}.size{stat.st_size}.mtime{stat.st_mtime_ns}{suffix}"
        )
        if output.is_file() and output.stat().st_size > 0:
            return output

        temporary = output.with_name(f".{output.name}.tmp.{os.getpid()}")
        try:
            font.save(str(temporary))
            if not temporary.is_file() or temporary.stat().st_size <= 0:
                raise RuntimeError(f"failed to extract font face {face_index} from {collection_path}")
            os.replace(temporary, output)
        finally:
            temporary.unlink(missing_ok=True)
        return output
    finally:
        collection.close()


def _fontconfig_match(preferred: str) -> tuple[Path, int, str, str]:
    """Return fontconfig's exact file, collection index, family, and style."""
    if not shutil.which("fc-match"):
        raise RuntimeError("fc-match is required to resolve named fonts")
    separator = "\x1f"
    result = subprocess.run(
        [
            "fc-match",
            "-f",
            f"%{{file}}{separator}%{{index}}{separator}%{{family}}{separator}%{{style}}\\n",
            preferred,
        ],
        check=True,
        text=True,
        capture_output=True,
    )
    first_line = next((line for line in result.stdout.splitlines() if line.strip()), "")
    fields = first_line.split(separator)
    if len(fields) != 4:
        raise RuntimeError(f"could not parse fc-match result for {preferred!r}: {first_line!r}")
    file_value, index_value, family_value, style_value = (field.strip() for field in fields)
    matched_file = Path(file_value).expanduser()
    if not matched_file.is_file():
        raise RuntimeError(f"fontconfig returned a missing font file for {preferred!r}: {matched_file}")
    try:
        face_index = int(index_value or "0")
    except ValueError as exc:
        raise RuntimeError(
            f"fontconfig returned an invalid collection index for {preferred!r}: {index_value!r}"
        ) from exc
    family = family_value.split(",", 1)[0].strip()
    style = style_value.split(",", 1)[0].strip()
    if not family:
        raise RuntimeError(f"fontconfig returned no family for {preferred!r}")
    return matched_file.resolve(), face_index, family, style


def _candidate_font_families(preferred: str) -> tuple[str, ...]:
    normalized = preferred.casefold()
    if "cjk sc" in normalized or "source han sans sc" in normalized:
        return (
            preferred,
            "Noto Sans CJK SC",
            "Source Han Sans SC",
            "Microsoft YaHei UI",
            "Microsoft YaHei",
            "DengXian",
            "SimHei",
            "PingFang SC",
            "Heiti SC",
            "Arial Unicode MS",
            "DejaVu Sans",
        )
    if "cjk jp" in normalized or "source han sans jp" in normalized:
        return (
            preferred,
            "Noto Sans CJK JP",
            "Source Han Sans JP",
            "Yu Gothic UI",
            "Yu Gothic",
            "Meiryo",
            "Hiragino Sans",
            "Arial Unicode MS",
            "DejaVu Sans",
        )
    return (
        preferred,
        "Noto Sans",
        "Arial",
        "Segoe UI",
        "Helvetica",
        "DejaVu Sans",
    )


def _matplotlib_find_family(family: str) -> str | None:
    try:
        from matplotlib import font_manager

        prop = font_manager.FontProperties(family=family)
        path = Path(font_manager.findfont(prop, fallback_to_default=False))
    except (ImportError, OSError, RuntimeError, ValueError):
        return None
    if not path.is_file():
        return None
    registered = _register_matplotlib_font(path)
    return registered or family


def _fallback_font_family(preferred: str, *, reason: Exception) -> str:
    for family in _candidate_font_families(preferred):
        resolved = _matplotlib_find_family(family)
        if resolved:
            print(
                json.dumps(
                    {
                        "warning": "subtitle_font_fallback",
                        "requested": preferred,
                        "selected": family,
                        "registered": resolved,
                        "reason": f"{type(reason).__name__}: {reason}",
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                flush=True,
            )
            return family
    if os.name == "nt":
        selected = "Microsoft YaHei"
    else:
        selected = "DejaVu Sans"
    print(
        json.dumps(
            {
                "warning": "subtitle_font_unverified_fallback",
                "requested": preferred,
                "selected": selected,
                "reason": f"{type(reason).__name__}: {reason}",
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        flush=True,
    )
    return selected


def detect_font(preferred: str) -> str:
    """Resolve and register the exact requested font face.

    For an SC request this function must return and register an SC face.  It
    never treats JP as an acceptable substitute.  With Debian's
    ``NotoSansCJK-Regular.ttc``, fontconfig selects index 2 for SC; that face is
    extracted to Matplotlib's cache and registered as a standalone OpenType
    font, while the returned family remains ``Noto Sans CJK SC`` for libass.
    """
    requested_path = Path(preferred).expanduser()
    if requested_path.is_file():
        if requested_path.suffix.casefold() in {".ttc", ".otc"}:
            raise ValueError(
                "A TTC/OTC path is ambiguous because it contains multiple regional faces. "
                "Pass the exact family name, for example 'Noto Sans CJK SC'."
            )
        registered = _register_matplotlib_font(requested_path.resolve())
        if not registered:
            raise RuntimeError(f"Matplotlib could not register font file: {requested_path}")
        return registered

    try:
        matched_file, face_index, family, _style = _fontconfig_match(preferred)
    except RuntimeError as exc:
        return _fallback_font_family(preferred, reason=exc)
    if "cjk sc" in preferred.casefold() and family.casefold() != preferred.casefold():
        raise RuntimeError(
            f"fontconfig did not resolve the requested Simplified Chinese face: "
            f"requested={preferred!r}, matched={family!r}"
        )

    registration_path = matched_file
    if matched_file.suffix.casefold() in {".ttc", ".otc"}:
        registration_path = _extract_collection_face_for_matplotlib(
            matched_file,
            face_index=face_index,
            expected_family=family,
        )
    registered = _register_matplotlib_font(registration_path)
    if not registered:
        raise RuntimeError(
            f"Matplotlib could not register the resolved font face: "
            f"family={family!r}, file={matched_file}, index={face_index}"
        )
    if registered.casefold() != family.casefold():
        raise RuntimeError(
            "Matplotlib registered a different font face than fontconfig selected: "
            f"fontconfig={family!r}, matplotlib={registered!r}, "
            f"file={matched_file}, index={face_index}"
        )
    return family

def _parse_rate(value: str) -> float:
    if "/" in value:
        numerator, denominator = value.split("/", 1)
        denominator_value = float(denominator)
        return float(numerator) / denominator_value if denominator_value else 30.0
    return float(value)


def probe_video(path: Path, *, subtitle_band_height: int | None = None) -> VideoGeometry:
    command = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,avg_frame_rate",
        "-of", "json", str(path),
    ]
    payload = json.loads(subprocess.run(command, check=True, text=True, capture_output=True).stdout)
    streams = payload.get("streams", [])
    if not streams:
        raise RuntimeError(f"no video stream found: {path}")
    stream = streams[0]
    width = int(stream["width"])
    height = int(stream["height"])
    if width <= 0 or height <= 0:
        raise RuntimeError(f"invalid video geometry: {width}x{height}")
    requested_band = subtitle_band_height or max(160, int(round(height * 0.34)))
    band = min(height, max(96, int(requested_band)))
    # H.264/yuv420p requires even dimensions.
    width += width % 2
    height += height % 2
    band += band % 2
    return VideoGeometry(
        width=width,
        source_height=max(0, height - band),
        canvas_height=height,
        subtitle_band_height=band,
        fps=max(1.0, _parse_rate(str(stream.get("avg_frame_rate", "30/1")))),
    )


def audio_geometry(*, width: int = 1280, height: int = 720) -> VideoGeometry:
    width += width % 2
    height += height % 2
    band = max(160, int(round(height * 0.34)))
    band = min(height, max(96, band))
    band += band % 2
    return VideoGeometry(
        width=width,
        source_height=max(0, height - band),
        canvas_height=height,
        subtitle_band_height=band,
        fps=30.0,
    )


def build_bottom_ass(
    alignment: dict[str, Any],
    *,
    label: str,
    font: str,
    geometry: VideoGeometry,
    style: SubtitleStyle | None = None,
) -> str:
    """Build two non-overlapping outlined karaoke rows over the lower image area."""

    style = style or SubtitleStyle()
    width = geometry.width
    height = geometry.canvas_height
    band = min(geometry.subtitle_band_height, height)
    band_top = max(0, height - band)
    rows_y = [
        int(band_top + band * 0.38),
        int(band_top + band * 0.78),
    ]
    max_font = max(28, min(58, int(band * 0.20)))
    outline = max(2, int(round(max_font / 18)))
    characters = alignment["characters"]
    line_specs = alignment["lines"]
    by_line: dict[int, list[dict[str, Any]]] = {}
    for row in characters:
        by_line.setdefault(int(row["line_index"]), []).append(row)
    duration = float(alignment["summary"]["audio_duration_sec"])

    events: list[str] = []

    starts: list[float] = []
    ends: list[float] = []
    ordered_lines: list[list[dict[str, Any]]] = []
    for line in line_specs:
        rows = sorted(
            by_line.get(int(line["line_index"]), []),
            key=lambda row: int(row["index_in_line"]),
        )
        ordered_lines.append(rows)
        if rows:
            starts.append(float(rows[0]["start_sec"]))
            ends.append(float(rows[-1]["end_sec"]))
        else:
            starts.append(duration)
            ends.append(duration)

    for index, rows in enumerate(ordered_lines):
        if not rows:
            continue
        line_start = max(0.0, starts[index])
        line_end = max(line_start + 0.01, ends[index])
        next_start = starts[index + 1] if index + 1 < len(starts) else duration
        active_end = max(line_end, min(duration, next_start))
        preview_start = starts[index - 1] if index > 0 else 0.0
        preview_end = line_start
        y = rows_y[index % 2]
        display_text = "".join(
            str(row.get("display_prefix", ""))
            + str(row.get("display_text") or row.get("alignment_unit") or row["character"])
            + str(row.get("display_suffix", ""))
            for row in rows
        )
        visual_units = sum(0.5 if character.isspace() else 1.0 for character in display_text)
        line_font_size = min(max_font, max(24, int((width - 96) / max(visual_units, 1.0))))

        if preview_end - preview_start > 0.01:
            events.append(
                f"Dialogue: 0,{ass_time(preview_start)},{ass_time(preview_end)},Preview,,0,0,0,,"
                f"{{\\an5\\pos({width // 2},{y})\\fs{line_font_size}}}{ass_escape(display_text)}"
            )

        karaoke_parts: list[str] = []
        for char_index, row in enumerate(rows):
            current_start = float(row["start_sec"])
            if char_index + 1 < len(rows):
                next_char_start = float(rows[char_index + 1]["start_sec"])
                duration_cs = max(1, int(round(max(next_char_start - current_start, 0.01) * 100)))
            else:
                duration_cs = max(
                    1,
                    int(round(max(float(row["end_sec"]) - current_start, 0.01) * 100)),
                )
            visible = ass_escape(
                str(row.get("display_prefix", ""))
                + str(row.get("display_text") or row.get("alignment_unit") or row["character"])
                + str(row.get("display_suffix", ""))
            )
            karaoke_parts.append(f"{{\\kf{duration_cs}}}{visible}")
        events.append(
            f"Dialogue: 1,{ass_time(line_start)},{ass_time(active_end)},Karaoke,,0,0,0,,"
            f"{{\\an5\\pos({width // 2},{y})\\fs{line_font_size}}}{''.join(karaoke_parts)}"
        )

    return "\n".join(
        [
            "[Script Info]",
            "ScriptType: v4.00+",
            f"PlayResX: {width}",
            f"PlayResY: {height}",
            "ScaledBorderAndShadow: yes",
            "WrapStyle: 2",
            "",
            "[V4+ Styles]",
            "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,"
            "Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,"
            "Alignment,MarginL,MarginR,MarginV,Encoding",
            f"Style: Preview,{font},{max_font},{style.preview},{style.preview},{style.outline},{style.back},"
            f"0,0,0,0,100,100,2,0,1,{outline},0,2,40,40,20,1",
            f"Style: Karaoke,{font},{max_font},{style.karaoke_primary},{style.karaoke_secondary},{style.outline},{style.back},"
            f"1,0,0,0,100,100,2,0,1,{outline},0,2,40,40,20,1",
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
            *events,
            "",
        ]
    )


def _output_is_current(output: Path, identity: Path, request_hash: str) -> bool:
    if not output.is_file() or not identity.is_file():
        return False
    try:
        payload = json.loads(identity.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return payload.get("request_hash") == request_hash


def _run(command: list[str], *, cwd: Path | None = None) -> None:
    print(json.dumps({"ffmpeg": command}, ensure_ascii=False), flush=True)
    subprocess.run(command, check=True, cwd=cwd)


def render_media_video(
    *,
    alignment_path: Path,
    visual_source: Path | None,
    audio_track: Path,
    output_path: Path,
    ass_path: Path,
    label: str,
    font: str,
    force: bool = False,
    subtitle_band_height: int | None = None,
    audio_width: int = 1280,
    audio_height: int = 720,
    background_image: Path | None = None,
    profile: str = "final",
    subtitle_style: SubtitleStyle | None = None,
) -> dict[str, Any]:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError("ffmpeg and ffprobe are required")
    alignment = json.loads(alignment_path.read_text(encoding="utf-8"))
    settings = {
        "review": {"fps": 24, "preset": "veryfast", "crf": 28, "audio_bitrate": "96k"},
        "final": {"fps": 30, "preset": "veryfast", "crf": 20, "audio_bitrate": "192k"},
    }.get(profile)
    if settings is None:
        raise ValueError(f"unsupported render profile: {profile}")
    geometry = (
        probe_video(visual_source, subtitle_band_height=subtitle_band_height)
        if visual_source is not None
        else audio_geometry(width=audio_width, height=audio_height)
    )
    request = {
        "schema_version": RENDER_SCHEMA_VERSION,
        "alignment_request_hash": alignment["identity"]["request_hash"],
        "visual_source": str(visual_source.resolve()) if visual_source else None,
        "visual_source_sha256": sha256(visual_source) if visual_source else None,
        "audio_track": str(audio_track.resolve()),
        "audio_track_sha256": sha256(audio_track),
        "background_image": str(background_image.resolve()) if background_image else None,
        "background_image_sha256": sha256(background_image) if background_image else None,
        "label": label,
        "font": font,
        "geometry": geometry.__dict__,
        "profile": profile,
        "encoding": settings,
        "subtitle_style": (subtitle_style or SubtitleStyle()).__dict__,
    }
    request_hash = canonical_hash(request)
    identity_path = output_path.with_suffix(output_path.suffix + ".identity.json")
    if not force and _output_is_current(output_path, identity_path, request_hash):
        return {"path": str(output_path), "request_hash": request_hash, "skipped": True}

    ass_path.parent.mkdir(parents=True, exist_ok=True)
    ass_path.write_text(
        build_bottom_ass(
            alignment,
            label=label,
            font=font,
            geometry=geometry,
            style=subtitle_style,
        ),
        encoding="utf-8",
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(".tmp.mp4")
    duration = float(alignment["summary"]["audio_duration_sec"])

    if visual_source is None:
        if background_image is None:
            command = [
                "ffmpeg", "-nostdin", "-y", "-v", "warning",
                "-f", "lavfi", "-i",
                f"color=c=0x14191f:s={geometry.width}x{geometry.canvas_height}:r={settings['fps']}:d={duration:.6f}",
                "-i", str(audio_track),
                "-vf", f"ass={ass_path.name}",
                "-map", "0:v:0", "-map", "1:a:0",
            ]
        else:
            filter_graph = (
                f"[0:v]scale={geometry.width}:{geometry.canvas_height}:force_original_aspect_ratio=increase,"
                f"crop={geometry.width}:{geometry.canvas_height},setsar=1,ass={ass_path.name}[v]"
            )
            command = [
                "ffmpeg", "-nostdin", "-y", "-v", "warning",
                "-loop", "1", "-framerate", str(settings["fps"]), "-i", str(background_image),
                "-i", str(audio_track),
                "-filter_complex", filter_graph,
                "-map", "[v]", "-map", "1:a:0",
            ]
    else:
        filter_graph = (
            f"[0:v]scale={geometry.width}:{geometry.canvas_height}:force_original_aspect_ratio=decrease,"
            f"pad={geometry.width}:{geometry.canvas_height}:(ow-iw)/2:(oh-ih)/2:black,"
            f"setsar=1,ass={ass_path.name}[v]"
        )
        command = [
            "ffmpeg", "-nostdin", "-y", "-v", "warning",
            "-i", str(visual_source), "-i", str(audio_track),
            "-filter_complex", filter_graph,
            "-map", "[v]", "-map", "1:a:0",
        ]
    command.extend(
        [
            "-r", str(settings["fps"]),
            "-c:v", "libx264", "-preset", str(settings["preset"]), "-crf", str(settings["crf"]), "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", str(settings["audio_bitrate"]), "-shortest", "-movflags", "+faststart",
            str(temporary),
        ]
    )
    _run(command, cwd=ass_path.parent)
    temporary.replace(output_path)
    atomic_json(identity_path, {**request, "request_hash": request_hash})
    return {"path": str(output_path), "request_hash": request_hash, "skipped": False}


def render_composite(
    *,
    sources: Sequence[Path],
    source_hashes: Sequence[str],
    output_path: Path,
    layout: str,
    force: bool = False,
) -> dict[str, Any]:
    if layout not in ("two", "three", "four"):
        raise ValueError(layout)
    expected = {"two": 2, "three": 3, "four": 4}[layout]
    if len(sources) != expected or len(source_hashes) != expected:
        raise ValueError(f"{layout} composite requires {expected} sources")
    request = {
        "schema_version": RENDER_SCHEMA_VERSION,
        "source_request_hashes": list(source_hashes),
        "layout": layout,
    }
    request_hash = canonical_hash(request)
    identity_path = output_path.with_suffix(output_path.suffix + ".identity.json")
    if not force and _output_is_current(output_path, identity_path, request_hash):
        return {"path": str(output_path), "request_hash": request_hash, "skipped": True}

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(".tmp.mp4")
    command = ["ffmpeg", "-nostdin", "-y", "-v", "warning"]
    for source in sources:
        command.extend(["-i", str(source)])
    if layout == "two":
        filters = (
            "[0:v]scale=960:540:force_original_aspect_ratio=decrease,pad=960:540:(ow-iw)/2:(oh-ih)/2:black[v0];"
            "[1:v]scale=960:540:force_original_aspect_ratio=decrease,pad=960:540:(ow-iw)/2:(oh-ih)/2:black[v1];"
            "[v0][v1]hstack=inputs=2[v]"
        )
    elif layout == "three":
        filters = (
            "[0:v]scale=960:540:force_original_aspect_ratio=decrease,pad=960:540:(ow-iw)/2:(oh-ih)/2:black[v0];"
            "[1:v]scale=960:540:force_original_aspect_ratio=decrease,pad=960:540:(ow-iw)/2:(oh-ih)/2:black[v1];"
            "[2:v]scale=960:540:force_original_aspect_ratio=decrease,pad=960:540:(ow-iw)/2:(oh-ih)/2:black[v2];"
            "[v0][v1][v2]xstack=inputs=3:layout=0_0|w0_0|0_h0:fill=black[v]"
        )
    else:
        filters = (
            "[0:v]scale=960:540:force_original_aspect_ratio=decrease,pad=960:540:(ow-iw)/2:(oh-ih)/2:black[v0];"
            "[1:v]scale=960:540:force_original_aspect_ratio=decrease,pad=960:540:(ow-iw)/2:(oh-ih)/2:black[v1];"
            "[2:v]scale=960:540:force_original_aspect_ratio=decrease,pad=960:540:(ow-iw)/2:(oh-ih)/2:black[v2];"
            "[3:v]scale=960:540:force_original_aspect_ratio=decrease,pad=960:540:(ow-iw)/2:(oh-ih)/2:black[v3];"
            "[v0][v1][v2][v3]xstack=inputs=4:layout=0_0|w0_0|0_h0|w0_h0:fill=black[v]"
        )
    command.extend(
        [
            "-filter_complex", filters,
            "-map", "[v]", "-map", "0:a:0?",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart",
            str(temporary),
        ]
    )
    _run(command)
    temporary.replace(output_path)
    atomic_json(identity_path, {**request, "request_hash": request_hash})
    return {"path": str(output_path), "request_hash": request_hash, "skipped": False}


def render_alignment_comparison(
    *,
    alignment_paths: Sequence[Path],
    labels: Sequence[str],
    visual_source: Path | None,
    audio_track: Path,
    output_path: Path,
    ass_root: Path,
    font: str,
    layout: str,
    profile: str = "review",
    force: bool = False,
    subtitle_style: SubtitleStyle | None = None,
) -> dict[str, Any]:
    """Render 2 or 4 alignments directly in one ffmpeg encoding pass.

    Unlike render_media_video + render_composite, this decodes the source once,
    applies each ASS subtitle stream inside one filter graph, and encodes only
    the final comparison. No intermediate panel videos or duplicate audio
    encodes are created.
    """
    expected = {"two": 2, "four": 4}.get(layout)
    if expected is None:
        raise ValueError(f"unsupported direct comparison layout: {layout}")
    if len(alignment_paths) != expected or len(labels) != expected:
        raise ValueError(f"{layout} comparison requires {expected} alignments and labels")
    if profile not in {"review", "final"}:
        raise ValueError(f"unsupported render profile: {profile}")
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg is required")

    settings = {
        "review": {
            "panel_width": 640, "panel_height": 360, "fps": 24,
            "preset": "veryfast", "crf": 28, "audio_bitrate": "96k",
        },
        "final": {
            "panel_width": 960, "panel_height": 540, "fps": 30,
            "preset": "veryfast", "crf": 22, "audio_bitrate": "160k",
        },
    }[profile]
    panel_width = int(settings["panel_width"])
    panel_height = int(settings["panel_height"])
    band_height = max(120, int(round(panel_height * 0.34)))
    band_height += band_height % 2
    source_height = panel_height - band_height
    source_height -= source_height % 2
    geometry = VideoGeometry(
        width=panel_width,
        source_height=source_height,
        canvas_height=panel_height,
        subtitle_band_height=panel_height - source_height,
        fps=float(settings["fps"]),
    )

    alignments = [json.loads(path.read_text(encoding="utf-8")) for path in alignment_paths]
    duration = max(float(payload["summary"]["audio_duration_sec"]) for payload in alignments)
    alignment_identities = [
        {
            "path": str(path.resolve()),
            "content_sha256": sha256(path),
            "alignment_request_hash": payload.get("identity", {}).get("request_hash"),
            "label": label,
        }
        for path, payload, label in zip(alignment_paths, alignments, labels)
    ]
    request = {
        "schema_version": RENDER_SCHEMA_VERSION + "_direct_comparison_v1",
        "alignments": alignment_identities,
        "visual_source": str(visual_source.resolve()) if visual_source else None,
        "visual_source_sha256": sha256(visual_source) if visual_source else None,
        "audio_track": str(audio_track.resolve()),
        "audio_track_sha256": sha256(audio_track),
        "layout": layout,
        "profile": profile,
        "font": font,
        "geometry": geometry.__dict__,
        "settings": settings,
        "subtitle_style": (subtitle_style or SubtitleStyle()).__dict__,
    }
    request_hash = canonical_hash(request)
    identity_path = output_path.with_suffix(output_path.suffix + ".identity.json")
    if not force and _output_is_current(output_path, identity_path, request_hash):
        return {
            "path": str(output_path), "request_hash": request_hash,
            "skipped": True, "encoding_passes": 0,
        }

    ass_root.mkdir(parents=True, exist_ok=True)
    ass_names: list[str] = []
    for index, (payload, label) in enumerate(zip(alignments, labels)):
        ass_path = ass_root / f"panel_{index}.ass"
        ass_path.write_text(
            build_bottom_ass(
                payload,
                label=label,
                font=font,
                geometry=geometry,
                style=subtitle_style,
            ),
            encoding="utf-8",
        )
        ass_names.append(ass_path.name)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(".tmp.mp4")
    fps = int(settings["fps"])
    command = ["ffmpeg", "-nostdin", "-y", "-v", "warning"]
    if visual_source is None:
        command.extend([
            "-f", "lavfi", "-i",
            f"color=c=black:s={panel_width}x{panel_height}:r={fps}:d={duration:.6f}",
            "-i", str(audio_track),
        ])
        base_filter = f"[0:v]fps={fps},format=yuv420p[base]"
    else:
        command.extend(["-i", str(visual_source), "-i", str(audio_track)])
        base_filter = (
            f"[0:v]fps={fps},scale={panel_width}:{source_height}:force_original_aspect_ratio=decrease,"
            f"pad={panel_width}:{source_height}:(ow-iw)/2:(oh-ih)/2:black,"
            f"pad={panel_width}:{panel_height}:0:0:black,format=yuv420p[base]"
        )
    split_outputs = "".join(f"[b{index}]" for index in range(expected))
    filters = [base_filter, f"[base]split={expected}{split_outputs}"]
    for index, ass_name in enumerate(ass_names):
        filters.append(f"[b{index}]ass={ass_name}[v{index}]")
    if layout == "two":
        filters.append("[v0][v1]hstack=inputs=2[v]")
    else:
        filters.append("[v0][v1][v2][v3]xstack=inputs=4:layout=0_0|w0_0|0_h0|w0_h0:fill=black[v]")
    command.extend([
        "-filter_complex", ";".join(filters),
        "-map", "[v]", "-map", "1:a:0",
        "-c:v", "libx264", "-preset", str(settings["preset"]),
        "-crf", str(settings["crf"]), "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", str(settings["audio_bitrate"]),
        "-shortest", "-movflags", "+faststart", str(temporary),
    ])
    _run(command, cwd=ass_root)
    temporary.replace(output_path)
    atomic_json(identity_path, {**request, "request_hash": request_hash})
    return {
        "path": str(output_path), "request_hash": request_hash,
        "skipped": False, "encoding_passes": 1,
    }


def link_or_copy(source: Path, destination: Path) -> str:
    """Create a no-duplicate entry point when possible, with a portable fallback."""
    import os

    source = source.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.unlink(missing_ok=True)
    try:
        os.link(source, destination)
        return "hardlink"
    except OSError:
        pass
    try:
        destination.symlink_to(Path(os.path.relpath(source, destination.parent)))
        return "symlink"
    except OSError:
        shutil.copy2(source, destination)
        return "copy_fallback"
