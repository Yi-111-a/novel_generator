"""Fix A：SceneWriter 注入世界观 / 地点 / 前后文 / 身份称谓（治孤岛误读/无前后文/叫真名）。"""
from __future__ import annotations

from novel_engine import db
from novel_engine.llm.base import LLMClient
from novel_engine.models import Arc, ChapterPlan, Entity, KnowledgeItem, Location, Part, Persona
from novel_engine.narration.scene_writer import SceneSpec, SceneWriter
from novel_engine.repository import Repository


class _Cap(LLMClient):
    def __init__(self):
        self.sys = ""
        self.usr = ""

    def complete(self, s, u):
        return self.complete_at(s, u)

    def complete_at(self, s, u, temperature=None):
        self.sys, self.usr = s, u
        return "他在桌前坐下，没有说话。"


def _repo() -> Repository:
    r = Repository(db.connect(":memory:"))
    r.insert_entity(Entity("hero", "character", "沈砚",
                           {"identity": {"public": "秦书白", "true": "沈砚", "fact_id": "f_idn"}}))
    r.insert_entity(Entity("ally", "character", "赵九", {}))
    r.insert_persona(Persona(agent_id="hero", name="沈砚"))
    r.insert_persona(Persona(agent_id="ally", name="赵九"))
    r.add_bible_section("settingCore", "设定内核",
                        "1939 年上海租界孤悬于沦陷区之中，号称「孤岛」——被日军占领区四面围困的租界。")
    r.upsert_location(Location(loc_id="loc_wu", part_id="p1", name="百乐门",
                               geo_full="百乐门舞厅：纸醉金迷的销金窟，爵士乐与暗杀并存。"))
    r.upsert_part(Part(part_id="p1", sequence_order=1, title="第一部", goal="潜伏与试探"))
    r.upsert_arc(Arc(arc_id="a1", part_id="p1", sequence_order=1, title="", summary="名单初现", target_chapters=3))
    # 已写完的前一章（供前情梗概）
    r.upsert_chapter_plan(ChapterPlan(chapter_id="c0", arc_id="a1", sequence_order=1, title="",
                                      status="done", cast=["hero"], summary="沈砚摸清七十六号的换岗规律"))
    return r


def _ch() -> ChapterPlan:
    return ChapterPlan(chapter_id="c1", arc_id="a1", sequence_order=2, title="", status="active",
                       cast=["hero", "ally"], location_ids=["loc_wu"], beat_goals=["接头"],
                       dramatic_question="名单能否送出？")


def test_scene_writer_injects_world_bible_with_island_note():
    r, llm = _repo(), _Cap()
    SceneWriter(r, llm).write(SceneSpec(pov="hero", beat="接头", chapter=_ch()))
    assert "世界观" in llm.sys and "孤岛" in llm.sys
    assert "不是真的海岛" in llm.sys          # 防望文生义


def test_scene_writer_injects_geo_and_arc_and_prior():
    r, llm = _repo(), _Cap()
    SceneWriter(r, llm).write(SceneSpec(pov="hero", beat="接头", chapter=_ch()))
    assert "百乐门" in llm.usr                 # 地点 geo
    assert "名单初现" in llm.usr               # 本卷 arc 上下文
    assert "前情梗概" in llm.usr and "换岗规律" in llm.usr   # 前章承接


def test_scene_writer_uses_cover_name_when_identity_hidden():
    # POV 不知道自己身份事实时（外人视角场），称谓块要求用化名「秦书白」
    r, llm = _repo(), _Cap()
    SceneWriter(r, llm).write(SceneSpec(pov="ally", beat="接头", chapter=_ch()))
    assert "秦书白" in llm.usr and "称谓" in llm.usr


def test_pov_self_can_use_real_name_internally():
    r, llm = _repo(), _Cap()
    # 沈砚自己的 POV：称谓块说明公开用化名、内心用真名
    SceneWriter(r, llm).write(SceneSpec(pov="hero", beat="接头", chapter=_ch(),
                                        pov_known=[KnowledgeItem("hero", "f_idn", "我是沈砚", 1.0, 0)]))
    assert "秦书白" in llm.usr and "沈砚" in llm.usr
