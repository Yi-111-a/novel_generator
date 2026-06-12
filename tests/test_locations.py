"""§12.3 地点一等实体：geo_full / 连通拓扑 / 固有道具入库 / 并入章道具台账。"""
from __future__ import annotations

import json

from novel_engine import db
from novel_engine.llm.base import LLMClient
from novel_engine.models import Arc, Entity, Fact, Foreshadow, InventoryItem, KnowledgeItem, Location, Part, Persona
from novel_engine.planner import Planner
from novel_engine.repository import Repository


def _seed_repo() -> Repository:
    r = Repository(db.connect(":memory:"))
    r.insert_entity(Entity("loc_main", "location", "主场景", {}))
    for aid, name in [("hero", "云鹤子"), ("ally", "季拾遗"), ("villain", "墨渊")]:
        r.insert_entity(Entity(aid, "character", name, {}))
        r.insert_persona(Persona(agent_id=aid, name=name, want="求道"))
    r.append_fact(Fact("f_secret", "state", "旧案关键握在墨渊手里。", involved_entities=["villain"]))
    r.insert_knowledge(KnowledgeItem("villain", "f_secret", "旧案关键握在墨渊手里。", 1.0, 0))
    r.upsert_foreshadow(Foreshadow("fs_secret", "墨渊瞒着什么？", "f_secret", 1, True))
    return r


def test_offline_part_locations_have_geo_and_connectivity():
    r = _seed_repo()
    Planner(r, llm=None, theme="证道").build_master(part_count=3)
    for part in r.list_parts():
        locs = r.list_locations(part.part_id)
        assert len(locs) >= 2
        assert all(l.geo_full for l in locs)          # 每个地点有完整描写（非空壳）
        # 同部地点至少形成一条通路（防瞬移）
        assert any(l.connects_to for l in locs)
        # loc_id 与 location 实体一一对应
        ent_ids = {e.entity_id for e in r.list_entities() if e.type == "location"}
        assert all(l.loc_id in ent_ids for l in locs)


class _LocLLM(LLMClient):
    def complete(self, system: str, user: str) -> str:
        return json.dumps([
            {"name": "藏经阁", "geo_full": "高阁三层，檐角悬铜铃，夜里风过有声。守卫森严。",
             "controlling_faction": "太虚宗", "notable_items": ["铜钥"]},
            {"name": "断魂崖", "geo_full": "千仞绝壁，雾锁深渊，唯一条铁索横越。",
             "controlling_faction": "", "notable_items": []},
        ])


def test_materialize_creates_item_entities_and_inventory():
    r = _seed_repo()
    p = Planner(r, llm=_LocLLM(), theme="证道")
    p._materialize_part_locations("part_x", {"region": "北域", "goal": "探秘"})
    locs = r.list_locations("part_x")
    assert {l.name for l in locs} == {"藏经阁", "断魂崖"}
    cangjing = next(l for l in locs if l.name == "藏经阁")
    assert cangjing.controlling_faction == "太虚宗"
    assert len(cangjing.notable_items) == 1
    oid = cangjing.notable_items[0]
    # 固有道具 → object 实体 + inventory（无主，归属该地）
    obj = next((e for e in r.list_entities() if e.entity_id == oid), None)
    assert obj is not None and obj.name == "铜钥"
    inv = r.get_inventory_item(oid)
    assert inv is not None and inv.holder_agent_id is None and "固有" in inv.note


def test_location_items_enter_chapter_ledger():
    r = _seed_repo()
    # 手工搭一个部：一个带固有道具的地点
    part = Part(part_id="part_a", sequence_order=1, title="第一部", region="北域", status="active")
    r.upsert_part(part)
    r.insert_entity(Entity("loc_a", "location", "藏经阁", {"part": "part_a"}))
    r.insert_entity(Entity("obj_key", "object", "铜钥", {"home_loc": "loc_a"}))
    r.set_inventory(InventoryItem("obj_key", holder_agent_id=None, status="held", note="藏经阁固有"))
    r.upsert_location(Location(loc_id="loc_a", part_id="part_a", name="藏经阁",
                              geo_full="高阁三层。", notable_items=["obj_key"]))
    r.upsert_arc(Arc(arc_id="arc_a", part_id="part_a", sequence_order=1, target_chapters=5,
                     focus_agents=[{"agent_id": "hero", "weight": 0.7}], status="active"))

    ch = Planner(r, llm=None, theme="证道").next_chapter()
    assert ch is not None and ch.location_ids == ["loc_a"]
    # 地点固有道具进入在场台账 + 可用物品（来源：地点固有，非凭空）
    assert "obj_key" in ch.items_present
    assert "obj_key" in ch.available_items
    assert "obj_key" in ch.items_introduced
