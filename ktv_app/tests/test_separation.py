import json
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from ktv_app.config import Settings
from ktv_app.media import fingerprint
from ktv_app.separation import prepare_alignment_audio, validate_audio
from ktv_app.workflow import AlignmentOptions


def test_original_mix_never_requires_separation_runtime(tmp_path):
    original = tmp_path / "audio.wav"
    sf.write(original, np.zeros(16000), 16000)
    path, evidence = prepare_alignment_audio(
        "unused.mp4", original, {"audio_offset": 1.5}, tmp_path, "none", Settings()
    )
    assert path == original and evidence["origin"] == 1.5
    assert evidence["sha256"] == fingerprint(original)


@pytest.mark.parametrize("bad", ["short", "stereo", "integer", "nonfinite"])
def test_invalid_stems_cannot_change_the_audio_clock(tmp_path, bad):
    original, stem = tmp_path / "original.wav", tmp_path / "stem.wav"
    sf.write(original, np.zeros(16000), 16000)
    values = (
        np.zeros((16000, 2))
        if bad == "stereo"
        else np.zeros(15999 if bad == "short" else 16000)
    )
    if bad == "nonfinite":
        values[500] = np.nan
    sf.write(stem, values, 16000, subtype="PCM_16" if bad == "integer" else "FLOAT")
    with pytest.raises(ValueError):
        validate_audio(stem, original)


def test_kim_requires_components_before_probing_cuda(tmp_path):
    with pytest.raises(FileNotFoundError, match="分离组件"):
        prepare_alignment_audio(
            "unused",
            tmp_path / "missing.wav",
            {},
            tmp_path,
            "kim",
            Settings(device="cuda", separation_python=tmp_path / "missing_python.exe"),
        )


def test_cache_must_match_the_source_and_stem_hash(tmp_path, monkeypatch):
    import ktv_app.separation as module

    original = tmp_path / "audio.wav"
    sf.write(original, np.zeros(16000), 16000)
    source = tmp_path / "source"
    (source / "configs").mkdir(parents=True)
    (source / "utils.py").write_text("# cache identity test")
    model, python = tmp_path / "model.ckpt", tmp_path / "python.exe"
    model.write_bytes(b"fixture")
    (source / "configs/config_vocals_mel_band_roformer.yaml").write_text("test: true")
    python.write_bytes(b"fixture")
    monkeypatch.setattr(module, "KIM_SHA256", fingerprint(model))
    monkeypatch.setattr(
        module,
        "probe_device",
        lambda *args, **kw: {"device": "cuda", "precision": "bf16"},
    )
    settings = Settings(kim_source=source, kim_model=model, separation_python=python)
    work = tmp_path / "separation/kim"
    work.mkdir(parents=True)
    target = work / "alignment.wav"
    sf.write(target, np.zeros(16000), 16000, subtype="FLOAT")
    identity = dict(
        video_sha256="video",
        mixture_sha256=fingerprint(original),
        checkpoint_sha256=fingerprint(model),
        config_sha256=fingerprint(
            (source / "configs/config_vocals_mel_band_roformer.yaml")
        ),
        source_sha256={"utils.py": fingerprint(source / "utils.py")},
        origin=1.5,
        runtime={
            "device": "cuda",
            "precision": "fp16",
            "requested_device": "cuda",
            "cpu_threads": 8,
        },
        worker_sha256=fingerprint(
            Path(module.__file__).with_name("separation_worker.py")
        ),
        protocol=3,
    )
    evidence = dict(identity=identity, sha256=fingerprint(target))
    (work / "alignment_audio.json").write_text(json.dumps(evidence))
    calls = []
    monkeypatch.setattr(module, "run", lambda *args, **kw: calls.append(args))
    audio, saved = prepare_alignment_audio(
        "unused",
        original,
        {"sha256": "video", "audio_offset": 1.5},
        tmp_path,
        "kim",
        settings,
    )
    assert audio == target and saved == evidence and calls == []


def test_removed_models_are_not_accepted_by_the_public_workflow():
    for model in ("progressive_tifa", "chunked_tifa", "dual_fusion"):
        with pytest.raises(ValueError):
            AlignmentOptions(model=model)
    assert AlignmentOptions().model == "reliable_blocks_tifa"
    assert AlignmentOptions(vocal_separation="kim").vocal_separation == "kim"


def test_all_local_worker_imports_exist_in_source_tree():
    import ast
    import ktv_app

    root = Path(ktv_app.__file__).parent.parent
    for path in (root / "ktv_app").rglob("*.py"):
        if "tests" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and node.module.startswith("ktv_app.")
            ):
                target = root.joinpath(*node.module.split("."))
                assert (
                    target.with_suffix(".py").exists()
                    or (target / "__init__.py").exists()
                ), node.module
