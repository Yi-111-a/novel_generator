# -*- coding: utf-8 -*-
"""把 agent 亲手写的正文入库（引擎只存结果，不跑 LLM 推进/审计）。

用法：python scripts/ingest_chapter.py <chapter_no> <title> <prose_md_path> [proj_id]
- 正文存进 accepted_chapters（chapter_no/title/prose/summary）
- 对应 chapter_plan.status -> done
- summary 取该章 chapter_plan.summary
- 同步落一条 accepted 状态的 chapter_draft 备查
- 【方案2·知识图谱】入库后跑引擎 FactExtractor，从正文抽事实/事件、增量更新知识图谱
  (graph_edges/events/facts/inventory)。只读正文、不改正文。
  关掉：设环境变量 ZHUTIAN_NO_KG=1（省 token / 离线时）。失败不影响入库。
打印中文字数（CJK 计数）供核对 3000-3600 区间。

章号映射：卷1=accepted chapter_no 1..12 -> part seq1 的 sequence_order=chapter_no；
         卷2=accepted chapter_no 13..58 -> part seq2 的 sequence_order=chapter_no-12。
"""
import json
import os
import re
import sqlite3
import sys
import time
import uuid

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
for p in (ROOT, os.path.join(ROOT, "src")):
    if p not in sys.path:
        sys.path.insert(0, p)


def cjk_count(text: str) -> int:
    return len(re.findall(r"[一-鿿]", text))


def plan_coords(ch_no: int):
    """全书章号 -> (part_seq, 卷内 plan sequence_order)。卷边界：卷1=1..12，卷2=13..58。"""
    if ch_no <= 12:
        return 1, ch_no
    return 2, ch_no - 12


def update_knowledge_graph(db: str, chapter_id: str, prose: str) -> None:
    """方案2：用引擎 FactExtractor 增量更新知识图谱。只读正文、不改正文；任何异常都不影响已完成的入库。"""
    if os.getenv("ZHUTIAN_NO_KG") == "1":
        print("  [KG] 已按 ZHUTIAN_NO_KG=1 跳过知识图谱更新")
        return
    try:
        from server import config_store
        from novel_engine.config import LLMConfig
        from novel_engine.llm import build_client
        from novel_engine.llm.logging_wrapper import LoggingLLMClient
        from novel_engine.db import connect
        from novel_engine.repository import Repository
        from novel_engine.narration.fact_extractor import FactExtractor

        cfg = config_store.load_config()
        key = (cfg.get("llmApiKey") or "").strip()
        if not key:
            print("  [KG] 无 LLM key，跳过（图谱不更新，不影响入库）")
            return

        conn = connect(db, check_same_thread=False)
        repo = Repository(conn)
        raw = build_client(LLMConfig(provider="deepseek", model=cfg["modelName"],
                                     base_url=cfg["baseUrl"], api_key=key))
        llm = LoggingLLMClient(raw, conn, caller="kg_ingest")

        plan = next((p for p in repo.list_chapter_plans() if p.chapter_id == chapter_id), None)
        if plan is None:
            print("  [KG] 未找到章纲，跳过")
            conn.close()
            return

        # 幂等：清掉本章上一次抽取留下的 events/facts（按 beat_id=chapter_id），避免反复入库时事件翻倍。
        # graph_edges 是累计注意力(再 bump 只是略微抬高强度)，无法干净回退，影响可忽略。
        conn.execute("delete from facts where source_event_id in (select event_id from events where beat_id=?)",
                     (chapter_id,))
        conn.execute("delete from events where beat_id=?", (chapter_id,))
        conn.commit()

        edges_before = len(repo.list_edges())
        events_before = len(repo.list_events())
        extractor = FactExtractor(repo, llm)
        spec = type("Spec", (), {"may_reveal": list(plan.reveal_gate or [])})()
        delta = extractor.extract(prose, plan.pov_agent or "", list(plan.cast or []), spec)
        tick = events_before + 1
        with repo.transaction():
            extractor.commit(delta, plan.pov_agent or "", list(plan.cast or []), tick, chapter=plan)
        edges_after = len(repo.list_edges())
        events_after = len(repo.list_events())
        print(f"  [KG] 知识图谱已更新：events +{events_after - events_before}（{events_after}），"
              f"graph_edges +{edges_after - edges_before}（{edges_after}），"
              f"new_facts={len(delta.new_facts)} character_beats={len(delta.character_beats)}")
        conn.close()
    except Exception as e:
        print(f"  [KG] 更新失败（不影响入库）：{type(e).__name__}: {e}")


def main():
    ch_no = int(sys.argv[1])
    title = sys.argv[2]
    prose_path = sys.argv[3]
    pid = sys.argv[4] if len(sys.argv) > 4 else open(
        os.path.join(os.path.dirname(__file__), ".zhutian_new_pid")).read().strip()
    db = os.path.join("server", ".data", "projects", f"{pid}.db")
    prose = open(prose_path, encoding="utf-8").read().strip()

    n = cjk_count(prose)
    print(f"ch{ch_no} 《{title}》 中文字数={n}", "  [OK 3000-3600]" if 3000 <= n <= 3600 else "  [!! 区间外]")

    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    part_seq, plan_seq = plan_coords(ch_no)
    plan = cur.execute("select chapter_id, summary from chapter_plans where sequence_order=?"
                       " and arc_id in (select a.arc_id from arcs a join parts p on a.part_id=p.part_id where p.sequence_order=?)",
                       (plan_seq, part_seq)).fetchone()
    summary = (plan["summary"] if plan else "") or ""
    chapter_id = plan["chapter_id"] if plan else None
    if plan:
        cur.execute("update chapter_plans set status='done' where chapter_id=?", (chapter_id,))

    # 幂等：删旧的同号 accepted / draft
    cur.execute("delete from accepted_chapters where chapter_no=?", (ch_no,))

    cur.execute(
        "insert into chapter_drafts (project_id, chapter_no, title, outline, prose, guidance, target_words,"
        " mode, status, context_snapshot_json, created_at, accepted_at)"
        " values (?,?,?,?,?,?,?,?,?,?,?,?)",
        (pid, ch_no, title, summary, prose, "agent-handwritten", n, "manual", "accepted",
         json.dumps({"source": "agent_handwritten"}, ensure_ascii=False), int(time.time()), int(time.time())),
    )
    draft_id = cur.lastrowid

    cur.execute(
        "insert into accepted_chapters (project_id, draft_id, chapter_no, title, prose, summary, created_at)"
        " values (?,?,?,?,?,?,?)",
        (pid, draft_id, ch_no, title, prose, summary, int(time.time())),
    )
    con.commit()
    total = cur.execute("select count(*) from accepted_chapters").fetchone()[0]
    con.close()
    print(f"入库完成 accepted_chapters: ch{ch_no} draft_id={draft_id}; chapter_plan -> done; accepted total={total}")

    # 方案2：入库后增量更新知识图谱（独立连接，失败不影响上面已提交的入库）
    if chapter_id:
        update_knowledge_graph(db, chapter_id, prose)


if __name__ == "__main__":
    main()
