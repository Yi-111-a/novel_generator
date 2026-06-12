"""场次衔接：本章滚动梗概 + POV 当前状态 + 无缝接续 注入 SceneWriter；director 组装。"""
from __future__ import annotations

from novel_engine import db
from novel_engine.agent import CharacterAgent
from novel_engine.dilemma import DilemmaGenerator
from novel_engine.director import Director
from novel_engine.llm.base import LLMClient
from novel_engine.llm.mock import MockClient
from novel_engine.models import ChapterPlan, Entity, Location, Persona
from novel_engine.monitors import Monitors
from novel_engine.narration.fact_extractor import FactExtractor
from novel_engine.narration.scene_writer import SceneSpec, SceneWriter
from novel_engine.repository import Repository
from novel_engine.validator import Validator


class _Cap(LLMClient):
    def __init__(self):
        self.usr = ""

    def complete(self, s, u):
        return self.complete_at(s, u)

    def complete_at(self, s, u, temperature=None):
        self.usr = u
        return "他接着往前走，没有停。"


def test_scene_writer_injects_digest_state_and_seamless():
    r = Repository(db.connect(":memory:"))
    r.insert_entity(Entity("hero", "character", "沈砚", {}))
    r.insert_persona(Persona(agent_id="hero", name="沈砚"))
    llm = _Cap()
    spec = SceneSpec(pov="hero", beat="逼近", chapter=ChapterPlan("c1", "a1", 1, "", cast=["hero"]),
                     prev_tail="他握紧了那张戏票，转身走向后台的暗门。" * 3,
                     chapter_digest="· 沈砚混入舞会\n· 苏静偷走纸条",
                     pov_state="此刻在「百乐门」；余温：警觉未消；刚做出的选择：不动声色")
    SceneWriter(r, llm).write(spec)
    assert "本章已发生" in llm.usr and "苏静偷走纸条" in llm.usr   # 滚动梗概
    assert "当前状态" in llm.usr and "百乐门" in llm.usr          # POV 状态
    assert "无缝接续" in llm.usr                                 # 接续硬约束
    assert "戏票" in llm.usr                                     # 加厚的 prev_tail


def test_director_assembles_continuity_across_scenes():
    r = Repository(db.connect(":memory:"))
    r.insert_entity(Entity("hero", "character", "沈砚", {}))
    r.insert_persona(Persona(agent_id="hero", name="沈砚"))
    r.upsert_location(Location(loc_id="loc1", part_id="p1", name="百乐门", geo_full="舞厅。"))
    ch = ChapterPlan("c1", "a1", 1, "", status="active", cast=["hero"],
                     location_ids=["loc1"], beat_goals=["甲", "乙", "丙"],
                     target_scenes=3, min_scenes=1)
    r.upsert_chapter_plan(ch)

    class _P:
        def ensure_chapter(self):
            return ch
    # 用离线 writer 但捕获其 spec：包一层
    captured = {}
    base = SceneWriter(r, llm=None)

    class _SpyWriter(SceneWriter):
        def write(self, spec, feedback=""):
            captured["digest"] = spec.chapter_digest
            captured["state"] = spec.pov_state
            return base.write(spec, feedback)

    d = Director(r, DilemmaGenerator(r, llm=None, theme=""), CharacterAgent(r, MockClient()),
                 Validator(r), Monitors(r), planner=_P(),
                 writer=_SpyWriter(r, llm=None), extractor=FactExtractor(r, llm=None), mode="scripted")
    d.step()   # 场1：本章尚无前事
    d.step()   # 场2：应能看到场1留下的 marker 事件作为 digest
    assert captured["digest"]                       # 第二场拿到了本章已发生
    assert "百乐门" in captured["state"]             # POV 状态含地点
