"""§13.1 道具台账：只允许节拍点名或前章实际继承的道具。"""
from __future__ import annotations

from novel_engine import db
from novel_engine.models import Entity, Fact, Foreshadow, InventoryItem, KnowledgeItem, Persona
from novel_engine.planner import Planner
from novel_engine.repository import Repository


def _seed_repo() -> Repository:
    r = Repository(db.connect(":memory:"))
    r.insert_entity(Entity("loc_main", "location", "主场景", {}))
    for aid, name, motif in [("hero", "云鹤子", ["obj_compass"]), ("ally", "季拾遗", ["obj_scroll"]),
                             ("villain", "墨渊", [])]:
        r.insert_entity(Entity(aid, "character", name, {}))
        for o in motif:
            r.insert_entity(Entity(o, "object", o, {}))
        r.insert_persona(Persona(agent_id=aid, name=name, want="求道", motif_objects=motif))
    r.append_fact(Fact("f_secret", "state", "旧案关键握在墨渊手里。", involved_entities=["villain"]))
    r.insert_knowledge(KnowledgeItem("villain", "f_secret", "旧案关键握在墨渊手里。", 1.0, 0))
    r.upsert_foreshadow(Foreshadow("fs_secret", "墨渊瞒着什么？", "f_secret", 1, True))
    return r


def test_cast_motif_holdings_are_candidates_not_automatic_scene_items():
    r = _seed_repo()
    p = Planner(r, llm=None, theme="证道")
    p.build_master(part_count=3, arcs_per_part=1)
    ch = p.next_chapter()
    # motif/inventory 只是候选池；当前 beat 没点名就不得自动入场。
    for aid in ch.cast:
        for it in r.items_held_by(aid):
            assert it.object_id not in ch.items_present
    # 持久化往返一致
    again = r.get_chapter_plan(ch.chapter_id)
    assert again.items_present == ch.items_present
    assert again.items_introduced == ch.items_introduced


def test_introduced_items_have_source_in_inventory():
    r = _seed_repo()
    p = Planner(r, llm=None, theme="证道")
    p.build_master(part_count=3, arcs_per_part=1)
    ch = p.next_chapter()
    # 新登场道具必须能在库存里指明来源（cast 持有），不得凭空
    holders = {it.object_id for aid in ch.cast for it in r.items_held_by(aid)}
    for obj in ch.items_introduced:
        assert obj in holders


def test_items_present_inherits_unconsumed_from_prev_chapter():
    r = _seed_repo()
    p = Planner(r, llm=None, theme="证道")
    p.build_master(part_count=3, arcs_per_part=1)
    c1 = p.next_chapter()
    # 模拟第一章消耗掉一件道具
    consumed = c1.items_present[0] if c1.items_present else None
    if consumed:
        c1.items_consumed = [consumed]
    c1.status = "done"
    r.upsert_chapter_plan(c1)

    c2 = p.next_chapter()
    # 被消耗的道具不应再继承进下一章在场台账
    if consumed:
        carried_from_prev = [o for o in c1.items_present if o != consumed]
        for o in carried_from_prev:
            assert o in c2.items_present
        assert consumed not in c2.items_present or any(  # 除非本章 cast 又重新携带它
            r.agent_holds(a, consumed) for a in c2.cast)


def test_items_present_does_not_carry_destroyed_or_sacrificed_items():
    r = _seed_repo()
    p = Planner(r, llm=None, theme="证道")
    p.build_master(part_count=3, arcs_per_part=1)
    c1 = p.next_chapter()
    doomed = r.items_held_by("hero")[0].object_id
    c1.items_present = [doomed]
    r.transfer_item(doomed, None, c1.sequence_order, status="sacrificed", note="used up")
    c1.status = "done"
    r.upsert_chapter_plan(c1)

    c2 = p.next_chapter()
    assert doomed not in c2.items_present
