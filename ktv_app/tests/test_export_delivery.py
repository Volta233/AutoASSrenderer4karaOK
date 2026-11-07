"""Exports land in the configured download folder instead of a manual download."""

from pathlib import Path

from ktv_app.backend.service import Projects
from ktv_app.backend.storage import write_json
from ktv_app.backend.tasks import export as export_task
from ktv_app.backend.tasks.context import TaskContext
from ktv_app.config import Settings
from ktv_app.timeline import Timeline


def test_delivered_video_never_overwrites_an_earlier_export(tmp_path):
    source = tmp_path / "renders" / "r1" / "发如雪 - 周杰伦_KTV.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"first render")
    folder = tmp_path / "成品"
    first, error = export_task.deliver(source, folder)
    second, _ = export_task.deliver(source, folder)
    assert error is None
    assert Path(first).read_bytes() == b"first render"
    assert Path(second).name == "发如雪 - 周杰伦_KTV (2).mp4"
    # The render also stays in the project folder, so a retry costs no re-render.
    assert source.is_file()


def test_unusable_download_folder_reports_the_reason_and_keeps_the_render(tmp_path):
    source = tmp_path / "song_KTV.mp4"
    source.write_bytes(b"render")
    blocker = tmp_path / "blocked"
    blocker.write_bytes(b"a file where the folder should be")
    saved, error = export_task.deliver(source, blocker / "成品")
    assert saved is None
    assert "下载目录" in error
    assert source.read_bytes() == b"render"


def export_once(tmp_path, monkeypatch, download_dir=""):
    """Run the export handler with rendering stubbed out; return store, pid, name."""
    settings = Settings(projects=tmp_path / "projects")
    store = Projects(settings)
    video = tmp_path / "input.mp4"
    video.write_bytes(b"CRUD fixture; never passed to a model")
    project = store.create(video, "Song", "Artist", "en")
    pid = project["id"]
    folder = store.folder(pid)
    name = "Song - Artist_KTV.mp4"
    write_json(folder / "timeline.json", Timeline(duration=30).model_dump())
    write_json(
        folder / "media.json",
        {"duration": 30, "audio_offset": 0, "raw": {"streams": []}},
    )
    store.preferences.save(download_dir=str(download_dir) if download_dir else "")

    def write_bundle(timeline, style, bundle):
        bundle.mkdir(parents=True, exist_ok=True)
        write_json(
            bundle / "export_report.json",
            {"rendered_lines": 0, "total_lines": 0, "unmatched_lines": 0},
        )

    def render(video, bundle, info, settings, options, output_name=None):
        target = Path(bundle) / output_name
        target.write_bytes(b"rendered mp4")
        return target

    monkeypatch.setattr(export_task, "write_bundle", write_bundle)
    monkeypatch.setattr(export_task, "render", render)
    context = TaskContext(
        store,
        pid,
        "export",
        export_task.RenderOptions().model_dump(),
        store.get(pid),
        folder,
        store.get(pid)["media"],
    )
    export_task.export(context)
    return store, pid, name


def test_export_writes_the_finished_video_into_the_download_folder(
    tmp_path, monkeypatch
):
    folder = tmp_path / "成品"
    store, pid, name = export_once(tmp_path, monkeypatch, folder)
    try:
        saved = store.get(pid)["artifacts"][-1]["saved"]
        assert saved == str(folder / name)
        assert Path(saved).read_bytes() == b"rendered mp4"
        # The render stays inside the project so a failed download is retryable.
        assert list((store.folder(pid) / "renders").glob("*/*.mp4"))
        assert "成片已保存到" in store.get(pid)["events"][-1]["message"]
    finally:
        store.pool.shutdown(wait=True)


def test_export_without_a_download_folder_keeps_the_video_in_the_project(
    tmp_path, monkeypatch
):
    store, pid, _ = export_once(tmp_path, monkeypatch)
    try:
        artifact = store.get(pid)["artifacts"][-1]
        assert artifact["saved"] is None and artifact["save_error"] is None
        assert (store.folder(pid) / artifact["path"]).is_file()
    finally:
        store.pool.shutdown(wait=True)
