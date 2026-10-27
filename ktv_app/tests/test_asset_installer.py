"""Exercise interrupted downloads, verification and safe installation without public network."""

import hashlib
import importlib.util
import io
import json
from pathlib import Path
import zipfile

import pytest


def module():
    path = Path(__file__).resolve().parents[2] / "scripts/download_models.py"
    spec = importlib.util.spec_from_file_location("asset_installer", path)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


class Response(io.BytesIO):
    def __init__(self, data, status=200, headers=None):
        super().__init__(data)
        self.status = status
        self.headers = headers or {"Content-Length": str(len(data))}


@pytest.mark.parametrize("resume", [True, False])
def test_partial_download_resumes_or_restarts_if_range_ignored(
    tmp_path, monkeypatch, resume
):
    api = module()
    payload = b"verified model bytes"
    path = tmp_path / "model"
    path.with_suffix(".part").write_bytes(payload[:5])

    def serve(request, **kwargs):
        assert request.headers["Range"] == "bytes=5-"
        return (
            Response(
                payload[5:],
                206,
                {"Content-Range": f"bytes 5-{len(payload) - 1}/{len(payload)}"},
            )
            if resume
            else Response(payload)
        )

    monkeypatch.setattr(api.urllib.request, "urlopen", serve)
    api.download(
        "https://example.invalid/model", path, hashlib.sha256(payload).hexdigest()
    )
    assert path.read_bytes() == payload
    assert not path.with_suffix(".part").exists()


def test_bad_hash_does_not_replace_existing_file(tmp_path, monkeypatch):
    api = module()
    path = tmp_path / "model"
    path.write_bytes(b"old")
    monkeypatch.setattr(api.urllib.request, "urlopen", lambda *a, **k: Response(b"bad"))
    monkeypatch.setattr(api.time, "sleep", lambda *a: None)
    with pytest.raises(RuntimeError, match="Download failed"):
        api.download("https://example.invalid/model", path, "0" * 64)
    assert path.read_bytes() == b"old"


@pytest.mark.parametrize(
    "name", ["../escape", "/absolute", "C:/escape", "safe/../../escape"]
)
def test_archive_cannot_write_outside_installation(tmp_path, name):
    api = module()
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr(name, b"unsafe")
    with pytest.raises(ValueError, match="Unsafe"):
        api.extract(archive, tmp_path / "output", 0)
    assert not (tmp_path / "output").exists()


def test_windows_path_separators_are_rejected(tmp_path):
    with pytest.raises(ValueError, match="Unsafe"):
        module().within(tmp_path, "safe\\escape")


def test_verified_archive_receipt_repairs_missing_files(tmp_path, monkeypatch):
    api = module()
    archive = tmp_path / "source.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("version/config.json", "{}")
    payload = archive.read_bytes()
    calls = []

    def serve(*args, **kwargs):
        calls.append(True)
        return Response(payload)

    monkeypatch.setattr(api.urllib.request, "urlopen", serve)
    assets = [
        dict(
            name="resource.zip",
            target="models/resource",
            kind="zip",
            strip=1,
            url="https://example.invalid/source",
            sha256=hashlib.sha256(payload).hexdigest(),
        )
    ]
    root = tmp_path / "新电脑 with spaces"
    api.install(root, assets)
    api.install(root, assets)
    assert len(calls) == 1
    (root / "models/resource/config.json").unlink()
    api.install(root, assets)
    assert len(calls) == 2
    assert (root / "models/resource/config.json").read_text() == "{}"


def test_locked_download_plan_has_no_forced_aligner_or_sony():
    root = Path(__file__).resolve().parents[2]
    assets = json.loads(
        (root / "requirements/assets.lock.json").read_text(encoding="utf-8")
    )["assets"]
    text = json.dumps(assets).lower()
    assert "forcedaligner" not in text and "sonymimo" not in text
    assert "qwen3-asr-1.7b" in text and "melbandroformer.ckpt" in text
    assert len({a["target"] for a in assets}) == len(assets)
    assert all(
        len(a["sha256"]) == 64 and a["url"].startswith("https://") for a in assets
    )


def test_health_checks_the_actual_kim_resource_layout(tmp_path):
    from fastapi.testclient import TestClient
    from ktv_app.backend.app import create_app
    from ktv_app.config import Settings

    source = tmp_path / "Kim"
    (source / "configs").mkdir(parents=True)
    (source / "configs/config_vocals_mel_band_roformer.yaml").write_text("model: {}")
    (source / "utils.py").write_text("# official demixer")
    model, python = tmp_path / "MelBandRoformer.ckpt", tmp_path / "python.exe"
    model.write_bytes(b"fixture")
    python.write_bytes(b"fixture")
    settings = Settings(
        projects=tmp_path / "jobs",
        kim_source=source,
        kim_model=model,
        separation_python=python,
    )
    with TestClient(create_app(settings)) as client:
        result = client.get("/api/health").json()
    assert result["kim_ready"] is True
    assert all(
        result["checks"][name]["ok"]
        for name in ("kim_config", "kim_source", "kim_model", "kim_python")
    )


def torch_downloader(monkeypatch):
    import sys

    scripts = Path(__file__).resolve().parents[2] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location(
        "torch_downloader", scripts / "download_torch.py"
    )
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def test_torch_wheel_range_resumes_and_validates_hash(tmp_path, monkeypatch):
    api = torch_downloader(monkeypatch)
    payload = b"verified wheel bytes"
    target = tmp_path / "wheel.whl"
    target.with_suffix(".whl.part").write_bytes(payload[:4])

    def serve(request, **kwargs):
        assert request.headers["Range"].startswith("bytes=4-")
        assert "lyriframe_range=4-" in request.full_url
        return Response(
            payload[4:],
            206,
            {"Content-Range": f"bytes 4-{len(payload) - 1}/{len(payload)}"},
        )

    monkeypatch.setattr(api.urllib.request, "urlopen", serve)
    api.ranged_download(
        "https://example.invalid/wheel", target, hashlib.sha256(payload).hexdigest()
    )
    assert target.read_bytes() == payload


def test_torch_wheel_rejects_wrong_content_range(tmp_path, monkeypatch):
    api = torch_downloader(monkeypatch)
    monkeypatch.setattr(
        api.urllib.request,
        "urlopen",
        lambda *a, **k: Response(b"wrong", 206, {"Content-Range": "bytes 5-9/10"}),
    )
    monkeypatch.setattr(api.time, "sleep", lambda *a: None)
    target = tmp_path / "wheel.whl"
    with pytest.raises(ValueError, match="different byte range"):
        api.ranged_download("https://example.invalid/wheel", target, "0" * 64)
    assert not target.exists()


def test_corrupt_completed_partial_wheel_can_restart(tmp_path, monkeypatch):
    api = torch_downloader(monkeypatch)
    payload = b"good wheel"
    target = tmp_path / "wheel.whl"
    target.with_suffix(".whl.part").write_bytes(b"corrupt completed wheel")
    requests = []

    def serve(request, **kwargs):
        requests.append(request)
        if len(requests) == 1:
            raise api.urllib.error.HTTPError(
                request.full_url, 416, "range rejected", {}, None
            )
        assert request.headers["Range"].startswith("bytes=0-")
        return Response(
            payload,
            206,
            {"Content-Range": f"bytes 0-{len(payload) - 1}/{len(payload)}"},
        )

    monkeypatch.setattr(api.urllib.request, "urlopen", serve)
    monkeypatch.setattr(api.time, "sleep", lambda *a: None)
    api.ranged_download(
        "https://example.invalid/wheel", target, hashlib.sha256(payload).hexdigest()
    )
    assert target.read_bytes() == payload


@pytest.mark.parametrize(
    "version,tag", [((3, minor), f"cp3{minor}") for minor in range(10, 15)]
)
def test_cuda_wheels_match_interpreter_and_remain_frozen(monkeypatch, version, tag):
    api = torch_downloader(monkeypatch)
    assets = api.selected_assets(version)
    assert len(assets) == 2
    assert {a["name"].split("-")[0] for a in assets} == {"torch", "torchaudio"}
    assert all(f"-{tag}-{tag}-win_amd64.whl" in a["name"] for a in assets)
    assert all(
        len(a["sha256"]) == 64
        and a["url"].startswith("https://download-r2.pytorch.org/")
        for a in assets
    )


def test_cuda_wheel_selection_rejects_unsupported_python(monkeypatch):
    with pytest.raises(ValueError, match="3.10, 3.11, 3.12, 3.13, 3.14"):
        torch_downloader(monkeypatch).selected_assets((3, 15))
