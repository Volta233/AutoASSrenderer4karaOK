"""Time independently recognized text with TIFA, never synthetic word timestamps."""

import math
from pathlib import Path
import time

from ..media import crop
from ..timeline import Line
from .anchor_policy import clean_tifa
from .tifa import TIFAAdapter


def owned_tokens(chunk, timeline):
    """Keep native whole words whose midpoint belongs to this core window."""
    items, tokens = [], []
    for line in timeline.lines:
        for unit in line.units:
            a, b = unit.start, unit.end
            if a is None or b is None:
                reason = "missing_native_time"
            elif not math.isfinite(a) or not math.isfinite(b) or b <= a:
                reason = "nonpositive_or_nonfinite"
            elif a < chunk["start"] or b > chunk["end"] + 0.02:
                reason = "outside_input"
            elif b - a > 4:
                reason = "stretched_word"
            elif unit.timing_source != "model":
                reason = "not_native_model_time"
            elif not chunk["core_start"] <= (a + b) / 2 < chunk["core_end"]:
                reason = "context_owned_by_another_window"
            else:
                reason = "retained"
                tokens.append(dict(text=unit.text, start=a, end=min(b, chunk["end"])))
            items.append(
                dict(
                    text=unit.text,
                    start=a,
                    end=b,
                    decision=reason,
                    unit_id=unit.id,
                    source_start=unit.source_start,
                    source_end=unit.source_end,
                )
            )
    return tokens, items


def time_asr_chunks(audio, result, settings, work):
    """One sequential TIFA batch runs after the ASR process has released its GPU."""
    started = time.monotonic()
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    jobs = []
    for index, chunk in enumerate(result["chunks"]):
        if not chunk["text"].strip():
            continue
        if not 0 < chunk["end"] - chunk["start"] < 90:
            raise ValueError("ASR 文本定位窗口必须小于90秒")
        folder = work / f"window_{index:04d}"
        folder.mkdir(parents=True, exist_ok=True)
        clip = folder / "audio.wav"
        crop(audio, clip, chunk["start"], chunk["end"], settings)
        language = {"Chinese": "zh", "Japanese": "ja", "English": "en"}.get(
            chunk["language"], "auto"
        )
        jobs.append(
            dict(
                id=str(index),
                audio=clip,
                lines=[Line(id=f"asr-{index}", text=chunk["text"], language=language)],
                origin=chunk["start"],
                end=chunk["end"],
                folder=folder,
            )
        )
    timelines = TIFAAdapter(settings).align_many(
        jobs, result["duration"], work / "batch"
    )
    tokens, verification = [], []
    errors = []
    for job in jobs:
        chunk = result["chunks"][int(job["id"])]
        timeline = timelines.get(job["id"])
        if timeline is None or timeline.diagnostics.get("error"):
            chunk["timing_error"] = (
                timeline.diagnostics["error"] if timeline else "missing_tifa_result"
            )
            errors.append(dict(window=job["id"], error=chunk["timing_error"]))
            continue
        native, items = owned_tokens(chunk, clean_tifa(timeline))
        chunk["items"] = items
        chunk["timestamp_counts"] = {
            decision: sum(item["decision"] == decision for item in items)
            for decision in dict.fromkeys(item["decision"] for item in items)
        }
        chunk["timing_diagnostics"] = timeline.diagnostics
        target = tokens if chunk["pass_index"] == 0 else verification
        target.extend(native)
    result.update(
        tokens=sorted(tokens, key=lambda t: (t["start"], t["end"])),
        verification_tokens=sorted(verification, key=lambda t: (t["start"], t["end"])),
        timestamp_model="TIFA-1.0-ST",
        timestamp_errors=errors,
        tifa_timing_elapsed=round(time.monotonic() - started, 3),
    )
    result["elapsed"] = result["asr_elapsed"] + result["tifa_timing_elapsed"]
    if not tokens or not verification:
        raise ValueError("未取得双遍 TIFA 原生词时间；保留识别文本，不伪造切点")
    return result
