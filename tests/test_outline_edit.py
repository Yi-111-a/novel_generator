"""大纲编辑/删除：编辑级联建人物道具；已写完判定；删除级联清正文。"""
from __future__ import annotations

from novel_engine import db
from novel_engine.models import Arc, ChapterPlan, Entity, Event, Persona, Scene
from novel_engine.planner import Planner
from novel_engine.repository import Repository


def _repo() -> Repository:
    r = Repository(db.connect(":memory:"))
    r.insert_entity(Entity("hero", "character", "沈砚", {}))
    r.insert_persona(Persona(agent_id="hero", name="沈砚", want="x",
                             values=[{"name": "v", "weight": 0.5}]))
    r.upsert_arc(Arc(arc_id="a1", part_id="p1", sequence_order=1))
    r.upsert_chapter_plan(ChapterPlan(chapter_id="c1", arc_id="a1", sequence_order=1,
                                      cast=["hero"], title="旧标题"))
    return r


def test_edit_chapter_basic_fields():
    r = _repo()
    p = Planner(r, llm=None, theme="x")
    ch = p.edit_chapter("c1", title="新标题", dramatic_question="他会走吗？",
                        conflict_type="身份危机", exit_state="名单转移")
    assert ch.title == "新标题" and ch.dramatic_question == "他会走吗？"
    assert ch.conflict_type == "身份危机" and ch.exit_state == "名单转移"
    assert r.get_chapter_plan("c1").title == "新标题"


def test_edit_cascade_creates_character_and_item():
    r = _repo()
    p = Planner(r, llm=None, theme="x")
    p.edit_chapter("c1", cast_names=["沈砚", "新角色甲"], item_names=["密信"])
    chars = {e.name for e in r.list_entities() if e.type == "character"}
    objs = {e.name for e in r.list_entities() if e.type == "object"}
    assert "新角色甲" in chars   # 级联新建人物
    assert "密信" in objs        # 级联新建道具
    ch = r.get_chapter_plan("c1")
    assert len(ch.cast) == 2 and "密信" in [r.get_entity(o).name for o in ch.items_present]


def test_chapter_is_written_and_delete_cascade():
    r = _repo()
    # 写入一场（含事件）→ 视为已写
    r.append_event(Event("e1", 0, ["hero"], "说", beat_id="c1"))
    r.insert_scene(Scene(scene_id="s1", discourse_order=1, source_events=["e1"],
                         pov="hero", prose_text="正文。"))
    assert r.chapter_is_written("c1") is True
    res = r.delete_chapter_cascade("c1")
    assert res["scenes"] == 1 and res["events"] == 1
    assert r.get_chapter_plan("c1") is None
    assert r.list_scenes() == [] and r.events_for_beat("c1") == []


def test_not_written_when_only_plan():
    r = _repo()
    assert r.chapter_is_written("c1") is False  # 只有计划、无正文 → 可改
