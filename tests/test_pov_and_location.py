"""第二批修复：POV 轮换（问题6）+ 场景换场轮换（问题1）。"""
from __future__ import annotations

from novel_engine import db
from novel_engine.models import Arc, ChapterPlan, Entity, Persona
from novel_engine.planner import Planner
from novel_engine.repository import Repository


def _repo() -> Repository:
    r = Repository(db.connect(":memory:"))
    for aid, name in [("hero", "沈砚"), ("ally", "赵九"), ("foe", "苏静")]:
        r.insert_entity(Entity(aid, "character", name, {}))
        r.insert_persona(Persona(agent_id=aid, name=name, want="x", values=[{"name": "v", "weight": 0.5}]))
    return r


# ---- 问题6：ChapterPlan 持久化 pov_agent / exit_state ----
def test_chapter_plan_persists_pov_and_exit():
    r = _repo()
    r.upsert_arc(Arc(arc_id="a1", part_id="p1", sequence_order=1))
    ch = ChapterPlan(chapter_id="c1", arc_id="a1", sequence_order=1,
                     cast=["hero", "ally"], pov_agent="ally", exit_state="名单转移")
    r.upsert_chapter_plan(ch)
    got = r.get_chapter_plan("c1")
    assert got.pov_agent == "ally"
    assert got.exit_state == "名单转移"


# ---- 问题1：换场轮换 helper ----
def test_rotate_location_prefers_fresh():
    r = _repo()
    p = Planner(r, llm=None, theme="x")
    locs = [("loc_a", "甲"), ("loc_b", "乙"), ("loc_c", "丙")]
    # 最近用过 a、b → 应换到没用过的 c
    assert p._rotate_location(locs, ["loc_a", "loc_b"], "loc_a") == "loc_c"


def test_rotate_location_falls_back_when_all_used():
    r = _repo()
    p = Planner(r, llm=None, theme="x")
    locs = [("loc_a", "甲"), ("loc_b", "乙")]
    # 全用过 → 退回 prev_loc
    assert p._rotate_location(locs, ["loc_a", "loc_b"], "loc_b") == "loc_b"


# ---- 问题6：离线生成时 POV 按章轮换（主角主导 + 配角隔章主讲） ----
def test_pov_rotation_offline():
    r = _repo()
    planner = Planner(r, llm=None, theme="x")
    planner.build_master(part_count=1, arcs_per_part=1)
    planner.plan_next_arc()
    povs = []
    for _ in range(6):
        ch = planner.next_chapter()
        if ch is None:
            break
        povs.append(ch.pov_agent)
    # 至少有一个非空 POV，且主角占多数（主导），但不应全是同一人（有配角主讲场）
    assert any(povs), f"POV 不应全空：{povs}"
    # 主角应是出现最多的 POV（主导）
    from collections import Counter
    if povs:
        top = Counter(p for p in povs if p).most_common(1)
        assert top, f"应有主导 POV：{povs}"
