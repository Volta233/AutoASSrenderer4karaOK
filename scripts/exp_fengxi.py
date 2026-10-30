"""实验：验证《风兮风兮》的切分失败是歌词缺口还是切分规则过严。

只跑规划阶段（复用已保存的 ASR 结果），不做任何推理。
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ktv_app.timeline import Line
from ktv_app.aligners.block_planning import build_duration_limited_plan

ROOT = Path("ktv_projects/55b6124599034764a1c34a5eeb91aaf2")
RUN = "4d38b3bbce9c"


def load():
    lines = json.loads((ROOT / "lines.json").read_text(encoding="utf-8"))
    rec = json.loads(
        (ROOT / "alignment" / RUN / "locator" / "recognition.json").read_text(
            encoding="utf-8"
        )
    )
    return [l["text"] for l in lines], rec


def run(name, texts, rec):
    lines = [Line(id=str(i), text=t, language="zh") for i, t in enumerate(texts)]
    plan, _, _ = build_duration_limited_plan(
        lines,
        rec["tokens"],
        rec["verification_tokens"],
        0.0,
        rec["duration"],
        None,
        lambda m: None,
        max_input_seconds=90,
    )
    a = plan["adaptive_relaxation"]
    print(f"== {name}: {len(texts)} 行")
    print(
        "   blocks",
        len(plan["blocks"]),
        "candidates",
        len(plan["candidate_boundaries"]),
        "met",
        a["target_met"],
        "longest",
        round(max(at["max_input_seconds"] for at in a["attempts"]), 1),
    )
    for at in a["attempts"]:
        print(
            "   lvl%d cand %d blocks %d longest %.1f"
            % (
                at["level"],
                at["candidates"],
                at["blocks"],
                at["max_input_seconds"],
            )
        )
    return plan


def main():
    base, rec = load()
    run("E1 原始（21 行，末行被截断，无重复副歌）", base, rec)

    completed_last = "鲲鹏扶摇九万里"
    e2 = ["冬雪语若柳絮飞"] + base[:20] + [
        completed_last if t.startswith("鲲鹏扶摇") else t for t in base[20:]
    ]
    run("E2 补首句 + 补全末行（22 行）", e2, rec)

    # 音频实际把主副歌整体重复了一遍，真实歌词文件应有这一段。
    chorus = e2[4:18]
    e3 = e2 + chorus
    plan = run("E3 E2 + 重复副歌（36 行，相当于完整歌词）", e3, rec)

    print("\nE3 分块：")
    for b in plan["blocks"]:
        print(
            "  ",
            b["id"],
            round(b["core_start"], 1),
            "-",
            round(b["core_end"], 1),
            "=%.1fs" % b["duration"],
            "lines",
            b["indices"][0],
            "-",
            b["indices"][-1],
        )


if __name__ == "__main__":
    main()
