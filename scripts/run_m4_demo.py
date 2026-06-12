"""M4 演示：伏笔台账 + 张弛曲线 + 弧线/代价追踪 + 伏笔诚实性闸门。

运行：python scripts/run_m4_demo.py
在 M3 的剪辑流程上补齐"好故事"的完成度：埋/收伏笔、张弛有度、背景滴灌、
收尾前过诚实性闸门，并给出弧线/代价完成度报告。

默认离线确定性（无需 key）；设 DEEPSEEK_API_KEY 则散文由 DeepSeek 渲染。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from novel_engine.config import load_llm_config  # noqa: E402
from novel_engine.llm import build_client  # noqa: E402
from novel_engine.monitors import story_completeness  # noqa: E402
from novel_engine.narration.editor import Editor  # noqa: E402
from novel_engine.narration.foreshadow import (  # noqa: E402
    backfill_payoff_beats,
    finalize_ending,
    honesty_gate,
)
from novel_engine.seed import THEME, seed_m4  # noqa: E402

LINE = "=" * 64


def main() -> None:
    cfg = load_llm_config()
    repo = seed_m4(ticks=4)
    llm = build_client(cfg) if (cfg.provider == "deepseek" and cfg.has_key) else None

    print(LINE)
    print("M4 演示 · 伏笔台账 / 张弛曲线 / 弧线代价追踪 / 诚实性闸门")
    print(LINE)

    editor = Editor(
        repo, llm=llm, theme=THEME, threshold=0.5, reveal_budget=1,
        tension=True, foreshadows=True, exposition=True, max_consecutive_high=1,
    )
    renders = editor.run()

    # ---------- 证据 1：张弛曲线 + 喘息 ----------
    print("\n【证据 1 · 张弛曲线】高潮之间插入喘息场景，背景在喘息里慢慢渗。")
    print("  话语序　目标张力　角色　　POV")
    for r in renders:
        bar = "█" * int(round(r.scene.target_tension * 10))
        print(f"   #{r.scene.discourse_order}     {r.scene.target_tension:<5}{bar:<11}"
              f"{r.role:<9}{r.scene.pov}")
    if editor.tension_alarms:
        print("  ⚠ " + "；".join(editor.tension_alarms))

    # ---------- 证据 2：伏笔台账（plant → payoff） ----------
    print(f"\n{LINE}\n【证据 2 · 伏笔台账】埋下→指向真实 fact→在喘息场景被背景滴灌回收。")
    for fs in repo.list_foreshadows():
        pay = f"，第{fs.payoff_discourse_pos}场回收" if fs.payoff_discourse_pos else ""
        print(f"  [{fs.status:8}] 「{fs.question}」 → fact={fs.linked_fact_id}"
              f"（埋于第{fs.planted_discourse_pos}场{pay}）")

    # ---------- 证据 3：背景渗透写入读者账本 ----------
    print(f"\n{LINE}\n【证据 3 · 背景滴灌】开篇零铺垫；背景只在条件满足时渗入读者账本。")
    for rk in repo.list_reader_knowledge():
        print(f"  [话语#{rk.revealed_discourse_pos} via {rk.via_pov}] {rk.revealed_version}")

    # ---------- 证据 4：伏笔诚实性闸门 ----------
    print(f"\n{LINE}\n【证据 4 · 诚实性闸门】收尾前，must_resolve 伏笔须真相在库且已排回收节拍。")
    rpt = honesty_gate(repo)
    print(f"  收尾前检查：{rpt.summary()}")
    if not rpt.ok:
        created = backfill_payoff_beats(repo)
        print(f"  → 回填回收节拍 {len(created)} 个：" + "、".join(b.beat_id for b in created))
        rpt2 = honesty_gate(repo)
        print(f"  复检：{rpt2.summary()}")
    fin = finalize_ending(repo, "end_truth", auto_backfill=True)
    if fin.ok:
        final = next(e for e in repo.list_endings() if e.status == "final")
        print(f"  ✓ 定稿结局：{final.ending_id} —— {final.summary}")

    # ---------- 证据 5：弧线/代价完成度 ----------
    print(f"\n{LINE}\n【证据 5 · 完成度】每个非静止角色是否被改变、是否付出代价、弱点是否反噬。")
    for row in story_completeness(repo):
        mark = "✓" if row.ok else "·"
        print(f"  {mark} {row.name}：弧线变化={row.arc_changed} 代价={row.cost_count}条 "
              f"弱点反噬={row.flaw_paid}")

    # ---------- 成品片段 ----------
    print(f"\n{LINE}\n【成品 · 小说片段（按话语顺序）】")
    for r in renders:
        tag = "（喘息）" if r.role == "breather" else ""
        print(f"\n  —— 第 {r.scene.discourse_order} 场{tag} POV={r.scene.pov} ——")
        for line in r.scene.prose_text.splitlines():
            print(f"    {line}")
        if r.scene.newly_revealed:
            print(f"    （本场揭示：{r.scene.newly_revealed}）")

    print(f"\n{LINE}")
    print("M4 演示结束。" + ("（散文由 DeepSeek 渲染）" if llm else "（离线模板渲染）"))


if __name__ == "__main__":
    main()
