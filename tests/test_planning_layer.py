"""P1 数据层：规划层（parts/arcs/chapter_plans/inventory/reveal_chain）的仓储读写。

只验证持久化与查询的正确性；规划器/导演接入在 P2/P3。
"""
from __future__ import annotations

from novel_engine import db
from novel_engine.models import Arc, ChapterPlan, InventoryItem, Part, RevealNode
from novel_engine.repository import Repository


def _repo() -> Repository:
    return Repository(db.connect(":memory:"))


def test_parts_roundtrip_and_order():
    r = _repo()
    r.upsert_part(Part("p2", 2, title="风暴之眼", goal="揭开第一层真相", region="北域"))
    r.upsert_part(Part("p1", 1, title="入局", goal="主角回到风暴中心", region="南境",
                       reveal_node_ids=["n1", "n2"]))
    parts = r.list_parts()
    assert [p.part_id for p in parts] == ["p1", "p2"]  # 按 sequence_order
    assert parts[0].reveal_node_ids == ["n1", "n2"]
    r.set_part_status("p1", "active")
    assert r.get_part("p1").status == "active"


def test_arcs_scoped_to_part_and_focus_weights():
    r = _repo()
    r.upsert_part(Part("p1", 1))
    r.upsert_arc(Arc("a1", "p1", 1, title="拜师", target_chapters=6,
                     focus_agents=[{"agent_id": "hero", "weight": 0.8}]))
    r.upsert_arc(Arc("a2", "p1", 2, title="配角的过往",
                     focus_agents=[{"agent_id": "side", "weight": 0.7}]))  # 主讲配角
    arcs = r.list_arcs("p1")
    assert [a.arc_id for a in arcs] == ["a1", "a2"]
    assert arcs[1].focus_agents[0]["agent_id"] == "side"
    assert r.get_arc("a1").target_chapters == 6


def test_chapter_plan_constraints_and_active_selection():
    r = _repo()
    r.upsert_arc(Arc("a1", "p1", 1))
    r.upsert_chapter_plan(ChapterPlan(
        "c1", "a1", 1, cast=["hero", "side"], location_ids=["loc_inn"],
        available_items=["obj_sword"], beat_goals=["主角察觉异常"],
        reveal_gate=["f_clue1"], knowledge_delta={"hero": ["f_clue1"], "reader": ["f_clue1"]},
        target_scenes=3, status="done",
    ))
    r.upsert_chapter_plan(ChapterPlan("c2", "a1", 2, cast=["hero"], status="planned"))
    c1 = r.get_chapter_plan("c1")
    assert c1.cast == ["hero", "side"] and c1.available_items == ["obj_sword"]
    assert c1.knowledge_delta["reader"] == ["f_clue1"]
    # c1 已 done，active_chapter_plan 应回退到最早的 planned = c2
    assert r.active_chapter_plan().chapter_id == "c2"


def test_inventory_transfer_and_lose():
    r = _repo()
    r.set_inventory(InventoryItem("obj_sword", "hero", "held", acquired_chapter=1))
    assert r.agent_holds("hero", "obj_sword")
    assert [i.object_id for i in r.items_held_by("hero")] == ["obj_sword"]
    # 转给别人
    r.transfer_item("obj_sword", "villain", chapter=5, note="被夺走")
    assert not r.agent_holds("hero", "obj_sword")
    assert r.agent_holds("villain", "obj_sword")
    # 彻底消失
    r.transfer_item("obj_sword", None, chapter=8, note="坠入深渊")
    assert r.get_inventory_item("obj_sword").status == "lost"
    assert r.items_held_by("villain") == []


def test_reveal_chain_unlock_gating():
    r = _repo()
    # n1 -> n2 -> n3(truth)
    r.upsert_reveal_node(RevealNode("n1", fact_id="f1", kind="clue", sequence_order=1))
    r.upsert_reveal_node(RevealNode("n2", fact_id="f2", kind="clue", sequence_order=2,
                                    prereq_node_ids=["n1"]))
    r.upsert_reveal_node(RevealNode("n3", fact_id="f3", kind="truth", sequence_order=3,
                                    prereq_node_ids=["n2"]))
    # 一开始只有 n1 可解锁（无前置）
    assert [n.node_id for n in r.unlockable_nodes()] == ["n1"]
    r.mark_node_discovered("n1", chapter=2)
    assert [n.node_id for n in r.unlockable_nodes()] == ["n2"]  # n3 仍被 n2 挡住
    r.mark_node_discovered("n2", chapter=4)
    assert [n.node_id for n in r.unlockable_nodes()] == ["n3"]
    assert r.get_reveal_node("n1").discovered_chapter == 2
