"""Download folder preference: validated once on save, then reused by exports."""

import pytest
from fastapi.testclient import TestClient

from ktv_app import folder_picker
from ktv_app.backend import app as app_module
from ktv_app.backend.app import create_app
from ktv_app.config import Settings


def client(tmp_path):
    return TestClient(create_app(Settings(projects=tmp_path / "projects")))


def test_download_folder_is_created_and_persisted_for_the_next_session(tmp_path):
    folder = tmp_path / "成品" / "导出"
    with client(tmp_path) as api:
        saved = api.put("/api/settings", json={"download_dir": str(folder)})
        assert saved.status_code == 200
        assert saved.json()["download_dir"] == str(folder.resolve())
        assert folder.is_dir()
        assert api.get("/api/settings").json()["download_dir"] == str(folder.resolve())
    # A restart keeps the preference instead of asking the user again.
    with client(tmp_path) as api:
        assert api.get("/api/settings").json()["download_dir"] == str(folder.resolve())


def test_clearing_the_download_folder_keeps_videos_inside_the_project(tmp_path):
    with client(tmp_path) as api:
        assert api.get("/api/settings").json()["download_dir"] == ""
        api.put("/api/settings", json={"download_dir": str(tmp_path / "成品")})
        assert api.put("/api/settings", json={"download_dir": "  "}).json()[
            "download_dir"
        ] == ""


def test_relative_paths_and_existing_files_are_rejected(tmp_path):
    file = tmp_path / "video.mp4"
    file.write_bytes(b"not a folder")
    with client(tmp_path) as api:
        assert api.put(
            "/api/settings", json={"download_dir": "成品/导出"}
        ).status_code == 400
        rejected = api.put("/api/settings", json={"download_dir": str(file)})
        assert rejected.status_code == 400
        assert "文件" in rejected.json()["detail"]
        assert api.get("/api/settings").json()["download_dir"] == ""


def test_settings_dialog_exposes_the_download_folder_field(tmp_path):
    with client(tmp_path) as api:
        page = api.get("/").text
    for element in ("downloadDir", "saveSettings", "settingsStatus"):
        assert f'id="{element}"' in page


def test_choosing_a_folder_returns_it_without_saving_until_confirmed(
    tmp_path, monkeypatch
):
    chosen = str(tmp_path / "成品")
    monkeypatch.setattr(app_module, "pick_directory", lambda title, initial: chosen)
    with client(tmp_path) as api:
        picked = api.post("/api/settings/directory")
        assert picked.status_code == 200
        assert picked.json()["download_dir"] == chosen
        # Filling the field is the page's job; only 保存 commits the preference.
        assert api.get("/api/settings").json()["download_dir"] == ""


def test_cancelling_the_folder_dialog_changes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "pick_directory", lambda title, initial: None)
    with client(tmp_path) as api:
        assert api.post("/api/settings/directory").json()["download_dir"] is None


def test_a_second_dialog_is_refused_while_one_is_open(tmp_path, monkeypatch):
    def busy(title, initial):
        raise ValueError("桌面上已有一个选择窗口，请先完成它")

    monkeypatch.setattr(app_module, "pick_directory", busy)
    with client(tmp_path) as api:
        refused = api.post("/api/settings/directory")
        assert refused.status_code == 400
        assert "选择窗口" in refused.json()["detail"]


def test_only_one_native_dialog_can_be_opened(monkeypatch):
    assert folder_picker.LOCK.acquire(blocking=False)
    try:
        with pytest.raises(ValueError, match="选择窗口"):
            folder_picker.pick_directory()
    finally:
        folder_picker.LOCK.release()


def test_cancelled_native_dialog_returns_no_path(monkeypatch):
    monkeypatch.setattr(folder_picker, "_ask_tk", lambda title, initial: "")
    assert folder_picker.pick_directory() is None
