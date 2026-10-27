import json
from pathlib import Path

import pytest

from ktv_app.aligners.asr_timing import owned_tokens, time_asr_chunks
from ktv_app.config import Settings
from ktv_app.timeline import Line, Timeline, Unit


def timed(a, b, kind="model"):
    return Timeline(
        duration=30,
        lines=[
            Line(
                id="l",
                text="test",
                units=[
                    Unit(
                        id="u",
                        text="test",
                        source_start=0,
                        source_end=4,
                        start=a,
                        end=b,
                        timing_source=kind,
                    )
                ],
            )
        ],
    )


@pytest.mark.parametrize(
    "a,b,kind,decision",
    [
        (10, 11, "model", "context_owned_by_another_window"),
        (12, 13, "model", "retained"),
        (None, None, "model", "missing_native_time"),
        (9, 10, "model", "outside_input"),
        (14, 19, "model", "outside_input"),
        (12, 17, "model", "stretched_word"),
        (12, 13, "estimated", "not_native_model_time"),
    ],
)
def test_only_native_owned_words_can_certify_a_cut(a, b, kind, decision):
    chunk = dict(start=10, end=18, core_start=12, core_end=16)
    tokens, items = owned_tokens(chunk, timed(a, b, kind))
    assert items[0]["decision"] == decision
    assert bool(tokens) == (decision == "retained")


def test_short_window_location_uses_asr_text_and_preserves_both_passes(
    tmp_path, monkeypatch
):
    import ktv_app.aligners.asr_timing as module

    monkeypatch.setattr(module, "crop", lambda *args: None)
    captured = []

    def infer(self, jobs, duration, work):
        captured.extend(jobs)
        return {job["id"]: timed(12, 13) for job in jobs}

    monkeypatch.setattr(module.TIFAAdapter, "align_many", infer)
    chunks = [
        dict(
            start=10,
            end=18,
            core_start=12,
            core_end=16,
            text="independent recognized words",
            language="English",
            pass_index=p,
        )
        for p in (0, 1)
    ]
    result = time_asr_chunks(
        "audio.wav",
        dict(
            chunks=chunks,
            duration=30,
            asr_elapsed=1,
        ),
        Settings(),
        tmp_path,
    )
    assert [job["lines"][0].text for job in captured] == [chunks[0]["text"]] * 2
    assert result["tokens"] == result["verification_tokens"]
    assert result["timestamp_model"] == "TIFA-1.0-ST"


def test_failed_short_window_location_does_not_invent_timestamps(tmp_path, monkeypatch):
    import ktv_app.aligners.asr_timing as module

    monkeypatch.setattr(module, "crop", lambda *args: None)
    monkeypatch.setattr(module.TIFAAdapter, "align_many", lambda *args: {})
    with pytest.raises(ValueError, match="原生词时间"):
        time_asr_chunks(
            "audio.wav",
            dict(
                chunks=[
                    dict(
                        start=0,
                        end=16,
                        text="heard text",
                        language="English",
                        core_start=0,
                        core_end=12,
                        pass_index=0,
                    )
                ],
                duration=30,
                asr_elapsed=1,
            ),
            Settings(),
            tmp_path,
        )


def test_release_has_no_qwen_fa_model_dependency():
    root = Path(__file__).resolve().parents[2]
    from dataclasses import fields

    assert "locator_model" not in {field.name for field in fields(Settings)}
    worker = (root / "ktv_app/aligners/locator_worker.py").read_text(encoding="utf-8")
    assert "forced_aligner=" not in worker and "return_time_stamps=False" in worker
    assert not (root / "ktv_app/aligners/qwen_fa_worker.py").exists()
