"""§14 深度校验闸门（过薄重生成）+ §11 provisional 章演到时复核。"""
from __future__ import annotations

import json

from novel_engine import db
from novel_engine.llm.base import LLMClient
from novel_engine.models import Entity, Fact, Foreshadow, KnowledgeItem, Persona
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


# ---------- §14 深度闸门 ----------
class _ThinThenRich(LLMClient):
    def __init__(self) -> None:
        self.n = 0

    def complete(self, system: str, user: str) -> str:
        self.n += 1
        if self.n == 1:  # 第一次：过薄（geo_full 太短）
            return json.dumps([{"name": "甲", "geo_full": "短", "controlling_faction": "", "notable_items": []}])
        return json.dumps([
            {"name": "藏经阁", "geo_full": "高阁三层，檐角悬铜铃，夜里风过有声，守卫森严。",
             "controlling_faction": "太虚宗", "notable_items": ["铜钥"]},
            {"name": "断魂崖", "geo_full": "千仞绝壁，雾锁深渊，唯一条铁索横越其上。",
             "controlling_faction": "", "notable_items": []},
        ])


def test_thin_locations_trigger_regeneration():
    r = _seed_repo()
    p = Planner(r, llm=_ThinThenRich(), theme="证道")
    specs = p._part_locations({"region": "北域", "goal": "探秘"})
    assert p.llm.n == 2  # 触发了一次重生成
    assert {s["name"] for s in specs} == {"藏经阁", "断魂崖"}
    assert all(len(s["geo_full"]) >= 12 for s in specs)


class _AlwaysThin(LLMClient):
    def complete(self, system: str, user: str) -> str:
        return json.dumps([{"name": "甲", "geo_full": "短", "controlling_faction": "", "notable_items": []}])


def test_persistently_thin_falls_back_to_template():
    r = _seed_repo()
    p = Planner(r, llm=_AlwaysThin(), theme="证道")
    specs = p._part_locations({"region": "北域", "goal": "探秘"})
    # 始终过薄 → 走确定性回退（≥2 个、geo_full 非空）
    assert len(specs) >= 2 and all(s["geo_full"] for s in specs)


# ---------- §11 provisional 复核 ----------
def test_revise_only_touches_planned_chapters():
    r = _seed_repo()
    p = Planner(r, llm=None, theme="证道")
    p.build_master(part_count=3, arcs_per_part=1)
    p.build_full_outline()
    part2 = r.list_parts()[1]
    arc_ids = {a.arc_id for a in r.list_arcs(part2.part_id)}
    planned = [c for c in r.list_chapter_plans() if c.arc_id in arc_ids and c.status == "planned"]
    assert planned, "第二部应有 provisional(planned) 章"

    # 把其中一章标 done → 复核应跳过它
    done_ch = planned[0]
    done_ch.status = "done"
    r.upsert_chapter_plan(done_ch)

    n = p.revise_provisional_chapters(part2.part_id)
    assert n == len(planned) - 1  # done 的那章被跳过
    assert r.get_chapter_plan(done_ch.chapter_id).status == "done"  # 未被动到


def test_revise_keeps_ids_and_role():
    r = _seed_repo()
    p = Planner(r, llm=None, theme="证道")
    p.build_master(part_count=3, arcs_per_part=1)
    p.build_full_outline()
    part2 = r.list_parts()[1]
    before = {c.chapter_id: (c.role, c.sequence_order)
              for a in r.list_arcs(part2.part_id) for c in r.list_chapter_plans(a.arc_id)}
    p.revise_provisional_chapters(part2.part_id)
    after = {c.chapter_id: (c.role, c.sequence_order)
             for a in r.list_arcs(part2.part_id) for c in r.list_chapter_plans(a.arc_id)}
    assert before == after  # id/role/章号不变（只刷新目标/问题/钩子）
