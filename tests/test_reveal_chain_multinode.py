"""§13.4 揭示链强制多节点：单点秘密被拆成 线索→线索→子真相→核心真相，且 ≥1 条副线谜团。"""
from __future__ import annotations

from novel_engine import db
from novel_engine.models import Entity, Fact, Foreshadow, KnowledgeItem, Persona
from novel_engine.planner import Planner
from novel_engine.repository import Repository


def _single_secret_repo() -> Repository:
    """根因 A 场景：种子只有 1 条 secret（villain 独握）。"""
    r = Repository(db.connect(":memory:"))
    r.insert_entity(Entity("loc_main", "location", "主场景", {}))
    for aid, name in [("hero", "云鹤子"), ("ally", "季拾遗"), ("villain", "墨渊")]:
        r.insert_entity(Entity(aid, "character", name, {}))
        r.insert_persona(Persona(agent_id=aid, name=name, want=f"{name}的执念"))
    r.append_fact(Fact("f_secret", "state", "旧案关键握在墨渊手里。", involved_entities=["villain"]))
    r.insert_knowledge(KnowledgeItem("villain", "f_secret", "旧案关键握在墨渊手里。", 1.0, 0))
    r.upsert_foreshadow(Foreshadow("fs_secret", "墨渊瞒着什么？", "f_secret", 1, True))
    return r


def test_single_secret_expands_to_four_node_chain():
    r = _single_secret_repo()
    Planner(r, llm=None, theme="证道").build_master(part_count=3)
    nodes = r.list_reveal_nodes()
    # 唯一核心真相
    truths = [n for n in nodes if n.kind == "truth"]
    assert len(truths) == 1 and truths[0].fact_id == "f_secret"
    # 核心真相的前置链至少 3 层（线索→线索→子真相），即整条 ≥4 节点
    def depth(node) -> int:
        d, cur = 1, node
        seen = set()
        while cur.prereq_node_ids and cur.prereq_node_ids[0] not in seen:
            seen.add(cur.node_id)
            nxt = next((x for x in nodes if x.node_id == cur.prereq_node_ids[0]), None)
            if nxt is None:
                break
            d += 1
            cur = nxt
        return d
    assert depth(truths[0]) >= 4, "核心真相应有 线索→线索→子真相 三层前置"
    # 子真相存在（clue 节点，描述含"子真相"）
    assert any(n.kind == "clue" and "子真相" in n.description for n in nodes)


def test_subplot_mystery_present():
    r = _single_secret_repo()
    Planner(r, llm=None, theme="证道").build_master(part_count=3)
    nodes = r.list_reveal_nodes()
    # ≥1 条副线谜团（从未握核心真相的配角 ally 派生）
    assert any("副线" in n.description for n in nodes)


def test_all_reveal_nodes_assigned_to_parts():
    r = _single_secret_repo()
    Planner(r, llm=None, theme="证道").build_master(part_count=3)
    nodes = r.list_reveal_nodes()
    assert nodes and all(n.part_id for n in nodes)
    # reveal_gate 只可能落在带 fact_id 的真相节点（副线/子真相不 gate）
    gateable = [n for n in nodes if n.fact_id]
    assert all(n.kind == "truth" for n in gateable)
