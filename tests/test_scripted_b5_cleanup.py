"""B5 清理：scripted 不依赖模拟核心（dilemma/agent/validator/monitors/engine 全 None 也能跑）。"""
from __future__ import annotations

from novel_engine import db
from novel_engine.director import Director
from novel_engine.models import ChapterPlan, Entity, Persona
from novel_engine.narration.controller import Controller
from novel_engine.narration.fact_extractor import FactExtractor
from novel_engine.narration.scene_writer import SceneWriter
from novel_engine.repository import Repository


class _StubPlanner:
    def __init__(self, ch):
        self.ch = ch

    def ensure_chapter(self):
        return None if self.ch.status == "done" else self.ch


def _scripted_director():
    r = Repository(db.connect(":memory:"))
    r.insert_entity(Entity("hero", "character", "沈砚", {}))
    r.insert_persona(Persona(agent_id="hero", name="沈砚"))
    ch = ChapterPlan(chapter_id="ch1", arc_id="a1", sequence_order=1, title="", status="active",
                     cast=["hero"], beat_goals=["甲", "乙"], target_scenes=2, min_scenes=1)
    r.upsert_chapter_plan(ch)
    # 关键：不传 generator/agent/validator/monitors（模拟核心），纯 scripted 三件套
    d = Director(r, planner=_StubPlanner(ch),
                 writer=SceneWriter(r, llm=None), extractor=FactExtractor(r, llm=None),
                 controller=Controller(r, llm=None), mode="scripted")
    return d, r, ch


def test_director_engine_is_none_in_scripted():
    d, r, ch = _scripted_director()
    assert d.engine is None              # 不构造模拟核心 Engine
    assert d.generator is None and d.monitors is None


def test_scripted_runs_without_sim_core():
    d, r, ch = _scripted_director()
    for _ in range(4):
        d.step()
        if ch.status == "done":
            break
    assert ch.status == "done"           # 纯 scripted 路径写满收束
    assert len(r.list_scenes()) >= 1 and r.list_scenes()[0].prose_text.strip()


def test_sim_director_still_builds_engine():
    # 回归：传齐模拟核心（sim）时 Engine 照常构造
    from novel_engine.agent import CharacterAgent
    from novel_engine.dilemma import DilemmaGenerator
    from novel_engine.llm.mock import MockClient
    from novel_engine.monitors import Monitors
    from novel_engine.validator import Validator
    r = Repository(db.connect(":memory:"))
    d = Director(r, DilemmaGenerator(r, llm=None, theme=""), CharacterAgent(r, MockClient()),
                 Validator(r), Monitors(r))
    assert d.engine is not None
