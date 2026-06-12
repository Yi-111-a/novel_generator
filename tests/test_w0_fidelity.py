"""W0 设定保真闸门 + canonical 地名固化（治"planner 发明遮面街/镜面塔"漂移）。

覆盖：`lock_canonical_geography` 抽取+幂等、`_name_faithful` 判定、
`_part_locations` 拒绝冲突奇幻地名并回退 canon、从属子地点放行 + materialize 标注 parent_canon。
"""
from __future__ import annotations

import json

from novel_engine import db
from novel_engine.llm.base import LLMClient
from novel_engine.models import Entity, Location, Persona
from novel_engine.planner import Planner
from novel_engine.repository import Repository
from novel_engine.worldbible import lock_canonical_geography


def _repo() -> Repository:
    r = Repository(db.connect(":memory:"))
    for aid, name in [("hero", "沈砚"), ("ally", "赵九")]:
        r.insert_entity(Entity(aid, "character", name, {}))
        r.insert_persona(Persona(agent_id=aid, name=name, want="x"))
    return r


def _with_canon(r: Repository) -> Repository:
    """手工固化两个 canon 地点（霞飞路/七十六号）。"""
    r.insert_entity(Entity("loc_xfl", "location", "霞飞路", {"canon": True}))
    r.upsert_location(Location(loc_id="loc_xfl", part_id="", name="霞飞路",
                              geo_full="法租界主干道，梧桐成荫，霓虹与黄包车交错。"))
    r.insert_entity(Entity("loc_76", "location", "七十六号", {"canon": True}))
    r.upsert_location(Location(loc_id="loc_76", part_id="", name="七十六号",
                              geo_full="极司菲尔路特工总部，铁门森严，地下有审讯室。"))
    return r


class _FixedLLM(LLMClient):
    def __init__(self, payload) -> None:
        self._payload = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)

    def complete(self, system: str, user: str) -> str:
        return self._payload


# ---------------- _name_faithful ----------------
def test_name_faithful_judgement():
    p = Planner(_repo(), llm=None, theme="x")
    canon = ["霞飞路", "七十六号", "百乐门"]
    assert p._name_faithful("霞飞路", canon)                 # 等于 canon
    assert p._name_faithful("霞飞路·梧桐里咖啡馆", canon)     # 从属子地点
    assert p._name_faithful("七十六号审讯室", canon)          # canon 名为子串
    assert not p._name_faithful("镜面塔", canon)             # 与设定无关的奇幻地名
    assert not p._name_faithful("遮面街", canon)
    assert not p._name_faithful("", canon)


# ---------------- lock_canonical_geography ----------------
def test_lock_canonical_geography_extracts_and_is_idempotent():
    r = _repo()
    r.add_bible_section("geography", "", "1939 上海孤岛：法租界霞飞路、七十六号特工总部、百乐门舞厅、闸北废墟。")
    llm = _FixedLLM([
        {"name": "霞飞路", "geo_full": "法租界主干道，梧桐成荫。", "controlling_faction": "法租界巡捕房"},
        {"name": "七十六号", "geo_full": "特工总部，铁门森严。", "controlling_faction": "七十六号"},
        {"name": "百乐门", "geo_full": "纸醉金迷的舞厅。", "controlling_faction": ""},
    ])
    made = lock_canonical_geography(r, llm=llm)
    assert len(made) == 3
    canon = [e for e in r.list_entities() if e.type == "location" and e.attributes.get("canon")]
    assert {e.name for e in canon} == {"霞飞路", "七十六号", "百乐门"}
    # 幂等：再调一次不再新建
    assert lock_canonical_geography(r, llm=llm) == []
    canon2 = [e for e in r.list_entities() if e.type == "location" and e.attributes.get("canon")]
    assert len(canon2) == 3


def test_lock_canonical_geography_noop_without_llm_or_geo():
    r = _repo()
    assert lock_canonical_geography(r, llm=None) == []            # 无 LLM → 绝不臆造
    assert lock_canonical_geography(r, llm=_FixedLLM([])) == []    # 无地理文本 → no-op


# ---------------- 保真闸门：拒绝冲突奇幻地名，回退 canon ----------------
def test_conflicting_fantasy_names_rejected_and_fallback_to_canon():
    r = _with_canon(_repo())
    llm = _FixedLLM([
        {"name": "镜面塔", "geo_full": "水银镜墙的奇幻高塔，与孤岛设定毫无关系，深不见底。", "controlling_faction": ""},
        {"name": "遮面街", "geo_full": "雾气弥漫的诡异长街，戴面具者穿行其间，非真实地名。", "controlling_faction": ""},
    ])
    p = Planner(r, llm=llm, theme="孤岛")
    specs = p._part_locations({"region": "法租界", "goal": "潜伏"})
    names = {s["name"] for s in specs}
    assert names <= {"霞飞路", "七十六号"}              # 回退到 canon，绝不出现奇幻地名
    assert "镜面塔" not in names and "遮面街" not in names

    # materialize 复用 canon 实体，不新建奇幻地点
    p._materialize_part_locations("part_x", {"region": "法租界", "goal": "潜伏"})
    all_names = {e.name for e in r.list_entities() if e.type == "location"}
    assert "镜面塔" not in all_names and "遮面街" not in all_names


# ---------------- 保真闸门：从属子地点放行 + 标注 parent_canon ----------------
def test_subordinate_sublocation_accepted_and_tagged():
    r = _with_canon(_repo())
    llm = _FixedLLM([
        {"name": "霞飞路·梧桐里咖啡馆", "geo_full": "霞飞路旁的二层小咖啡馆，常有人接头。", "controlling_faction": ""},
        {"name": "七十六号·审讯室", "geo_full": "地下一层的审讯室，灯光惨白。", "controlling_faction": "七十六号"},
    ])
    p = Planner(r, llm=llm, theme="孤岛")
    p._materialize_part_locations("part_y", {"region": "法租界", "goal": "审讯"})
    part_locs = {e.name: e for e in r.list_entities()
                 if e.type == "location" and e.attributes.get("part") == "part_y"}
    assert "霞飞路·梧桐里咖啡馆" in part_locs
    # 子地点标注从属的 canon 父地点
    assert part_locs["霞飞路·梧桐里咖啡馆"].attributes.get("parent_canon") == "loc_xfl"
