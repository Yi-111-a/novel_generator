"""故事时钟·一次性回填：给**已有项目**的已接受章节补抽时间线（events.story_clock +
project_meta.timeline），让新加的时钟硬约束能对老项目生效。

用法：
  PYTHONPATH=src python scripts/backfill_story_clock.py server/.data/projects/proj_31157567.db [--replan 13]

回填是纯附加（只写 timeline_json / events.story_clock）；--replan N 会对未写的第 N 章
重新规划（套用时间硬约束 + 写入 time_hint）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from novel_engine import db  # noqa: E402
from novel_engine.llm.factory import build_client  # noqa: E402
from novel_engine.config import LLMConfig  # noqa: E402
from novel_engine.narration.story_clock import capture_story_clock, fold_timeline  # noqa: E402
from novel_engine.planner import Planner  # noqa: E402
from novel_engine.repository import Repository  # noqa: E402


def _load_llm():
    cfg_path = Path("server/.data/config.json")
    cfg = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}
    key = cfg.get("llmApiKey") or ""
    if not key:
        print("[warn] 无 llmApiKey，使用正则兜底（不调用 LLM）")
        return None
    return build_client(LLMConfig(provider="deepseek", model=cfg.get("modelName", "deepseek-chat"),
                                  base_url=cfg.get("baseUrl", "https://api.deepseek.com"), api_key=key))


def main() -> None:
    db_path = sys.argv[1]
    replan_seq = None
    if "--replan" in sys.argv:
        replan_seq = int(sys.argv[sys.argv.index("--replan") + 1])

    llm = _load_llm()
    repo = Repository(db.connect(db_path, check_same_thread=False))
    plans = {p.sequence_order: p for p in repo.list_chapter_plans()}
    accepted = sorted(repo.list_accepted_chapters(), key=lambda a: a.chapter_no)

    print(f"[backfill] {len(accepted)} 章待回填时间线 …")
    for a in accepted:
        plan = plans.get(a.chapter_no)
        if plan is None:
            continue
        entry = capture_story_clock(repo, plan, a.prose, llm, event_ids=[])
        if entry:
            print(f"  ch{a.chapter_no}: end={entry['end_clock_text'] or '—'} "
                  f"deadlines={[d['label'] + ('@' + d['due_text'] if d.get('due_text') else '') for d in entry['deadlines']]}")
        else:
            print(f"  ch{a.chapter_no}: （无可结构化时间）")

    folded = fold_timeline(repo.get_story_timeline())
    print("\n[timeline] 末时间:", folded["last_end_text"] or "—",
          "| 活跃死线:", [f"{d['label']}@{d.get('due_text','?')}" for d in folded["active_deadlines"]])

    if replan_seq is not None:
        ch = next((p for p in repo.list_chapter_plans() if p.sequence_order == replan_seq), None)
        if ch is None:
            print(f"[replan] 找不到第 {replan_seq} 章")
            return
        if ch.status == "done" or ch.audited:
            print(f"[replan] 第 {replan_seq} 章已写定/已审，跳过（replan 只作用于未写章）")
            return
        planner = Planner(repo, llm)
        print(f"\n[replan] 重新规划第 {replan_seq} 章（套用时间硬约束）…")
        new = planner.replan_chapter(ch.chapter_id)
        if new is None:
            print("[replan] 失败/不可重规划")
            return
        print(f"  time_hint = {new.time_hint!r}")
        print(f"  地点 = {new.location_ids}")
        for i, b in enumerate(new.beat_goals):
            print(f"  beat{i+1}: {b}")
        print(f"  exit_state = {new.exit_state}")


if __name__ == "__main__":
    main()
