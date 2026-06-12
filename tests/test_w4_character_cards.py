"""W4 分层人物卡：主角极详 + 主配加厚（三维度+小传+弧线+校验回路）。"""
from __future__ import annotations

import json
import re

from novel_engine import db
from novel_engine.casting import enrich_character_cards
from novel_engine.llm.base import LLMClient
from novel_engine.models import CharacterCard, Entity, Faction, Persona
from novel_engine.repository import Repository


class _CharLLM(LLMClient):
    def __init__(self, issues=None) -> None:
        self.issues = issues or []
        self.calls = []

    def complete(self, system: str, user: str) -> str:
        self.calls.append(system[:48])
        if "【任务：主角极详】" in system:
            return json.dumps({
                "appearance": "极详外貌" * 30,
                "social_role": "极详社会" * 30,
                "psychology": "极详心理" * 30,
                "backstory": "极详小传" * 40,
                "arc": "起点→中段→终点",
            }, ensure_ascii=False)
        if "【任务：主配加厚】" in system:
            m = re.search(r"角色「([^」]+)」", system)
            tag = m.group(1) if m else "X"
            return json.dumps({
                "appearance": f"{tag}的外貌" * 6,
                "social_role": f"{tag}的社会" * 6,
                "psychology": f"{tag}的心理" * 6,
                "backstory": f"{tag}的小传" * 8,
                "arc": f"{tag}的弧线",
            }, ensure_ascii=False)
        if "【任务：人物校验】" in system:
            return json.dumps({"issues": self.issues}, ensure_ascii=False)
        if "【任务：人物修订】" in system:
            return json.dumps({
                "appearance": "修订外貌" * 30, "social_role": "修订社会" * 30,
                "psychology": "修订心理" * 30, "backstory": "修订小传" * 40,
                "arc": "修订弧线",
            }, ensure_ascii=False)
        return "{}"


def _repo_with_lead_and_supports() -> Repository:
    r = Repository(db.connect(":memory:"))
    # 世界观锚
    r.upsert_w1_section("settingCore", "设定", "孤岛谍战", "全文")
    r.upsert_w1_section("culture", "文化", "三教九流杂处", "全文")
    # 势力
    r.upsert_faction(Faction(faction_id="fac_a", name="七十六号", summary="敌方",
                             detail="d", source="w3"))
    # 主角 persona + lead 卡
    aid_lead = "p_lead"
    r.insert_persona(Persona(agent_id=aid_lead, name="沈砚", want="送出名单",
                             values=[{"name": "忠诚", "weight": 0.9}],
                             fatal_flaw="心软", voice="冷峻", mannerisms=["紧领结"]))
    r.insert_entity(Entity(aid_lead, "character", "沈砚", {}))
    r.add_card(CharacterCard(card_id="card_lead", agent_id=aid_lead, tier="lead",
                             slot_key="seed_lead", name="沈砚", one_liner="潜伏者",
                             defining_trait="冷峻"))
    # 两张主配卡（一张挂势力，一张不挂）
    for i, (name, fac) in enumerate([("赵九", "fac_a"), ("苏静", "")]):
        aid = f"p_sup{i}"
        attrs = {"faction_id": fac} if fac else {}
        r.insert_entity(Entity(aid, "character", name, attrs))
        r.add_card(CharacterCard(card_id=f"card_sup{i}", agent_id=aid, tier="supporting",
                                 slot_key=f"faction:七十六号:{name}", name=name,
                                 one_liner=f"{name}·配角"))
    return r


def test_enrich_lead_three_dimensions():
    r = _repo_with_lead_and_supports()
    res = enrich_character_cards(r, llm=_CharLLM(), theme="孤岛")
    assert res["leads"] == 1 and res["supports"] == 2
    lead = next(c for c in r.list_cards() if c.tier == "lead")
    assert len(lead.appearance) >= 80
    assert len(lead.social_role) >= 80
    assert len(lead.psychology) >= 80
    assert len(lead.backstory) >= 100
    assert lead.arc


def test_enrich_supports_thickened():
    r = _repo_with_lead_and_supports()
    enrich_character_cards(r, llm=_CharLLM(), theme="x")
    sups = [c for c in r.list_cards() if c.tier == "supporting"]
    for c in sups:
        assert c.appearance and c.social_role and c.psychology
        assert c.backstory and c.arc


def test_idempotent_when_appearance_set():
    r = _repo_with_lead_and_supports()
    llm = _CharLLM()
    enrich_character_cards(r, llm=llm, theme="x")
    n1 = len(llm.calls)
    assert enrich_character_cards(r, llm=llm, theme="x") == {"skipped": "exists"}
    assert len(llm.calls) == n1


def test_noop_without_llm_or_lead():
    assert enrich_character_cards(_repo_with_lead_and_supports(), llm=None)["skipped"] == "no_llm"
    empty = Repository(db.connect(":memory:"))
    assert enrich_character_cards(empty, llm=_CharLLM())["skipped"] == "no_cards"


def test_review_loop_applies_fix():
    r = _repo_with_lead_and_supports()
    issues = [{"name": "沈砚", "problem": "与世界观冲突", "fix": "改一下"}]
    res = enrich_character_cards(r, llm=_CharLLM(issues=issues), theme="x")
    assert res["issues"] == 1 and res["fixed"] == 1
    lead = next(c for c in r.list_cards() if c.tier == "lead")
    assert lead.appearance.startswith("修订外貌")


def test_extras_skipped():
    r = _repo_with_lead_and_supports()
    # 加一个 extra 卡
    r.add_card(CharacterCard(card_id="card_extra", agent_id="p_x", tier="extra",
                             slot_key="extra_x", name="路人甲", one_liner="过路"))
    enrich_character_cards(r, llm=_CharLLM(), theme="x")
    extra = next(c for c in r.list_cards() if c.tier == "extra")
    assert extra.appearance == ""  # 龙套未被加厚


def test_migration_adds_w4_columns():
    conn = db.connect(":memory:")
    conn.execute("DROP TABLE character_cards")
    conn.execute("CREATE TABLE character_cards (card_id TEXT PRIMARY KEY, agent_id TEXT, "
                 "tier TEXT, slot_key TEXT UNIQUE, name TEXT, one_liner TEXT, "
                 "voice_register TEXT, defining_trait TEXT, core_desire TEXT, "
                 "verbal_habits TEXT, key_relation TEXT, backstory TEXT, fatal_flaw TEXT, "
                 "motif_objects TEXT NOT NULL DEFAULT '[]', "
                 "relationship_map TEXT NOT NULL DEFAULT '{}', arc TEXT, created_at INTEGER)")
    db._migrate(conn)
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(character_cards)").fetchall()}
    for c in ("appearance", "social_role", "psychology"):
        assert c in cols


def test_card_roundtrip():
    r = Repository(db.connect(":memory:"))
    r.add_card(CharacterCard(card_id="cid", agent_id="aid", tier="lead",
                             slot_key="slot1", name="X",
                             appearance="A 字段", social_role="S 字段", psychology="P 字段"))
    got = r.get_card_by_slot("slot1")
    assert got.appearance == "A 字段" and got.social_role == "S 字段" and got.psychology == "P 字段"
