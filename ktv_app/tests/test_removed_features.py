from pathlib import Path

from fastapi.testclient import TestClient

from ktv_app.backend.app import create_app
from ktv_app.config import Settings
from ktv_app.rendering import video_filename


def test_removed_features_are_not_exposed_or_executable(tmp_path):
    app = create_app(Settings(projects=tmp_path / "projects"))
    with TestClient(app) as client:
        schema = client.get("/openapi.json").json()
        assert not any("llm" in path for path in schema["paths"])
        video = tmp_path / "input.mp4"
        video.write_bytes(b"metadata fixture only")
        project = client.post("/api/projects", json={"video": str(video)}).json()
        for action in ("preview", "verify"):
            result = client.post(
                f"/api/projects/{project['id']}/tasks", json={"action": action}
            )
            assert result.status_code == 400
            assert not client.get(f"/api/projects/{project['id']}").json()["busy"]


def test_uploaded_original_filename_is_preserved_for_export(tmp_path):
    with TestClient(create_app(Settings(projects=tmp_path / "projects"))) as client:
        result = client.post(
            "/api/upload",
            files={"video": ("绿光现场.mp4", b"upload fixture", "video/mp4")},
        )
        assert result.status_code == 200
        project = result.json()
        assert Path(project["video"]).name != "绿光现场.mp4"
        assert (
            video_filename(project["song"], project["artist"], project["video_name"])
            == "绿光现场_KTV.mp4"
        )
