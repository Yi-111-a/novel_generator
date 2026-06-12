"""P1 多角色对手戏 + P5 道具转交物化 + P4b 取名去重 的针对性回归。"""
from __future__ import annotations

from novel_engine import db
from novel_engine.agent import CharacterAgent
from novel_engine.casting import _distinct_name, cast_or_get
from novel_engine.dilemma import DilemmaGenerator
from novel_engine.director import Director
from novel_engine.llm.mock import MockClient
from novel_engine.models import (
    Entity, Fact, Foreshadow, InventoryItem, KnowledgeItem, Persona,
)
from novel_engine.monitors import Monitors
from novel_engine.planner import Planner
from novel_engine.repository import Repository
from novel_engine.validator import Validator


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


def _planned_director(r: Repository, agent_llm=None) -> tuple[Director, Planner]:
    planner = Planner(r, llm=None, theme="证道")
    planner.build_master(part_count=3, arcs_per_part=1)
    planner.plan_next_arc()
    planner.next_chapter()
    d = Director(
        r, DilemmaGenerator(r, llm=None, theme="证道"),
        CharacterAgent(r, agent_llm or MockClient()), Validator(r),
        Monitors(r, flaw_max_free=2), planner=planner,
    )
    return d, planner


# ---------- P1：一个 beat 内多个 actor 轮流行动 ----------
def test_multiple_actors_act_within_chapter():
    r = _seed_repo()
    d, _ = _planned_director(r)
    ch = r.active_chapter_plan()
    ch.cast = ["hero", "ally", "villain"]
    r.upsert_chapter_plan(ch)

    for _ in range(30):
        step = d.step()
        if step.chapter_done:
            break
    actors = {a for e in r.events_for_beat(ch.chapter_id) for a in e.actors}
    # P1：本章事件的行动者应覆盖多个 cast 成员（而非只有焦点角色一人独白）
    assert len(actors) >= 2, f"应有多个角色真实行动，实际：{actors}"
    assert actors <= set(["hero", "ally", "villain"])


# ---------- P5：give 动作落 inventory 转移 ----------
def test_give_action_transfers_inventory():
    r = _seed_repo()
    # hero 持有一件道具
    r.insert_entity(Entity("obj_token", "object", "铜牌", {}))
    r.set_inventory(InventoryItem("obj_token", holder_agent_id="hero", status="held"))
    # hero 的动作：把铜牌交给 ally
    give = MockClient.from_actions([{
        "intent": "递交铜牌", "target": "ally", "dialogue": "拿着。",
        "inner_thought": "", "chosen_value": "",
        "referenced_facts": [], "referenced_entities": ["obj_token"],
    }])
    d, _ = _planned_director(r, agent_llm=give)
    ch = r.active_chapter_plan()
    ch.cast = ["hero", "ally"]
    ch.available_items = ["obj_token"]
    ch.items_present = ["obj_token"]
    r.upsert_chapter_plan(ch)

    # 第一拍：actor_idx=0 = hero（cast[0]）行动 → 触发转交
    d.step()
    item = r.get_inventory_item("obj_token")
    assert item is not None
    assert item.holder_agent_id == "ally", "铜牌应已真实转移到 ally 手中"
    assert item.status == "held"


def test_give_to_outsider_or_unowned_does_not_transfer():
    r = _seed_repo()
    r.insert_entity(Entity("obj_token", "object", "铜牌", {}))
    r.set_inventory(InventoryItem("obj_token", holder_agent_id="villain", status="held"))
    # hero 想给一件**自己并不持有**的道具 → 不应转移
    give = MockClient.from_actions([{
        "intent": "递交铜牌", "target": "ally", "dialogue": "",
        "inner_thought": "", "chosen_value": "",
        "referenced_facts": [], "referenced_entities": ["obj_token"],
    }])
    d, _ = _planned_director(r, agent_llm=give)
    ch = r.active_chapter_plan()
    ch.cast = ["hero", "ally"]
    r.upsert_chapter_plan(ch)
    d.step()
    item = r.get_inventory_item("obj_token")
    assert item.holder_agent_id == "villain", "非持有者不能转交他人之物"


# ---------- P4b：取名去重 ----------
def test_distinct_name_avoids_collision_and_clustering():
    existing = ["沈砚", "林晚", "陈阿公"]
    name = _distinct_name("", existing)
    assert name not in existing
    assert name[0] not in {n[0] for n in existing}, "不得同姓扎堆"
    assert name[-1] not in {n[-1] for n in existing}, "不得共用末字"
    assert "无名" not in name


def test_fallback_cast_no_placeholder_name():
    r = _seed_repo()
    card = cast_or_get(r, "incubated_slot", tier="supporting", context="x", llm=None)
    assert "无名客" not in card.name
    existing = ["云鹤子", "季拾遗", "墨渊"]
    assert card.name not in existing
