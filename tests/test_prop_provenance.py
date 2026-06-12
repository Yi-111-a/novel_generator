"""结构整改①：道具来源闸门——beat 文本里的道具登记成实体 + 进 items_introduced。"""
from __future__ import annotations

from novel_engine import db
from novel_engine.models import Entity, Persona
from novel_engine.planner import Planner
from novel_engine.repository import Repository


def _repo() -> Repository:
    r = Repository(db.connect(":memory:"))
    r.insert_entity(Entity("hero", "character", "沈砚", {}))
    r.insert_persona(Persona(agent_id="hero", name="沈砚", want="x",
                             values=[{"name": "v", "weight": 0.5}]))
    return r


def test_register_props_creates_and_reuses():
    p = Planner(_repo(), llm=None, theme="x")
    ids = p._register_props(["日记本", "照片"])
    assert len(ids) == 2
    names = {e.name for e in p.repo.list_entities() if e.type == "object"}
    assert "日记本" in names and "照片" in names
    # 再次登记同名 → 复用，不新建
    ids2 = p._register_props(["日记本"])
    assert ids2 == [ids[0]]
    assert len([e for e in p.repo.list_entities() if e.type == "object"]) == 2


def test_register_props_filters_junk():
    p = Planner(_repo(), llm=None, theme="x")
    # 空 / 过长(像句子) / 纯英文 → 全部过滤
    assert p._register_props(["", "他从口袋里掏出一支很长的钢笔来", "backup chip"]) == []


def test_chapter_spec_offline_returns_tuple():
    p = Planner(_repo(), llm=None, theme="x")
    out = p._chapter_spec(None, __import__("novel_engine.models", fromlist=["Arc"]).Arc(
        arc_id="a1", part_id="p1", sequence_order=1), "setup", has_reveal=False,
        locs=[("loc_a", "甲")], prev_loc=None)
    assert len(out) == 6                       # beats, loc, dq, props, exit_state, beat_povs
    beats, loc, dq, props, exit_state, beat_povs = out
    assert isinstance(beats, list) and isinstance(props, list) and isinstance(beat_povs, list)
    assert isinstance(exit_state, str)
