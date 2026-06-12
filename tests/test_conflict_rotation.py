"""S1 冲突类型轮换 + S2 审计向前检查。"""
from __future__ import annotations

from novel_engine import db
from novel_engine.models import Arc, ChapterPlan, Entity, Event, Persona
from novel_engine.planner import Planner, _conflict_curve, _CONFLICT_TYPES
from novel_engine.repository import Repository


def test_conflict_curve_rotates_no_adjacent_repeat():
    roles = ["setup", "rising", "rising", "rising", "twist", "climax", "resolution"]
    cur = _conflict_curve(roles)
    assert len(cur) == len(roles)
    assert all(c in _CONFLICT_TYPES for c in cur)
    # 相邻不重复
    assert all(cur[i] != cur[i + 1] for i in range(len(cur) - 1)), cur
    # 覆盖 ≥4 种
    assert len(set(cur)) >= 4, cur
    # 里程碑 role 给契合类型
    assert cur[4] == "身份危机" and cur[5] == "正面对峙" and cur[6] == "情感羁绊"


def _repo() -> Repository:
    r = Repository(db.connect(":memory:"))
    for aid, nm in [("hero", "沈砚"), ("ally", "赵九")]:
        r.insert_entity(Entity(aid, "character", nm, {}))
        r.insert_persona(Persona(agent_id=aid, name=nm, want="x",
                                 values=[{"name": "v", "weight": 0.5}]))
    return r


def test_generate_chapter_assigns_conflict_type():
    r = _repo()
    p = Planner(r, llm=None, theme="x")
    p.build_master(part_count=1, arcs_per_part=1)
    p.plan_next_arc()
    cts = []
    for _ in range(5):
        ch = p.next_chapter()
        if ch is None:
            break
        cts.append(ch.conflict_type)
    assert all(cts), f"每章都应有冲突类型：{cts}"
    # 相邻不重复
    assert all(cts[i] != cts[i + 1] for i in range(len(cts) - 1)), cts


def test_conflict_type_persists():
    r = _repo()
    r.upsert_arc(Arc(arc_id="a1", part_id="p1", sequence_order=1))
    r.upsert_chapter_plan(ChapterPlan(chapter_id="c1", arc_id="a1", sequence_order=1,
                                      conflict_type="身份危机"))
    assert r.get_chapter_plan("c1").conflict_type == "身份危机"


def test_audit_advancement_detection():
    from novel_engine.narration.audit import _advanced
    r = _repo()
    ch = ChapterPlan(chapter_id="c1", arc_id="a1", sequence_order=1)
    # 无抉择事件 → 未推进
    r.append_event(Event("e0", 0, ["hero"], "观察", payload={"chosen_value": ""}, beat_id="c1"))
    assert _advanced(r, ch) is False
    # 有关键抉择 → 推进
    r.append_event(Event("e1", 1, ["hero"], "抉择", payload={"chosen_value": "守诺"}, beat_id="c1"))
    assert _advanced(r, ch) is True
