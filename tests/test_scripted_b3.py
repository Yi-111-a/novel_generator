"""B3 收束 + 动态下一章：scripted exit_state 门控 + planner.revise_next_chapter。"""
from __future__ import annotations

from novel_engine import db
from novel_engine.agent import CharacterAgent
from novel_engine.dilemma import DilemmaGenerator
from novel_engine.director import Director
from novel_engine.llm.base import LLMClient
from novel_engine.llm.mock import MockClient
from novel_engine.models import Arc, ChapterPlan, Entity, Part, Persona
from novel_engine.monitors import Monitors
from novel_engine.narration.fact_extractor import FactExtractor
from novel_engine.narration.scene_writer import SceneWriter
from novel_engine.planner import Planner
from novel_engine.repository import Repository
from novel_engine.validator import Validator


class _StubPlanner:
    def __init__(self, ch):
        self.ch = ch

    def ensure_chapter(self):
        return None if self.ch.status == "done" else self.ch


class _ChosenLLM(LLMClient):
    """FactExtractor 用：抽出一个带 chosen_value 的事实 → 触发 _chapter_advanced。"""
    def complete(self, s, u):
        return self.complete_at(s, u)

    def complete_at(self, s, u, temperature=None):
        import json
        return json.dumps({"new_facts": [{"content": "沈砚做出抉择", "involved": ["沈砚"]}],
                           "chosen_value": "舍卒保车"}, ensure_ascii=False)


def _director(extractor_llm=None):
    r = Repository(db.connect(":memory:"))
    r.insert_entity(Entity("hero", "character", "沈砚", {}))
    r.insert_persona(Persona(agent_id="hero", name="沈砚"))
    ch = ChapterPlan(chapter_id="ch1", arc_id="a1", sequence_order=1, title="", status="active",
                     cast=["hero"], beat_goals=["甲", "乙"], target_scenes=4, min_scenes=1,
                     target_tension=0.5)
    r.upsert_chapter_plan(ch)
    d = Director(r, DilemmaGenerator(r, llm=None, theme=""), CharacterAgent(r, MockClient()),
                 Validator(r), Monitors(r), planner=_StubPlanner(ch),
                 writer=SceneWriter(r, llm=None), extractor=FactExtractor(r, llm=extractor_llm),
                 mode="scripted")
    return d, r, ch


# ---- 收束门控：演完 beats 但无推进 → 不在下界收，逼到上界 ----
def test_no_advance_runs_to_upper_bound():
    d, r, ch = _director(extractor_llm=None)   # 离线抽取→无 chosen_value→无推进
    for _ in range(3):                          # 2 beats 演完，但不该收（无推进）
        d.step()
    assert ch.status == "active" and len(r.list_scenes()) == 3
    d.step()                                     # 到上界 target_scenes=4 → 硬收
    assert ch.status == "done"


def test_advance_closes_at_beats_done():
    d, r, ch = _director(extractor_llm=_ChosenLLM())  # 抽出 chosen_value → 有推进
    d.step()
    d.step()                                     # 2 beats 演完 + 有推进 → 下界即收
    assert ch.status == "done" and len(r.list_scenes()) == 2


# ---- revise_next_chapter：复核下一个 planned 章 ----
def _planner_repo():
    r = Repository(db.connect(":memory:"))
    r.insert_entity(Entity("hero", "character", "沈砚", {}))
    r.insert_persona(Persona(agent_id="hero", name="沈砚"))
    r.upsert_part(Part(part_id="p1", sequence_order=1, title="第一部", goal="揭开序幕", status="active"))
    r.upsert_arc(Arc(arc_id="a1", part_id="p1", sequence_order=1, title="", summary="风云初起",
                     target_chapters=3))
    r.upsert_chapter_plan(ChapterPlan(chapter_id="c1", arc_id="a1", sequence_order=1, title="",
                                      status="done", cast=["hero"], role="setup"))
    r.upsert_chapter_plan(ChapterPlan(chapter_id="c2", arc_id="a1", sequence_order=2, title="",
                                      status="planned", cast=["hero"], role="rising",
                                      beat_goals=["旧目标"]))
    return r


def test_revise_next_chapter_targets_lowest_planned():
    r = _planner_repo()
    out = Planner(r, llm=None).revise_next_chapter()
    assert out is not None and out.chapter_id == "c2"
    ch2 = r.get_chapter_plan("c2")
    assert ch2.beat_goals and len(ch2.beat_goals) >= 3      # 离线模板刷新（≥3 递进节拍）
    assert ch2.status == "planned"                          # 不改状态/id/章号


class _ItemLLM(LLMClient):
    """B6.1：抽出叙事道具易手 → 入册 → _chapter_advanced 真。"""
    def complete(self, s, u):
        return self.complete_at(s, u)

    def complete_at(self, s, u, temperature=None):
        import json
        return json.dumps({"new_facts": [{"content": "沈砚夺走纸条", "involved": ["沈砚"]}],
                           "item_transfers": [{"obj": "染血纸条", "to": "沈砚", "status": "transferred"}]},
                          ensure_ascii=False)


def test_b6_1_item_extraction_makes_chapter_advance():
    d, r, ch = _director(extractor_llm=_ItemLLM())
    d.step()
    assert d._chapter_advanced(ch) is True       # 叙事道具入册 → 本章有实质推进
    assert any(e.name == "染血纸条" for e in r.list_entities())


def test_revise_next_chapter_none_when_no_planned():
    r = _planner_repo()
    r.upsert_chapter_plan(ChapterPlan(chapter_id="c2", arc_id="a1", sequence_order=2, title="",
                                      status="done", cast=["hero"], role="rising"))
    assert Planner(r, llm=None).revise_next_chapter() is None
