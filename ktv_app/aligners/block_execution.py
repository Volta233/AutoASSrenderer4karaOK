"""Run TIFA per planned block and retain only its owned native word times."""

from ..media import crop
from ..timeline import quality
from .anchor_policy import clear, scaffold, run_anchor_alignment
from .common import blank
from .block_planning import context_job


def run_blocks(
    audio, lines, blocks, first, second, duration, origin, limit, work, settings, event
):
    results = []
    for index, source in enumerate(blocks):
        block = dict(source)
        block.update(context_job(block, lines, first, second, origin, limit))
        folder = work / f"block_{block['id']}"
        folder.mkdir(parents=True, exist_ok=True)
        crop(
            audio,
            folder / "audio.wav",
            block["audio_start"] - origin,
            block["audio_end"] - origin,
            settings,
        )
        event(
            f"TIFA 大块 {index + 1}/{len(blocks)}：{block['core_start']:.2f}–{block['core_end']:.2f} 秒；歌词 {block['indices'][0] + 1}–{block['indices'][-1] + 1} 行"
        )
        result = run_anchor_alignment(
            folder / "audio.wav",
            blank([lines[i] for i in block["context_indices"]]),
            duration,
            block["audio_start"],
            folder,
            settings,
            lambda message: event(
                "块 "
                + block["id"]
                + "："
                + message.replace("TIFA 全曲对齐", "TIFA 块内对齐")
            ),
        )
        owned = set(block["line_ids"])
        result.lines = [line for line in result.lines if line.id in owned]
        block["boundary_native_lines"] = [
            line.model_dump() for line in (result.lines[:1] + result.lines[-1:])
        ]
        violations = []
        for line in result.lines:
            scaffold(line)
            for unit in line.units:
                if unit.start is not None and (
                    unit.start < block["core_start"] - 0.02
                    or unit.end > block["core_end"] + 0.02
                ):
                    violations.append(
                        {
                            "line_id": line.id,
                            "unit_id": unit.id,
                            "start": unit.start,
                            "end": unit.end,
                            "native_unit": unit.model_dump(),
                            "reason": "outside_verified_block_ownership",
                        }
                    )
                    clear(unit)
        result = quality(result)
        block["native_boundary_violations"] = violations
        block["diagnostics"] = result.diagnostics
        block["effective_audio_bounds"] = [block["core_start"], block["core_end"]]
        (folder / "owned_timeline.json").write_text(
            result.model_dump_json(indent=2), encoding="utf-8"
        )
        results.append((block, result))
    return results
