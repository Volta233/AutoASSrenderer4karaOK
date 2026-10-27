import os
from pathlib import Path

import pytest

from ktv_app.config import Settings
from ktv_app.media import run, probe, extract
from ktv_app.timeline import Timeline, Line, Unit, Style, quality
from ktv_app.subtitles import write_bundle
from ktv_app.rendering import render, RenderOptions

pytestmark = pytest.mark.skipif(
    os.getenv("KTV_LIVE_TESTS") != "1", reason="需要真实 FFmpeg"
)


def test_delayed_original_audio_survives_full_export(tmp_path):
    s = Settings()
    video = tmp_path / "offset input 空格.mkv"
    run(
        [
            s.tool("ffmpeg"),
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=640x360:r=25:d=6",
            "-itsoffset",
            "1.5",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=700:sample_rate=48000:duration=4.5",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            video,
        ]
    )
    info = extract(video, tmp_path, s)
    assert info["audio_offset"] == pytest.approx(1.48, abs=0.05)
    assert info["audio_duration"] < 4.6
    t = quality(
        Timeline(
            duration=info["duration"],
            origin=info["audio_offset"],
            lines=[
                Line(
                    id="l",
                    text="TEST",
                    units=[
                        Unit(
                            id="u",
                            text="TEST",
                            source_start=0,
                            source_end=4,
                            start=2,
                            end=3,
                        )
                    ],
                )
            ],
        )
    )
    bundle = tmp_path / "render 空格"
    write_bundle(t, Style(font="Arial"), bundle)
    full = render(
        video, bundle, info, s, RenderOptions(preset="ultrafast"), "歌曲 - 歌手_KTV.mp4"
    )
    assert full.name == "歌曲 - 歌手_KTV.mp4"
    assert probe(full, s)["audio_offset"] == pytest.approx(
        info["audio_offset"], abs=0.05
    )
    # PCM cannot be copied into MP4: original samples are encoded, never replaced with model input.
    pcm = tmp_path / "pcm.mkv"
    run(
        [
            s.tool("ffmpeg"),
            "-v",
            "error",
            "-y",
            "-i",
            video,
            "-c:v",
            "copy",
            "-c:a",
            "pcm_s16le",
            pcm,
        ]
    )
    result = render(pcm, bundle, probe(pcm, s), s, RenderOptions(preset="ultrafast"))
    assert probe(result, s)["audio_codec"] == "aac"
