"""§13.2 章末钩子：每章生成 ending_hook + hook_type，且持久化、按 role 取型、可链式接钩。"""
from __future__ import annotations

from novel_engine import db
from novel_engine.models import Entity, Fact, Foreshadow, KnowledgeItem, Persona
from novel_engine.planner import _HOOK_TYPE, Planner
from novel_engine.repository import Repository


def _seed_repo() -> Repository:
    r = Repository(db.connect(":memory:"))
    r.insert_entity(Entity("loc_main", "location", "主场景", {}))
    for aid, name in [("hero", "云鹤子"), ("ally", "季拾遗"), ("villain", "墨渊")]:
        r.insert_entity(Entity(aid, "character", name, {}))
        r.insert_persona(Persona(agent_id=aid, name=name, want="求道"))
    r.append_fact(Fact("f_secret", "state", "旧案关键握在墨渊手里。", involved_entities=["villain"]))
    r.insert_knowledge(KnowledgeItem("villain", "f_secret", "旧案关键握在墨渊手里。", 1.0, 0))
    r.upsert_foreshadow(Foreshadow("fs_secret", "墨渊瞒着什么？", "f_secret", 1, True))
    return r


def test_every_chapter_has_hook_and_type():
    r = _seed_repo()
    p = Planner(r, llm=None, theme="证道")
    p.build_master(part_count=3, arcs_per_part=1)
    for _ in range(10):
        if p.next_chapter() is None:
            break
    chs = r.list_chapter_plans()
    assert chs
    for c in chs:
        assert c.ending_hook, f"章 {c.sequence_order} 缺章末钩子"
        assert c.hook_type == _HOOK_TYPE.get(c.role, "new_question")


def test_hook_persisted_roundtrip():
    r = _seed_repo()
    p = Planner(r, llm=None, theme="证道")
    p.build_master(part_count=3, arcs_per_part=1)
    ch = p.next_chapter()
    again = r.get_chapter_plan(ch.chapter_id)
    assert again.ending_hook == ch.ending_hook and again.hook_type == ch.hook_type


def test_climax_is_cliffhanger():
    r = _seed_repo()
    p = Planner(r, llm=None, theme="证道")
    p.build_master(part_count=3, arcs_per_part=1)
    arc = p.plan_next_arc()
    while len(r.list_chapter_plans(arc.arc_id)) < arc.target_chapters:
        p.next_chapter()
    climax = next(c for c in r.list_chapter_plans(arc.arc_id) if c.role == "climax")
    assert climax.hook_type == "cliffhanger"


def test_next_chapter_goal_carries_prev_hook_offline():
    # 离线回退路径：第二章目标应包含"回应上一章悬念"的措辞
    r = _seed_repo()
    p = Planner(r, llm=None, theme="证道")
    p.build_master(part_count=3, arcs_per_part=1)
    c1 = p.next_chapter()
    c1.status = "done"
    r.upsert_chapter_plan(c1)
    c2 = p.next_chapter()
    assert "上一章" in c2.beat_goals[0]
