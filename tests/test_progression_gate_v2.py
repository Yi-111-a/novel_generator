"""第三批：问题4 推进闸门（无实质推进不在主路径收束，逼到上界）+ exit_state。"""
from __future__ import annotations

from novel_engine import db
from novel_engine.director import Director
from novel_engine.models import ChapterPlan, Entity, Event, Fact, Persona, RevealNode
from novel_engine.repository import Repository


def _director_with_repo() -> tuple[Director, Repository]:
    r = Repository(db.connect(":memory:"))
    d = Director.__new__(Director)
    d.repo = r
    return d, r


def _add_events(r: Repository, ch_id: str, n: int, chosen=None):
    for i in range(n):
        eid = f"e_{ch_id}_{i}"
        r.append_event(Event(eid, story_time=i, actors=["hero"], action_type="说",
                             payload={"chosen_value": chosen or ""}, beat_id=ch_id))


def test_beats_done_without_progress_does_not_close():
    """beats 演完但无抉择/转移/揭示 → 不在主路径收束（继续逼），除非到上界。"""
    d, r = _director_with_repo()
    ch = ChapterPlan("c1", "a1", 1, beat_goals=["b1", "b2", "b3"], min_scenes=2, target_scenes=5)
    _add_events(r, "c1", 3, chosen="")  # 3 个无抉择事件
    # beats_done=3 >= len(beats)=3，但无实质推进 → 不收
    assert d._should_close(ch, 3, K=1) is False


def test_beats_done_with_decision_closes():
    """有关键抉择(chosen_value) → 算实质推进 → 主路径收束。"""
    d, r = _director_with_repo()
    ch = ChapterPlan("c2", "a1", 1, beat_goals=["b1", "b2", "b3"], min_scenes=2, target_scenes=5)
    _add_events(r, "c2", 3, chosen="守诺")
    assert d._chapter_advanced(ch) is True
    assert d._should_close(ch, 3, K=1) is True


def test_upper_bound_forces_close_even_without_progress():
    """到上界(target_scenes)无条件收，不受推进闸门约束（防死循环）。"""
    d, r = _director_with_repo()
    ch = ChapterPlan("c3", "a1", 1, beat_goals=["b1", "b2"], min_scenes=2, target_scenes=3)
    _add_events(r, "c3", 5, chosen="")
    assert d._should_close(ch, 5, K=1) is True  # beats_done=5 >= target 3 → 收


def test_reveal_counts_as_progress():
    d, r = _director_with_repo()
    r.append_fact(Fact("f1", "state", "真相"))
    r.upsert_reveal_node(RevealNode("n1", fact_id="f1", kind="truth"))
    r.mark_node_discovered("n1", chapter=1)
    ch = ChapterPlan("c4", "a1", 1, beat_goals=["b1"], reveal_gate=["f1"],
                     min_scenes=1, target_scenes=5)
    _add_events(r, "c4", 1, chosen="")
    assert d._chapter_advanced(ch) is True  # 揭示解锁 = 推进


def test_exit_state_assigned_by_role():
    from novel_engine.planner import _ROLE_EXIT
    r = Repository(db.connect(":memory:"))
    for aid, name in [("hero", "甲"), ("ally", "乙")]:
        r.insert_entity(Entity(aid, "character", name, {}))
        r.insert_persona(Persona(agent_id=aid, name=name, want="x", values=[{"name": "v", "weight": 0.5}]))
    from novel_engine.planner import Planner
    p = Planner(r, llm=None, theme="x")
    p.build_master(part_count=1, arcs_per_part=1)
    p.plan_next_arc()
    ch = p.next_chapter()
    assert ch.exit_state in _ROLE_EXIT.values()
