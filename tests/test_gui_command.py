from __future__ import annotations

from pathlib import Path

from lyricalign.gui.command import (
    DEFAULT_R2_CHECKPOINT_PATH,
    GuiRunConfig,
    build_batch_command,
    build_child_environment,
)


def test_gui_default_r2_checkpoint_points_to_packaged_checkpoint() -> None:
    assert DEFAULT_R2_CHECKPOINT_PATH.name == "R2checkpoint"
    assert DEFAULT_R2_CHECKPOINT_PATH.parent.name == "models"


def test_gui_command_passes_explicit_lyrics_and_demucs_cache(tmp_path: Path) -> None:
    config = GuiRunConfig(
        media=tmp_path / "song.mp4",
        lyrics=tmp_path / "lyrics.txt",
        python_executable=tmp_path / "python.exe",
        output_dir=tmp_path / "out",
        demucs_torch_home=tmp_path / "torch",
        r2_checkpoint=tmp_path / "r2",
        font="Microsoft YaHei",
        subtitle_band_height=280,
        karaoke_primary_color="#00FFFF",
        karaoke_secondary_color="#888888",
        subtitle_preview_color="#AAAAAA",
        subtitle_outline_color="#111111",
        audio_width=1920,
        audio_height=1080,
        background_image=tmp_path / "cover.png",
        dry_run=True,
    )

    command = build_batch_command(config)

    assert command[0] == str(tmp_path / "python.exe")
    assert "--lyrics" in command
    assert str(config.lyrics) in command
    assert "--demucs-torch-home" in command
    assert str(config.demucs_torch_home) in command
    assert "--r2-checkpoint" in command
    assert str(config.r2_checkpoint) in command
    assert "--font" in command
    assert "Microsoft YaHei" in command
    assert "--subtitle-band-height" in command
    assert "280" in command
    assert "--karaoke-primary-color" in command
    assert "#00FFFF" in command
    assert "--karaoke-secondary-color" in command
    assert "#888888" in command
    assert "--subtitle-preview-color" in command
    assert "#AAAAAA" in command
    assert "--subtitle-outline-color" in command
    assert "#111111" in command
    assert "--audio-width" in command
    assert "1920" in command
    assert "--audio-height" in command
    assert "1080" in command
    assert "--background-image" in command
    assert str(tmp_path / "cover.png") in command
    assert "--dry-run" in command


def test_gui_environment_prepends_src_to_pythonpath(tmp_path: Path) -> None:
    config = GuiRunConfig(
        media=tmp_path / "song.wav",
        lyrics=tmp_path / "lyrics.txt",
        python_executable=tmp_path / "python.exe",
        demucs_torch_home=tmp_path / "torch",
    )

    environment = build_child_environment(config)

    assert "src" in environment["PYTHONPATH"]
    assert environment["TORCH_HOME"] == str(tmp_path / "torch")
