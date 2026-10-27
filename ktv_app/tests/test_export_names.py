from pathlib import Path

import pytest
from pydantic import ValidationError

from ktv_app.rendering import RenderOptions, render, video_filename


@pytest.mark.parametrize(
    "song,artist,video,expected",
    [
        ("发如雪", "周杰伦", "input.mp4", "发如雪 - 周杰伦_KTV.mp4"),
        (" 绿光 ", "", "input.mp4", "绿光_KTV.mp4"),
        ("", "", "我的视频.mp4", "我的视频_KTV.mp4"),
        ("a/b:c?", "x\\y*", "input.mp4", "a_b_c_ - x_y__KTV.mp4"),
        ("CON", None, "input.mp4", "CON_KTV.mp4"),
        ("\x00. .", "", "input.mp4", "歌曲_KTV.mp4"),
    ],
)
def test_readable_portable_video_name(song, artist, video, expected):
    assert video_filename(song, artist, video) == expected


def test_filename_is_bounded_and_cannot_escape_export_folder(tmp_path):
    name = video_filename("../" + "歌" * 300, "歌手", "input.mp4")
    assert Path(name).name == name
    assert len(name) <= 148
    for unsafe in ("../outside.mp4", str(tmp_path.parent / "outside.mp4"), "file.txt"):
        with pytest.raises(ValueError, match="文件名"):
            render("input.mp4", tmp_path, {}, None, RenderOptions(), unsafe)


def test_removed_preview_parameters_are_not_silently_accepted():
    with pytest.raises(ValidationError):
        RenderOptions(preview_start=10, preview_duration=20)
