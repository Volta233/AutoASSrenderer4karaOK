"""临时诊断脚本：打印某项目每行歌词的双遍匹配证据与切点判定。

用法: python scripts/diag_cuts.py <project_dir> <alignment_run_id>
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ktv_app.timeline import Line
from ktv_app.aligners.boundary_matching import (
    match_boundary_lines,
    strong_match,
)
from ktv_app.aligners.block_planning import build_duration_limited_plan


def main(project, run):
    root = Path(project)
    lines = [
        Line(id=str(i), text=t["text"], language=t.get("language", "zh"))
        for i, t in enumerate(
            json.loads((root / "lines.json").read_text(encoding="utf-8"))
        )
    ]
    rec = json.loads(
        (root / "alignment" / run / "locator" / "recognition.json").read_text(
            encoding="utf-8"
        )
    )
    tokens = rec["tokens"]
    ver = rec["verification_tokens"]
    print("tokens", len(tokens), "verification", len(ver))
    lower, upper = 0.0, rec["duration"]
    first = match_boundary_lines(lines, tokens, lower)
    second = match_boundary_lines(lines, ver, lower)
    f = {m["line_index"]: m for m in first}
    s = {m["line_index"]: m for m in second}
    print(f"{'i':>3} {'line':<14} {'p1':<38} {'p2':<38}")
    for i, line in enumerate(lines):

        def cell(m):
            if not m:
                return "MISSING"
            return "cov%.2f sim%.2f %s%s" % (
                m["coverage"],
                m["similarity"],
                "P" if m["prefix_confirmed"] else "-",
                "S" if m["suffix_confirmed"] else "-",
            )

        print(f"{i:>3} {line.text:<14} {cell(f.get(i)):<38} {cell(s.get(i)):<38}")

    print("\n--- 弱匹配（即使放宽也不视为完整句）---")
    for i, line in enumerate(lines):
        for name, m in (("p1", f.get(i)), ("p2", s.get(i))):
            if m and not strong_match(line, m):
                print(f"{i} {name} {line.text} <= {m['recognized_text']}")

    plan, first, second = build_duration_limited_plan(
        lines,
        tokens,
        ver,
        lower,
        upper,
        None,
        lambda msg: print("[event]", msg),
        max_input_seconds=90,
    )
    for at in plan["adaptive_relaxation"]["attempts"]:
        print(
            "level",
            at["level"],
            "cand",
            at["candidates"],
            "blocks",
            at["blocks"],
            "max_input",
            round(at["max_input_seconds"], 2),
        )


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
