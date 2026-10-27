"""Mirror routing must preserve the frozen bytes and recover from bad sources."""

import hashlib
import importlib
import io
from pathlib import Path

import pytest


@pytest.fixture
def api(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    monkeypatch.setenv("LYRIFRAME_DOWNLOAD_SOURCE", "cn")
    return importlib.import_module("download_models")


class Response(io.BytesIO):
    status = 200

    def __init__(self, content):
        super().__init__(content)
        self.headers = {"Content-Length": str(len(content))}


@pytest.mark.parametrize(
    "original,first",
    [
        (
            "https://huggingface.co/Qwen/Qwen3-ASR-1.7B/resolve/frozen/config.json",
            "https://modelscope.cn/",
        ),
        (
            "https://huggingface.co/KimberleyJSN/model/resolve/frozen/model.ckpt",
            "https://hf-mirror.com/",
        ),
        (
            "https://codeload.github.com/openvpi/TIFA/zip/frozen",
            "https://ghfast.top/https://github.com/openvpi/TIFA/archive/frozen.zip",
        ),
        (
            "https://github.com/a/b/releases/download/v1/model.zip",
            "https://ghfast.top/",
        ),
        (
            "https://download-r2.pytorch.org/whl/cu128/torch.whl",
            "https://mirrors.aliyun.com/pytorch-wheels/cu128/",
        ),
        (
            "https://files.pythonhosted.org/packages/a/pip.whl",
            "https://pypi.tuna.tsinghua.edu.cn/packages/",
        ),
    ],
)
def test_domestic_first_and_official_last(api, original, first):
    urls = api.candidate_urls(original)
    assert urls[0].startswith(first)
    assert urls[-1] == original
    assert len(urls) == len(set(urls))


def test_official_mode_disables_all_mirrors(api, monkeypatch):
    monkeypatch.setenv("LYRIFRAME_DOWNLOAD_SOURCE", "official")
    original = "https://huggingface.co/a/b/resolve/frozen/config.json"
    assert api.candidate_urls(original) == [original]


def test_kim_uses_verified_modelscope_snapshot_first(api):
    original = "https://huggingface.co/KimberleyJSN/melbandroformer/resolve/ac9b0614ab3cd7f77219e18ba494dfd93956c348/MelBandRoformer.ckpt"
    assert (
        api.candidate_urls(original)[0]
        == "https://modelscope.cn/models/cpadyun/melband-roformer/resolve/2fb90062313e164074d3d943fd133e5d7295ceb2/MelBandRoformer.ckpt"
    )


def test_default_ten_retries_survive_five_transient_failures(
    api, monkeypatch, tmp_path
):
    monkeypatch.delenv("LYRIFRAME_DOWNLOAD_RETRIES", raising=False)
    calls, delays = [], []
    payload = b"verified after intermittent failures"

    def serve(*args, **kwargs):
        calls.append(True)
        if len(calls) <= 5:
            raise TimeoutError("temporary network timeout")
        return Response(payload)

    monkeypatch.setattr(api.urllib.request, "urlopen", serve)
    monkeypatch.setattr(api.time, "sleep", delays.append)
    target = tmp_path / "model"
    api.download(
        "https://example.invalid/model", target, hashlib.sha256(payload).hexdigest()
    )
    assert api.download_retries() == 10
    assert len(calls) == 6
    assert delays == [2, 4, 8, 16, 30]
    assert target.read_bytes() == payload


@pytest.mark.parametrize("retries", [0, 4, 15])
def test_retry_limit_is_configurable(api, monkeypatch, tmp_path, retries):
    monkeypatch.setenv("LYRIFRAME_DOWNLOAD_RETRIES", str(retries))
    calls = []

    def serve(*args, **kwargs):
        calls.append(True)
        raise TimeoutError("temporary outage")

    monkeypatch.setattr(api.urllib.request, "urlopen", serve)
    monkeypatch.setattr(api.time, "sleep", lambda _: None)
    with pytest.raises(RuntimeError, match="Download failed"):
        api.download("https://example.invalid/model", tmp_path / "model", "0" * 64)
    assert len(calls) == retries + 1


def test_missing_file_moves_to_next_source_without_ten_retries(
    api, monkeypatch, tmp_path
):
    calls = []

    def serve(request, **kwargs):
        calls.append(request.full_url)
        raise api.urllib.error.HTTPError(request.full_url, 404, "missing", {}, None)

    monkeypatch.setattr(api.urllib.request, "urlopen", serve)
    with pytest.raises(RuntimeError):
        api.download(
            "https://huggingface.co/a/b/resolve/frozen/model",
            tmp_path / "model",
            "0" * 64,
        )
    assert len(calls) == 2


def test_truncated_http_response_is_retried(api, monkeypatch, tmp_path):
    calls = []
    payload = b"complete payload"

    def serve(*args, **kwargs):
        calls.append(True)
        if len(calls) == 1:
            raise api.IncompleteRead(b"partial", 20)
        return Response(payload)

    monkeypatch.setattr(api.urllib.request, "urlopen", serve)
    monkeypatch.setattr(api.time, "sleep", lambda _: None)
    target = tmp_path / "model"
    api.download(
        "https://example.invalid/model", target, hashlib.sha256(payload).hexdigest()
    )
    assert target.read_bytes() == payload
    assert len(calls) == 2


def test_torch_retries_more_than_two_transient_failures(api, monkeypatch, tmp_path):
    torch = importlib.import_module("download_torch")
    calls = []
    payload = b"verified wheel"

    def serve(*args, **kwargs):
        calls.append(True)
        if len(calls) <= 5:
            raise TimeoutError("temporary failure")
        response = Response(payload)
        response.status = 206
        response.headers["Content-Range"] = f"bytes 0-{len(payload) - 1}/{len(payload)}"
        return response

    monkeypatch.setattr(api.urllib.request, "urlopen", serve)
    monkeypatch.setattr(api.time, "sleep", lambda _: None)
    target = tmp_path / "torch.whl"
    torch.ranged_download(
        "https://example.invalid/wheel", target, hashlib.sha256(payload).hexdigest()
    )
    assert target.read_bytes() == payload
    assert len(calls) == 6


def test_corrupt_mirror_falls_back_without_accepting_changed_weights(
    api, tmp_path, monkeypatch
):
    good = b"frozen verified model"
    calls = []

    def serve(request, **kwargs):
        calls.append(request.full_url)
        return Response(b"corrupt" if "hf-mirror.com" in request.full_url else good)

    monkeypatch.setattr(api.urllib.request, "urlopen", serve)
    monkeypatch.setattr(api.time, "sleep", lambda _: None)
    target = tmp_path / "model"
    api.download(
        "https://huggingface.co/a/b/resolve/frozen/model",
        target,
        hashlib.sha256(good).hexdigest(),
    )
    assert target.read_bytes() == good
    assert any("hf-mirror.com" in url for url in calls)
    assert calls[-1].startswith("https://huggingface.co/")
    assert not target.with_suffix(".part").exists()


def test_torch_mirror_without_range_can_stream_verified_file(
    api, monkeypatch, tmp_path
):
    torch = importlib.import_module("download_torch")
    payload = b"verified torch wheel"
    monkeypatch.setattr(
        api.urllib.request, "urlopen", lambda *a, **k: Response(payload)
    )
    target = tmp_path / "torch.whl"
    torch.ranged_download(
        "https://download-r2.pytorch.org/whl/cu128/torch.whl",
        target,
        hashlib.sha256(payload).hexdigest(),
    )
    assert target.read_bytes() == payload


def test_dependencies_retry_one_index_at_a_time_without_user_config(api, monkeypatch):
    installer = importlib.import_module("install_dependencies")
    calls = []

    def run(command, env):
        calls.append(command)
        assert env["PIP_CONFIG_FILE"] == installer.os.devnull
        assert "PIP_EXTRA_INDEX_URL" not in env
        assert command.count("--index-url") == 1
        return type("Result", (), {"returncode": 0 if len(calls) == 2 else 1})()

    monkeypatch.setenv("PIP_EXTRA_INDEX_URL", "https://example.invalid/simple")
    monkeypatch.setattr(installer.subprocess, "run", run)
    monkeypatch.setattr(
        installer.sys, "argv", ["install_dependencies.py", "-r", "requirements.txt"]
    )
    installer.main()
    assert len(calls) == 2
    assert "https://pypi.tuna.tsinghua.edu.cn/simple" in calls[0]
    assert "https://mirrors.aliyun.com/pypi/simple" in calls[1]
