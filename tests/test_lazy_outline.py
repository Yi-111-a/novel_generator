"""惰性大纲：锁定只生成总体大纲(全 Arc 骨架)+第一部章纲；后续部演到时再生成。"""
from __future__ import annotations

from novel_engine import db
from novel_engine.models import Entity, Persona
from novel_engine.planner import Planner
from novel_engine.repository import Repository


def _repo() -> Repository:
    r = Repository(db.connect(":memory:"))
    for aid, nm in [("hero", "沈砚"), ("ally", "赵九")]:
        r.insert_entity(Entity(aid, "character", nm, {}))
        r.insert_persona(Persona(agent_id=aid, name=nm, want="x",
                                 values=[{"name": "v", "weight": 0.5}]))
    return r


def test_lazy_only_builds_first_part_chapters():
    r = _repo()
    p = Planner(r, llm=None, theme="x")
    p.build_master(part_count=3, arcs_per_part=1)
    info = p.build_lazy_outline()
    parts = r.list_parts()
    # 全部 Part 都有 Arc 骨架
    assert all(r.list_arcs(pt.part_id) for pt in parts)
    # 只有第一部有章纲
    first_chs = sum(len(r.list_chapter_plans(a.arc_id)) for a in r.list_arcs(parts[0].part_id))
    later_chs = sum(len(r.list_chapter_plans(a.arc_id))
                    for pt in parts[1:] for a in r.list_arcs(pt.part_id))
    assert first_chs > 0 and later_chs == 0
    assert info["chapters"] == first_chs


def test_ensure_part_chapters_builds_on_demand():
    r = _repo()
    p = Planner(r, llm=None, theme="x")
    p.build_master(part_count=3, arcs_per_part=1)
    p.build_lazy_outline()
    parts = r.list_parts()
    made = p.ensure_part_chapters(parts[1].part_id)  # 演到第二部
    assert made > 0
    # 幂等：再调不重复生成
    assert p.ensure_part_chapters(parts[1].part_id) == 0


def test_lazy_builds_only_first_arc_when_multi_arc():
    """粒度=第一个 Arc：第一部有多个 Arc 时，锁定只生成第一个 Arc 的章纲。"""
    r = _repo()
    p = Planner(r, llm=None, theme="x")
    p.build_master(part_count=2, arcs_per_part=2)
    p.build_lazy_outline()
    arcs0 = r.list_arcs(r.list_parts()[0].part_id)
    assert len(arcs0) == 2
    c0 = len(r.list_chapter_plans(arcs0[0].arc_id))  # 第一个 Arc：有章
    c1 = len(r.list_chapter_plans(arcs0[1].arc_id))  # 第二个 Arc：暂无（边写边补）
    assert c0 > 0 and c1 == 0
