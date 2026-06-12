"""W3 势力系统：build_factions 多级生成 + 关系图 + 核心成员落卡。"""
from __future__ import annotations

import json
import re

from novel_engine import db
from novel_engine.llm.base import LLMClient
from novel_engine.models import Entity, Faction, Location
from novel_engine.repository import Repository
from novel_engine.worldbible import build_factions


class _FacLLM(LLMClient):
    def __init__(self, n=3, issues=None, edges=None) -> None:
        self.n = n
        self.issues = issues or []
        self.edges = edges  # None → 自动生成；[] → 显式空
        self.calls = []
        self._fac_ids: list[str] = []  # 记录被深写的势力，校验阶段用

    def complete(self, system: str, user: str) -> str:
        self.calls.append(system[:40])
        if "【任务：总览】" in system:
            # 解析 canon loc_id 用作 territory
            lids = re.findall(r"loc_id=([A-Za-z0-9_]+)", user)
            facs = [{"name": f"势力{i}", "ideology": f"信条{i}",
                     "territory": [lids[i % len(lids)]] if lids else []}
                    for i in range(self.n)]
            return json.dumps({"factions": facs}, ensure_ascii=False)
        if "【任务：深写】" in system:
            # 从 system/user 抽出本势力名，确保成员名全局唯一
            m = re.search(r"势力「([^」]+)」", system)
            tag = m.group(1) if m else "X"
            return json.dumps({
                "summary": "势力速览一句", "detail": "详" * 100,
                "goals": "目标", "methods": "手段",
                "structure": "架构", "history": "历史", "secret": "秘密",
                "key_members": [
                    {"name": f"{tag}-A", "role": "领袖", "note": "心狠"},
                    {"name": f"{tag}-B", "role": "副官", "note": "细心"},
                ],
            }, ensure_ascii=False)
        if "【任务：校验】" in system:
            return json.dumps({"issues": self.issues}, ensure_ascii=False)
        if "【任务：修订】" in system:
            return json.dumps({"summary": "修订速览", "detail": "修订" * 80,
                               "goals": "修订目标", "methods": "修订手段"},
                              ensure_ascii=False)
        if "【任务：关系】" in system:
            if self.edges is not None:
                return json.dumps({"edges": self.edges}, ensure_ascii=False)
            fids = re.findall(r"faction_id=([A-Za-z0-9_]+)", user)
            edges = []
            if len(fids) >= 2:
                edges.append({"src": fids[0], "dst": fids[1], "kind": "hostile",
                              "intensity": 5, "note": "敌对"})
            if len(fids) >= 3:
                edges.append({"src": fids[0], "dst": fids[2], "kind": "allied",
                              "intensity": 3, "note": "结盟"})
            return json.dumps({"edges": edges}, ensure_ascii=False)
        return "{}"


def _repo_with_canon_geo(n: int = 3) -> Repository:
    r = Repository(db.connect(":memory:"))
    r.add_bible_section("settingCore", "设定", "1939 上海孤岛。", source="user")
    r.upsert_w1_section("settingCore", "设定", "孤岛谍战", "全文")
    r.upsert_w1_section("culture", "文化", "三教九流杂处", "全文")
    for i in range(n):
        lid = f"loc_g{i}"
        r.insert_entity(Entity(lid, "location", f"地点{i}", {"canon": True}))
        r.upsert_location(Location(loc_id=lid, name=f"地点{i}",
                                   geo_full=f"原 g{i}", summary=f"sum{i}"))
    return r


# ---------------- build_factions 主流程 ----------------
def test_build_factions_creates_first_class_entities():
    r = _repo_with_canon_geo(3)
    res = build_factions(r, llm=_FacLLM(n=3), theme="孤岛谍战")
    assert res["factions"] == 3
    facs = r.list_factions()
    assert len(facs) == 3
    assert all(f.summary and f.detail and f.goals and f.history for f in facs)
    # 地盘都在 canon
    canon_ids = {e.entity_id for e in r.list_entities()
                 if e.type == "location" and (e.attributes or {}).get("canon")}
    for f in facs:
        for t in f.territory:
            assert t in canon_ids


def test_relations_graph_built():
    r = _repo_with_canon_geo(3)
    res = build_factions(r, llm=_FacLLM(n=3), theme="x")
    assert res["relations"] >= 1
    rels = [rel for f in r.list_factions() for rel in f.relations]
    assert any(r0["kind"] in ("hostile", "allied") for r0 in rels)
    # 关系指向的也是真实 faction_id
    fids = {f.faction_id for f in r.list_factions()}
    for rel in rels:
        assert rel["target_faction_id"] in fids


def test_key_members_create_supporting_cards():
    r = _repo_with_canon_geo(2)
    res = build_factions(r, llm=_FacLLM(n=2), theme="x")
    assert res["members_created"] == 4   # 2 势力 × 2 成员
    cards = [c for c in r.list_cards() if c.tier == "supporting"]
    assert len(cards) == 4
    # 卡 agent_id 写回了 faction.key_members
    for f in r.list_factions():
        for m in f.key_members:
            assert m.get("agent_id", "").startswith("named_")


def test_build_idempotent():
    r = _repo_with_canon_geo(2)
    llm = _FacLLM(n=2)
    build_factions(r, llm=llm, theme="x")
    n1 = len(llm.calls)
    assert build_factions(r, llm=llm, theme="x") == {"skipped": "exists"}
    assert len(llm.calls) == n1


def test_noop_without_llm_or_canon():
    assert build_factions(_repo_with_canon_geo(1), llm=None)["skipped"] == "no_llm"
    empty = Repository(db.connect(":memory:"))
    empty.add_bible_section("settingCore", "x", "y", source="user")
    assert build_factions(empty, llm=_FacLLM())["skipped"] == "no_canon"


def test_review_loop_applies_fix():
    r = _repo_with_canon_geo(2)
    issues = [{"faction_index": 0, "problem": "地盘冲突", "fix": "改地盘"}]
    res = build_factions(r, llm=_FacLLM(n=2, issues=issues), theme="x")
    assert res["issues"] == 1 and res["fixed"] == 1
    facs = sorted(r.list_factions(), key=lambda f: f.faction_id)
    # 第一个被修订
    assert any(f.summary == "修订速览" for f in facs)


def test_invalid_relations_rejected():
    r = _repo_with_canon_geo(2)
    bad_edges = [
        {"src": "fac_FAKE", "dst": "fac_FAKE2", "kind": "hostile", "intensity": 3, "note": ""},
        {"src": "x", "dst": "x", "kind": "weirdkind", "intensity": 9, "note": ""},
    ]
    res = build_factions(r, llm=_FacLLM(n=2, edges=bad_edges), theme="x")
    assert res["relations"] == 0  # 全被拒
    for f in r.list_factions():
        assert f.relations == []


def test_faction_summaries_text_for_injection():
    r = _repo_with_canon_geo(2)
    build_factions(r, llm=_FacLLM(n=2), theme="x")
    text = r.faction_summaries_text()
    assert text.count("·") >= 2   # 至少两行 summary


def test_faction_roundtrip():
    r = Repository(db.connect(":memory:"))
    r.upsert_faction(Faction(
        faction_id="fac_x", name="测试", ideology="信", goals="目标",
        methods="手段", territory=["loc_a"], structure="架构",
        key_members=[{"name": "A", "role": "头", "agent_id": "aid"}],
        history="史", relations=[{"target_faction_id": "fac_y", "kind": "hostile",
                                  "intensity": 4, "note": ""}],
        secret="秘", summary="速", detail="详", source="w3",
    ))
    got = r.get_faction("fac_x")
    assert got.name == "测试" and got.territory == ["loc_a"]
    assert got.key_members[0]["agent_id"] == "aid"
    assert got.relations[0]["kind"] == "hostile"
