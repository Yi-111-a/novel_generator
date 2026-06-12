"""W2 地理层：canon 地点加厚（两级 summary/detail + 风土人情 + 层级 + 校验回路）。"""
from __future__ import annotations

import json
import re

from novel_engine import db
from novel_engine.llm.base import LLMClient
from novel_engine.models import Entity, Location
from novel_engine.repository import Repository
from novel_engine.worldbible import build_geography


class _GeoLLM(LLMClient):
    """脚本化各阶段响应：总览/深写/校验/修订。"""

    def __init__(self, issues=None) -> None:
        self.issues = issues or []
        self.calls = []

    def _ids(self, user: str) -> list[str]:
        return re.findall(r"loc_id=([A-Za-z0-9_]+)", user)

    def complete(self, system: str, user: str) -> str:
        self.calls.append(system[:40])
        if "【任务：总览】" in system:
            ids = self._ids(user)
            return json.dumps({lid: {"level": "街道", "parent": "",
                                     "summary": f"{lid}的一句话定位。"} for lid in ids},
                              ensure_ascii=False)
        if "【任务：深写】" in system:
            return json.dumps({"detail": "深写" * 60, "culture_local": "本地风土" * 20},
                              ensure_ascii=False)
        if "【任务：校验】" in system:
            return json.dumps({"issues": self.issues}, ensure_ascii=False)
        if "【任务：修订】" in system:
            return json.dumps({"summary": "修订后定位", "detail": "修订" * 80,
                               "culture_local": "修订风土" * 20}, ensure_ascii=False)
        return "{}"


def _repo_with_canon(n: int = 2) -> Repository:
    r = Repository(db.connect(":memory:"))
    r.add_bible_section("settingCore", "设定内核", "1939 上海孤岛。", source="user")
    r.add_bible_section("geography", "地理", "霞飞路、七十六号。", source="user")
    # 用 W1 路径补 summary 锚
    r.upsert_w1_section("settingCore", "设定内核", "孤岛谍战", "孤岛设定全文")
    r.upsert_w1_section("geography", "地理", "公共租界与法租界", "地理全文")
    # 模拟 W0 已固化的 canon 地点
    for i in range(n):
        lid = f"loc_c{i}"
        r.insert_entity(Entity(lid, "location", f"地点{i}", {"canon": True}))
        r.upsert_location(Location(loc_id=lid, part_id="", name=f"地点{i}",
                                   geo_full=f"原 geo_full{i}",
                                   controlling_faction="某势力"))
    return r


# ---------------- build_geography ----------------
def test_build_geography_two_level():
    r = _repo_with_canon(2)
    res = build_geography(r, llm=_GeoLLM(), theme="孤岛谍战")
    assert res["locations"] == 2
    locs = [l for l in r.list_locations() if l.loc_id.startswith("loc_c")]
    assert all(l.summary.strip() and l.detail.strip() for l in locs)        # 两级齐全
    assert all(l.culture_local.strip() for l in locs)                       # 风土人情
    assert all(l.level for l in locs)                                       # 层级


def test_build_geography_is_idempotent():
    r = _repo_with_canon(2)
    llm = _GeoLLM()
    build_geography(r, llm=llm, theme="x")
    n_calls_1 = len(llm.calls)
    assert build_geography(r, llm=llm, theme="x") == {"skipped": "exists"}
    assert len(llm.calls) == n_calls_1                                      # 不再调 LLM


def test_build_geography_noop():
    # 无 LLM
    assert build_geography(_repo_with_canon(1), llm=None)["skipped"] == "no_llm"
    # 无 canon 地点
    empty = Repository(db.connect(":memory:"))
    empty.add_bible_section("settingCore", "x", "y", source="user")
    assert build_geography(empty, llm=_GeoLLM())["skipped"] == "no_canon"


def test_review_loop_applies_fix():
    r = _repo_with_canon(2)
    issues = [{"loc_id": "loc_c0", "problem": "与世界观冲突", "fix": "用真实租界地点"}]
    res = build_geography(r, llm=_GeoLLM(issues=issues), theme="孤岛")
    assert res["issues"] == 1 and res["fixed"] == 1
    fixed = next(l for l in r.list_locations() if l.loc_id == "loc_c0")
    assert fixed.summary == "修订后定位"
    assert fixed.detail.startswith("修订")


def test_existing_geo_full_preserved_when_llm_empty():
    """LLM 深写失败时 detail 回落 geo_full（不丢原数据）。"""
    class _NullLLM(_GeoLLM):
        def complete(self, system, user):
            if "【任务：深写】" in system:
                return "garbage not json"
            return super().complete(system, user)
    r = _repo_with_canon(1)
    build_geography(r, llm=_NullLLM(), theme="x")
    loc = next(l for l in r.list_locations() if l.loc_id == "loc_c0")
    assert "原 geo_full" in loc.detail                                       # 回落保留


# ---------------- 迁移 ----------------
def test_migration_adds_w2_columns():
    conn = db.connect(":memory:")
    conn.execute("DROP TABLE locations")
    conn.execute("CREATE TABLE locations (loc_id TEXT PRIMARY KEY, part_id TEXT, "
                 "name TEXT, geo_full TEXT, connects_to TEXT NOT NULL DEFAULT '[]', "
                 "controlling_faction TEXT, notable_items TEXT NOT NULL DEFAULT '[]')")
    db._migrate(conn)
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(locations)").fetchall()}
    for col in ("level", "parent", "culture_local", "summary", "detail"):
        assert col in cols


def test_location_roundtrip_includes_w2_fields():
    r = Repository(db.connect(":memory:"))
    r.upsert_location(Location(loc_id="x1", name="L", geo_full="g",
                               level="街道", parent="p1",
                               culture_local="风土", summary="速览", detail="详情"))
    loc = r.get_location("x1")
    assert loc.level == "街道" and loc.parent == "p1"
    assert loc.summary == "速览" and loc.detail == "详情" and loc.culture_local == "风土"
