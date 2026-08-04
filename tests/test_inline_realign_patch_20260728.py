from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from lyricalign.demo import media_render

ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / "demo" / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_stable_trial_request_uses_explicit_args_value() -> None:
    module = load_script("stable_trial_request_patch", "run_inline_realign_experiment.py")
    request = module.build_stable_trial_request(
        args=SimpleNamespace(max_stable_window_trials_per_item=3, stable_left_overlap_units=11),
        assistance_payload={"identity": {"request_hash": "assist-hash"}},
    )
    assert request["max_trials_per_item"] == 3
    assert request["stable_left_overlap_units"] == 11
    assert request["assistance_request_hash"] == "assist-hash"


def test_watcher_reads_actual_visual_and_render_summary_fields(tmp_path: Path) -> None:
    module = load_script("watcher_patch", "watch_inline_realign_status.py")
    (tmp_path / "experiment_manifest.jsonl").write_text(
        json.dumps({"item_id": "x"}) + "\n", encoding="utf-8"
    )
    write_json(tmp_path / "experiment_summary.json", {"completed_item_count": 1, "failed_item_count": 0})
    write_json(tmp_path / "visualization_summary.json", {"complete_count": 1, "failed_count": 0})
    write_json(tmp_path / "demo_render_summary.json", {"rendered_item_count": 1, "failed_item_count": 0})
    output = module.render(tmp_path)
    assert "静态图汇总：合计完成 1" in output
    assert "视频汇总：合计完成 1" in output
    assert "失败 0" in output


def test_visualization_missing_experimental_alignments_fails_stage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_script("visual_patch", "analyze_inline_realign_visuals.py")
    item_id = "demo_x"
    manifest_path = tmp_path / "experiment_manifest.jsonl"
    manifest_path.write_text(json.dumps({
        "item_id": item_id,
        "dataset": "demo",
        "variant_set": "official_primary",
        "gt_path": None,
    }) + "\n", encoding="utf-8")
    item_root = tmp_path / "items" / item_id
    alignment = {
        "characters": [{"global_character_index": 0, "character": "歌", "start_sec": 0.0, "end_sec": 0.5}],
        "summary": {"audio_duration_sec": 1.0},
        "window_trace": [],
    }
    write_json(item_root / "branches" / "B2_30_silence_official" / "alignment.json", alignment)
    write_json(item_root / "branches" / "B2_30_silence_official" / "alignment.raw.json", alignment)
    write_json(tmp_path / "resolved_config.json", {
        "source_config": {
            "shadow": {
                "stable_anchor": {"enabled": True},
                "deferred_realign": {"enabled": True, "immediate_inline": True},
            }
        }
    })
    monkeypatch.setattr(module, "detect_font", lambda value: "Noto Sans CJK SC")
    monkeypatch.setattr(
        module.sys,
        "argv",
        [
            "analyze_inline_realign_visuals.py",
            "--manifest", str(manifest_path),
            "--experiment-root", str(tmp_path),
        ],
    )
    assert module.main() == 1
    summary = json.loads((tmp_path / "visualization_summary.json").read_text(encoding="utf-8"))
    assert summary["complete_count"] == 0
    assert summary["failed_count"] == 1
    assert "expected stable/realign alignments" in summary["failures"][0]["error"]


def test_render_set_rejects_missing_comparison_inputs(tmp_path: Path) -> None:
    module = load_script("render_patch", "render_inline_realign_demo_batch.py")
    with pytest.raises(FileNotFoundError, match="comparison inputs"):
        module.render_set(
            paths=[tmp_path / f"missing_{index}.json" for index in range(4)],
            labels=["a", "b", "c", "d"],
            visual=None,
            audio=tmp_path / "audio.wav",
            output=tmp_path / "out.mp4",
            ass_root=tmp_path / "ass",
            font="Noto Sans CJK SC",
            profile="review",
            force=False,
        )


def test_detect_font_preserves_sc_collection_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    collection = tmp_path / "NotoSansCJK-Regular.ttc"
    collection.write_bytes(b"font-placeholder")
    extracted = tmp_path / "NotoSansCJKSC-Regular.otf"
    extracted.write_bytes(b"sc-face-placeholder")
    monkeypatch.setattr(
        media_render,
        "_fontconfig_match",
        lambda preferred: (collection.resolve(), 2, "Noto Sans CJK SC", "Regular"),
    )
    extraction_calls: list[tuple[Path, int, str]] = []

    def fake_extract(path: Path, *, face_index: int, expected_family: str) -> Path:
        extraction_calls.append((path, face_index, expected_family))
        return extracted

    registered: list[Path] = []

    def fake_register(path: Path) -> str:
        registered.append(path)
        return "Noto Sans CJK SC"

    monkeypatch.setattr(media_render, "_extract_collection_face_for_matplotlib", fake_extract)
    monkeypatch.setattr(media_render, "_register_matplotlib_font", fake_register)
    assert media_render.detect_font("Noto Sans CJK SC") == "Noto Sans CJK SC"
    assert extraction_calls == [(collection.resolve(), 2, "Noto Sans CJK SC")]
    assert registered == [extracted]


def test_detect_font_rejects_jp_substitution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    collection = tmp_path / "NotoSansCJK-Regular.ttc"
    collection.write_bytes(b"font-placeholder")
    monkeypatch.setattr(
        media_render,
        "_fontconfig_match",
        lambda preferred: (collection.resolve(), 0, "Noto Sans CJK JP", "Regular"),
    )
    with pytest.raises(RuntimeError, match="did not resolve.*Simplified Chinese"):
        media_render.detect_font("Noto Sans CJK SC")


def test_detect_font_falls_back_when_fontconfig_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        media_render,
        "_fontconfig_match",
        lambda preferred: (_ for _ in ()).throw(RuntimeError("fc-match is required")),
    )
    monkeypatch.setattr(
        media_render,
        "_matplotlib_find_family",
        lambda family: family if family == "Microsoft YaHei" else None,
    )
    assert media_render.detect_font("Noto Sans CJK SC") == "Microsoft YaHei"
