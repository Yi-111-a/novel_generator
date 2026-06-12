"""问题1：地点↔beat 对账门——LLM "嘴上换地、身体没动"（location 填咖啡馆、beat 却写在包厢）时，
以 beat 实际发生地为准对齐 location，消除"包厢 vs 咖啡馆"硬矛盾。
"""
from __future__ import annotations

import json

from novel_engine import db
from novel_engine.llm.base import LLMClient
from novel_engine.models import Arc, Entity, Part, Persona
from novel_engine.planner import Planner
from novel_engine.repository import Repository

_LOCS = [("loc_caf", "霞飞路·文艺复兴咖啡馆"), ("loc_box", "百乐门舞厅·二楼牡丹包厢")]


def _repo() -> Repository:
    r = Repository(db.connect(":memory:"))
    for aid, name in [("hero", "沈砚"), ("ally", "赵九")]:
        r.insert_entity(Entity(aid, "character", name, {}))
        r.insert_persona(Persona(agent_id=aid, name=name, want="x"))
    return r


# ---------------- _dominant_beat_loc 判定 ----------------
def test_dominant_beat_loc_matches_box():
    p = Planner(_repo(), llm=None, theme="x")
    beats = ["沈砚独自坐在百乐门二楼牡丹包厢的皮沙发上，指尖摩挲着茶杯。"]
    assert p._dominant_beat_loc(beats, _LOCS) == "loc_box"


def test_dominant_beat_loc_matches_cafe():
    p = Planner(_repo(), llm=None, theme="x")
    beats = ["两人在文艺复兴咖啡馆二楼临窗坐下，沈砚点了杯黑咖啡。"]
    assert p._dominant_beat_loc(beats, _LOCS) == "loc_caf"


def test_dominant_beat_loc_no_match_returns_empty():
    p = Planner(_repo(), llm=None, theme="x")
    assert p._dominant_beat_loc(["他走在雨里，心事重重，谁也没遇见。"], _LOCS) == ""


# ---------------- _chapter_spec 对齐 ----------------
class _ConflictLLM(LLMClient):
    """location 填咖啡馆，但 beat 首拍写在百乐门包厢——典型"嘴上换地、身体没动"。"""

    def complete(self, system: str, user: str) -> str:
        return json.dumps({
            "beats": [
                "沈砚独自坐在百乐门二楼牡丹包厢的皮沙发上，指尖摩挲着茶杯，垂眼看地毯上的折痕。",
                "苏静推门进来在对面坐下，质问地毯下折痕的事，气氛骤紧。",
                "陈啸闯入，靠在门框上，声称昨天就看过那份名单。",
            ],
            "beat_povs": ["沈砚", "沈砚", "沈砚"],
            "location": "霞飞路·文艺复兴咖啡馆",
            "question": "沈砚的伪装会不会被拆穿？",
            "exit_state": "陈啸介入，沈砚与苏静互相猜忌，局面失控。",
            "props": ["照片"],
        }, ensure_ascii=False)


def test_chapter_spec_aligns_location_to_beats():
    r = _repo()
    p = Planner(r, llm=_ConflictLLM(), theme="孤岛")
    part = Part(part_id="part1", sequence_order=1, title="霓虹面具", goal="潜伏", region="法租界")
    arc = Arc(arc_id="arc1", part_id="part1", sequence_order=1, summary="入局", target_chapters=5)
    beats, loc, dq, props, exit_state, povs = p._chapter_spec(
        part, arc, "rising", has_reveal=False, locs=_LOCS,
        prev_loc="loc_box", pov_name="沈砚", cast_names="沈砚、赵九")
    # 地点对齐到 beat 实际发生地（包厢），而非 LLM 嘴上填的咖啡馆 → 不再自相矛盾
    assert loc == "loc_box", (loc, beats[0])
    assert len(beats) >= 3
