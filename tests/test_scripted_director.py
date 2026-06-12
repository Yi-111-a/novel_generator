"""B1-b scripted 模式接线：director._step_scripted 端到端（照 beat 写场→抽事实→落库→收束）。"""
from __future__ import annotations

from novel_engine import db
from novel_engine.agent import CharacterAgent
from novel_engine.dilemma import DilemmaGenerator
from novel_engine.director import Director
from novel_engine.llm.mock import MockClient
from novel_engine.models import ChapterPlan, Entity, Persona
from novel_engine.monitors import Monitors
from novel_engine.narration.fact_extractor import FactExtractor
from novel_engine.narration.scene_writer import SceneWriter
from novel_engine.repository import Repository
from novel_engine.validator import Validator


class _StubPlanner:
    """最小 planner：吐一个 active 章，演完置 done 后 ensure_chapter 返回 None。"""
    def __init__(self, ch):
        self.ch = ch

    def ensure_chapter(self):
        return None if self.ch.status == "done" else self.ch


def _director(mode="scripted", llm=None) -> tuple[Director, Repository, ChapterPlan]:
    r = Repository(db.connect(":memory:"))
    r.insert_entity(Entity("hero", "character", "沈砚", {}))
    r.insert_entity(Entity("ally", "character", "赵九", {}))
    r.insert_persona(Persona(agent_id="hero", name="沈砚"))
    r.insert_persona(Persona(agent_id="ally", name="赵九"))
    ch = ChapterPlan(chapter_id="ch1", arc_id="a1", sequence_order=1, title="", status="active",
                     cast=["hero", "ally"], beat_goals=["对峙", "试探", "摊牌"],
                     target_scenes=3, min_scenes=2, target_tension=0.6,
                     dramatic_question="谁先动手？")
    r.upsert_chapter_plan(ch)
    d = Director(
        r, DilemmaGenerator(r, llm=None, theme=""), CharacterAgent(r, MockClient()),
        Validator(r), Monitors(r, flaw_max_free=2),
        planner=_StubPlanner(ch),
        writer=SceneWriter(r, llm=llm), extractor=FactExtractor(r, llm=llm), mode=mode,
    )
    return d, r, ch


def test_scripted_writes_scene_with_beat_linked_event():
    d, r, ch = _director()
    step = d.step()
    assert step.chapter_id == "ch1"
    scenes = r.list_scenes()
    assert len(scenes) == 1 and scenes[0].prose_text.strip()
    # 该场的源事件带 beat_id → 复用计数/收束/审计
    ev = r.get_event(scenes[0].source_events[0])
    assert ev.beat_id == "ch1"


def test_scripted_advances_beats_and_closes_chapter():
    d, r, ch = _director()
    done_flags = []
    for _ in range(6):
        step = d.step()
        done_flags.append(step.chapter_done)
        if ch.status == "done":
            break
    assert any(done_flags)                       # 到 target_scenes 收束
    assert ch.status == "done"
    assert len(r.list_scenes()) >= ch.min_scenes


def test_scripted_idle_after_book_filled():
    d, r, ch = _director()
    for _ in range(8):
        d.step()
    # 章 done 后 planner.ensure_chapter→None → step 空转，不再加场
    n = len(r.list_scenes())
    d.step()
    assert len(r.list_scenes()) == n


def test_pov_agent_overrides_focus_weight():
    # 章 pov_agent=hero，但 arc.focus_agents 让 ally 权重更高 → POV 必须用 hero（不被 arc 权重覆盖）
    from novel_engine.models import Arc
    r = Repository(db.connect(":memory:"))
    r.insert_entity(Entity("hero", "character", "沈砚", {}))
    r.insert_entity(Entity("ally", "character", "赵九", {}))
    r.insert_persona(Persona(agent_id="hero", name="沈砚"))
    r.insert_persona(Persona(agent_id="ally", name="赵九"))
    r.upsert_arc(Arc(arc_id="a1", part_id="p1", sequence_order=1, title="", summary="", target_chapters=1,
                     focus_agents=[{"agent_id": "hero", "weight": 0.3}, {"agent_id": "ally", "weight": 0.7}]))
    ch = ChapterPlan("ch1", "a1", 1, "", status="active", cast=["hero", "ally"],
                     pov_agent="hero", beat_goals=["甲"], target_scenes=1, min_scenes=1)
    r.upsert_chapter_plan(ch)
    cap = {}

    class _Spy(SceneWriter):
        def write(self, spec, feedback=""):
            cap["pov"] = spec.pov
            return super().write(spec, feedback)

    d = Director(r, planner=_StubPlanner(ch), writer=_Spy(r, llm=None),
                 extractor=FactExtractor(r, llm=None), mode="scripted")
    d.step()
    assert cap["pov"] == "hero"          # 章 pov_agent 优先，未被 arc 权重(ally 0.7)覆盖


def test_pov_follows_beat_per_scene():
    # POV 跟着节拍：beat_povs=[ally, hero] → 场1 POV=赵九，场2 POV=沈砚（轮到谁就谁视角）
    r = Repository(db.connect(":memory:"))
    r.insert_entity(Entity("hero", "character", "沈砚", {}))
    r.insert_entity(Entity("ally", "character", "赵九", {}))
    r.insert_persona(Persona(agent_id="hero", name="沈砚"))
    r.insert_persona(Persona(agent_id="ally", name="赵九"))
    ch = ChapterPlan("ch1", "a1", 1, "", status="active", cast=["hero", "ally"],
                     pov_agent="hero", beat_goals=["赵九潜入", "沈砚接应"],
                     beat_povs=["ally", "hero"], target_scenes=2, min_scenes=1)
    r.upsert_chapter_plan(ch)
    seen = []

    class _Spy(SceneWriter):
        def write(self, spec, feedback=""):
            seen.append(spec.pov)
            return super().write(spec, feedback)

    d = Director(r, planner=_StubPlanner(ch), writer=_Spy(r, llm=None),
                 extractor=FactExtractor(r, llm=None), mode="scripted")
    d.step()
    d.step()
    assert seen[:2] == ["ally", "hero"]      # 每场视角跟着该拍的 beat_pov


def test_beat_povs_round_trips_in_db():
    r = Repository(db.connect(":memory:"))
    ch = ChapterPlan("ch1", "a1", 1, "", cast=["hero", "ally"],
                     beat_goals=["a", "b"], beat_povs=["hero", "ally"])
    r.upsert_chapter_plan(ch)
    got = r.get_chapter_plan("ch1")
    assert got.beat_povs == ["hero", "ally"]


def test_sim_mode_default_does_not_use_scripted():
    # mode 默认 sim：即便传了 writer/extractor，planner 走 _step_planned（不产成稿场）
    d, r, ch = _director(mode="sim")
    d.step()
    assert r.list_scenes() == []                  # sim 模式不在 director 内落成稿场
