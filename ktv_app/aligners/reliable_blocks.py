"""Orchestrate independent ASR evidence, bounded TIFA blocks and seam repair.

The original helper imports remain available for existing callers. New code
should import helpers from their owning module.
"""

import json
from collections import Counter
from pathlib import Path
from ..timeline import Timeline, quality
from .anchor_policy import POLICY
from .recognition import locate, match_text, japanese_reading_table
from .block_policy import PROFILE, RELAXATION_RULES
from .boundary_matching import (
    match_boundary_lines,
    strong_match,
    unique_context,
    repeat_proof,
    complete_line_cut,
    partial_unique_cut,
)
from .block_planning import (
    build_plan,
    build_adaptive_plan,
    build_duration_limited_plan,
    context_job,
)
from .block_execution import run_blocks
from .seam_repair import repair_seams, seam_groups, retry_seam_gaps, bounded_seam_groups

__all__ = [
    "match_boundary_lines",
    "strong_match",
    "unique_context",
    "repeat_proof",
    "complete_line_cut",
    "build_plan",
    "build_adaptive_plan",
    "partial_unique_cut",
    "build_duration_limited_plan",
    "context_job",
    "run_blocks",
    "repair_seams",
    "seam_groups",
    "retry_seam_gaps",
    "bounded_seam_groups",
    "align_reliable_blocks",
    "PROFILE",
    "RELAXATION_RULES",
    "match_text",
]


def align_reliable_blocks(
    audio,
    lines,
    duration,
    origin,
    work,
    settings,
    event=lambda message: None,
    max_input_seconds=None,
):
    import soundfile as sf

    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    limit = min(duration, origin + sf.info(audio).duration)
    event(
        "ASR 做两次错开短窗口识别（12秒核心＋两侧2秒），核验句子与重复段归属；不使用旧时间戳"
    )
    recognition, warning = locate(
        audio, lines, settings, work, profile=PROFILE, event=event
    )
    if not recognition:
        raise RuntimeError(warning or "ASR 未完成；正式路线不可跳过识别阶段")
    readings, reading_support = japanese_reading_table(
        lines, recognition, settings, work
    )
    reading = (lambda text: readings.get(text, text)) if readings else None
    planner = (
        build_adaptive_plan
        if max_input_seconds is None
        else build_duration_limited_plan
    )
    plan, first, second = planner(
        lines,
        recognition.get("tokens", []),
        recognition.get("verification_tokens", []),
        origin,
        limit,
        reading,
        event,
        **(
            {}
            if max_input_seconds is None
            else dict(max_input_seconds=max_input_seconds)
        ),
    )
    plan["reading_support"] = reading_support
    plan["asr_timestamp_summary"] = dict(
        sum(
            (
                Counter(chunk.get("timestamp_counts", {}))
                for chunk in recognition.get("chunks", [])
            ),
            Counter(),
        )
    )
    (work / "block_plan.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if max_input_seconds is not None and not plan["adaptive_relaxation"]["target_met"]:
        raise ValueError(
            f"识别结果与已确认歌词之间没有足够的可信切点，无法生成小于{max_input_seconds}秒的对齐片段。"
            "请核对演唱语言、歌词版本及实际演唱段落后重试；识别与切片诊断已保存。"
        )
    event(
        f"通过核验的切点 {len(plan['boundaries'])} 个，分为 {len(plan['blocks'])} 个 TIFA 大块；无可信切点不强行切割"
    )
    first_by = {m["line_index"]: m for m in first}
    second_by = {m["line_index"]: m for m in second}
    results = run_blocks(
        audio,
        lines,
        plan["blocks"],
        first_by,
        second_by,
        duration,
        origin,
        limit,
        work,
        settings,
        event,
    )
    adjustments = repair_seams(results, plan)
    if adjustments:
        event(
            f"接缝在 TIFA 与双窗口 ASR 共同支持的词间空隙内微调 {len(adjustments)} 处；原生词时间不变"
        )
    seam_local_retries = retry_seam_gaps(
        audio, results, duration, origin, limit, work, settings, event
    )
    groups, deferred_seams = bounded_seam_groups(
        results, lines, first_by, second_by, origin, limit
    )
    retries = []
    final = []
    for group in groups:
        if len(group) == 1:
            final.append(results[group[0]])
            continue
        subset = [results[i][0] for i in group]
        merged = dict(
            id="join_" + "_".join(str(i) for i in group),
            indices=[i for block in subset for i in block["indices"]],
            line_ids=[i for block in subset for i in block["line_ids"]],
            core_start=subset[0]["core_start"],
            core_end=subset[-1]["core_end"],
            reason="seam_conflict_merged_retry",
            context_limit_seconds=6,
            max_core_seconds=45,
        )
        merged["duration"] = merged["core_end"] - merged["core_start"]
        merged["over_target"] = merged["duration"] > 45
        event("接缝有越界候选，合并相邻大块重试 TIFA：" + merged["id"])
        retried = run_blocks(
            audio,
            lines,
            [merged],
            first_by,
            second_by,
            duration,
            origin,
            limit,
            work,
            settings,
            event,
        )
        final.extend(retried)
        retries.append(
            {
                "block_ids": [b["id"] for b in subset],
                "merged_id": merged["id"],
                "reason": merged["reason"],
                "original_boundary_violations": [
                    v for b in subset for v in b["native_boundary_violations"]
                ],
            }
        )
    diag = {
        "pipeline": "verified_asr_blocks_tifa",
        "boundary_policy": POLICY,
        "block_policy": PROFILE,
        "block_plan": plan,
        "alignment_blocks": [b for b, _ in final],
        "seam_retries": retries,
        "seam_adjustments": adjustments,
        "asr_used": True,
        "primary_unit_model": "tifa",
        "seam_local_retries": seam_local_retries,
        "deferred_seams": deferred_seams,
        "recognition_runtime": {
            k: recognition.get(k)
            for k in (
                "model",
                "timestamp_model",
                "asr_elapsed",
                "tifa_timing_elapsed",
                "elapsed",
                "peak_gpu_gib",
                "chunk_seconds",
                "overlap_seconds",
            )
        },
        "warnings": [warning] if warning else [],
        "unit_sources": {},
        "effective_audio_bounds": [origin, limit],
    }
    if not plan["boundaries"] and limit - origin > 65:
        diag["warnings"].append(
            "ASR 原文可能有正确歌词，但未得到通过双窗口句子归属与时间核验的切点，本次仍为整段 TIFA；未声称细分成功。"
        )
    adaptive = plan["adaptive_relaxation"]
    adaptive["final_blocks_after_seam_retries"] = len(final)
    adaptive["final_target_met"] = len(final) >= adaptive["minimum_blocks"]
    if max_input_seconds is not None:
        actual = [b["audio_end"] - b["audio_start"] for b, _ in results + final]
        adaptive["max_actual_tifa_input_seconds"] = max(actual)
        adaptive["final_target_met"] = max(actual) < max_input_seconds
        assert adaptive["final_target_met"], "实际TIFA输入超过实验上限"
    if adaptive["relaxations_used"]:
        diag["warnings"].append(
            f"切片条件已放宽 {adaptive['relaxations_used']}/{adaptive['max_relaxations']} 次；当前 {len(plan['blocks'])} 块。新增切点的核验级别已记录，仍需试听接缝。"
        )
    if not adaptive["target_met"]:
        diag["warnings"].append(
            "达到两次放宽上限后，切片数仍不足分钟数目标；保留实际可用切点与全部歌词，不强制合成时间。"
        )
    elif not adaptive["final_target_met"]:
        diag["warnings"].append(
            "规划切片数已达到分钟数目标，但接缝核验后合并使最终块数减少；最终数量已单独记录，不恢复存在冲突的切点。"
        )
    if deferred_seams:
        diag["warnings"].append(
            f"{len(deferred_seams)} 处接缝保留待核验，已局部重试；未合成超过60秒的推理输入。歌词仍完整保留。"
        )
    for block, result in final:
        ids = {u.id for line in result.lines for u in line.units}
        diag["unit_sources"].update(
            {
                uid: source
                for uid, source in result.diagnostics["unit_sources"].items()
                if uid in ids
            }
        )
        if block["over_target"]:
            diag["warnings"].append(
                f"大块 {block['id']} 长 {block['duration']:.1f} 秒；缺少可信切点或接缝需合并，未强制按一分钟切割。"
            )
    timeline = quality(
        Timeline(
            duration=duration,
            origin=origin,
            lines=[l for _, r in final for l in r.lines],
            diagnostics=diag,
        )
    )
    assert [l.id for l in timeline.lines] == [l.id for l in lines]
    return timeline
