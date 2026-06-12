"""B3 推进闸门（停滞→升级弱点两难）+ B4 节拍演完即收。"""
from __future__ import annotations

from novel_engine import db
from novel_engine.agent import CharacterAgent
from novel_engine.dilemma import DilemmaGenerator
from novel_engine.director import Director
from novel_engine.llm.mock import MockClient
from novel_engine.models import (
    ChapterPlan, Entity, Fact, Foreshadow, KnowledgeItem, Persona,
)
from novel_engine.monitors import Monitors
from novel_engine.planner import Planner
from novel_engine.repository import Repository
from novel_engine.validator import Validator


# ---------- B4：纯逻辑 ----------
def test_should_close_when_all_beats_consumed():
    d = Director.__new__(Director)
    d.repo = None
    ch = ChapterPlan(chapter_id="c", arc_id="a", sequence_order=1,
                     beat_goals=["拍1", "拍2", "拍3"], min_scenes=2, target_scenes=4,
                     resolution_predicate="")
    assert d._should_close(ch, 2) is False   # 还差一拍
    assert d._should_close(ch, 3) is True    # 三拍演完 → 收


def test_should_close_respects_min_scenes_lower_bound():
    d = Director.__new__(Director)
    d.repo = None
    ch = ChapterPlan(chapter_id="c", arc_id="a", sequence_order=1,
                     beat_goals=["拍1", "拍2", "拍3"], min_scenes=2, target_scenes=4)
    assert d._should_close(ch, 1) is False


# ---------- B3+B4：端到端 ----------
def _seed_repo() -> Repository:
    r = Repository(db.connect(":memory:"))
    r.insert_entity(Entity("loc_main", "location", "主场景", {}))
    for aid, name in [("hero", "云鹤子"), ("ally", "季拾遗"), ("villain", "墨渊")]:
        r.insert_entity(Entity(aid, "character", name, {}))
        r.insert_persona(Persona(agent_id=aid, name=name, want="求道",
                                 values=[{"name": "道心", "weight": 0.7}], fatal_flaw="执念"))
    r.append_fact(Fact("f_secret", "state", "旧案关键握在墨渊手里。", involved_entities=["villain"]))
    r.insert_knowledge(KnowledgeItem("villain", "f_secret", "旧案关键握在墨渊手里。", 1.0, 0))
    r.upsert_foreshadow(Foreshadow("fs_secret", "墨渊瞒着什么？", "f_secret", 1, True))
    return r


def test_director_closes_chapter_around_beat_count():
    r = _seed_repo()
    planner = Planner(r, llm=None, theme="证道")
    planner.build_master(part_count=3, arcs_per_part=1)
    planner.plan_next_arc()
    ch = planner.next_chapter()
    nbeats = len(ch.beat_goals)
    d = Director(r, DilemmaGenerator(r, llm=None, theme="证道"),
                 CharacterAgent(r, MockClient()), Validator(r),
                 Monitors(r, flaw_max_free=2), planner=planner)
    # P1 多角色：K≤4 actors/beat，允许 nbeats*K + buffer 次迭代
    K = min(len(ch.cast), 4) if ch.cast else 1
    done_at = None
    for i in range(nbeats * K + 6):
        step = d.step()
        if step.chapter_done:
            done_at = i + 1
            break
    assert done_at is not None
    assert r.get_chapter_plan(ch.chapter_id).status == "done"
    # B3 停滞计数器存在且未抛错（路径被走到）
    assert isinstance(d._stall, dict)
