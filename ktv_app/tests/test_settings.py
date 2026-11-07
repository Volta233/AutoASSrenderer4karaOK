"""Download folder preference: validated once on save, then reused by exports."""

from fastapi.testclient import TestClient

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
