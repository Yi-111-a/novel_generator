"""§11 开拍前全量章纲：build_full_outline 一次生成全部 Arc + 章；首部 active 其余 planned。"""
from __future__ import annotations

from novel_engine import db
from novel_engine.models import Entity, Fact, Foreshadow, KnowledgeItem, Persona
from novel_engine.planner import Planner
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


def test_build_full_outline_generates_all_chapters():
    r = _seed_repo()
    p = Planner(r, llm=None, theme="证道", story_scale="short")
    p.build_master(part_count=3, arcs_per_part=2)
    info = p.build_full_outline()
    arcs = r.list_arcs()
    chapters = r.list_chapter_plans()
    # 每部 2 段 × 3 部 = 6 段
    assert len(arcs) == 6 and info["arcs"] == 6
    # 章数 = 各 Arc target_chapters 之和
    assert len(chapters) == sum(a.target_chapters for a in arcs)
    assert info["chapters"] == len(chapters)


def test_chapter_sequence_orders_contiguous():
    r = _seed_repo()
    p = Planner(r, llm=None, theme="证道")
    p.build_master(part_count=3, arcs_per_part=2)
    p.build_full_outline()
    seqs = sorted(c.sequence_order for c in r.list_chapter_plans())
    assert seqs == list(range(1, len(seqs) + 1))  # 1..N 连续不重号


def test_first_part_active_rest_planned():
    r = _seed_repo()
    p = Planner(r, llm=None, theme="证道")
    p.build_master(part_count=3, arcs_per_part=1)
    p.build_full_outline()
    parts = r.list_parts()
    assert parts[0].status == "active"
    assert all(pt.status == "planned" for pt in parts[1:])  # 尚未抵达 → provisional


def test_every_chapter_has_full_contract():
    r = _seed_repo()
    p = Planner(r, llm=None, theme="证道")
    p.build_master(part_count=3, arcs_per_part=1)
    p.build_full_outline()
    for c in r.list_chapter_plans():
        assert c.beat_goals and c.dramatic_question and c.ending_hook
        assert c.hook_type and c.target_words >= 1200
        assert c.role in ("setup", "rising", "twist", "climax", "resolution")
