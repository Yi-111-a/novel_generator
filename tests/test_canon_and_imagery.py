"""第一批修复：道具 canon 固化（问题3）+ 跨场意象禁令（问题2）。"""
from __future__ import annotations

from novel_engine import db
from novel_engine.casting import lock_motif_canon
from novel_engine.models import Entity, Persona
from novel_engine.narration.narrator import _recurring_imagery
from novel_engine.repository import Repository


# ---- 问题2：跨场意象禁令 ----
def test_recurring_imagery_flagged():
    scenes = [
        "挂钟的秒针停在十点。梧桐叶贴在窗上。",
        "他看着那口钟，梧桐叶又响了一阵。",
        "钟摆不动了，梧桐的影子落在桌上。",
    ]
    ban = _recurring_imagery(scenes, min_scenes=3)
    assert "钟" in ban and "梧桐" in ban


def test_imagery_not_flagged_when_few_scenes():
    # 不足 min_scenes 场 → 不触发
    assert _recurring_imagery(["钟。梧桐。"], min_scenes=3) == []


def test_imagery_distinct_not_flagged():
    scenes = ["他推开门，雨落在台阶上。", "她点了一支烟，望向码头。", "桌上摊着一封信。"]
    # 每个意象只出现一两场 → 不算刷屏
    assert _recurring_imagery(scenes, min_scenes=3) == []


# ---- 问题3：道具 canon 固化 ----
def test_lock_motif_canon_no_llm_returns_empty():
    r = Repository(db.connect(":memory:"))
    r.insert_entity(Entity("p1", "character", "沈砚", {}))
    r.insert_entity(Entity("obj_pen", "object", "钢笔", {}))
    r.insert_persona(Persona(agent_id="p1", name="沈砚", motif_objects=["obj_pen"]))
    assert lock_motif_canon(r, llm=None) == {}  # 无 LLM 不臆造


def test_update_entity_attributes_merges():
    r = Repository(db.connect(":memory:"))
    r.insert_entity(Entity("obj_pen", "object", "钢笔", {"x": 1}))
    r.update_entity_attributes("obj_pen", {"canon": "黑色犀飞利，刻『婉』字"})
    e = r.get_entity("obj_pen")
    assert e.attributes.get("canon") == "黑色犀飞利，刻『婉』字"
    assert e.attributes.get("x") == 1  # 合并而非覆盖
