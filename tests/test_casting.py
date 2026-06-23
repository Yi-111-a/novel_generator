"""§1 选角层：出生即建卡、功能位唯一、与 persona/agent 打通。"""
from __future__ import annotations

from novel_engine import db
from novel_engine.agent import CharacterAgent
from novel_engine.casting import cast_or_get, ensure_cards_for_personas, enrich_character_cards
from novel_engine.llm.base import LLMClient
from novel_engine.llm.mock import MockClient
from novel_engine.models import Entity, Persona
from novel_engine.repository import Repository


class _Cap(LLMClient):
    """记录所有 (system, user) 调用，用于校验 KV-cache 前缀稳定性。"""

    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def complete(self, s, u):
        return self.complete_at(s, u)

    def complete_at(self, s, u, temperature=None):
        self.calls.append((s, u))
        return "{}"


def _repo() -> Repository:
    return Repository(db.connect(":memory:"))


def test_slot_is_unique_no_respawn():
    r = _repo()
    c1 = cast_or_get(r, "tavern_old_waiter", tier="extra")
    c2 = cast_or_get(r, "tavern_old_waiter", tier="extra")
    assert c1.card_id == c2.card_id and c1.agent_id == c2.agent_id  # 同 slot 永不重抽
    # 落了真实 entity + persona（可被扮演）
    assert r.entity_exists(c1.agent_id)
    assert r.get_persona(c1.agent_id) is not None
    assert r.get_card_for_agent(c1.agent_id).slot_key == "tavern_old_waiter"


def test_motif_lands_in_inventory():
    r = _repo()
    c = cast_or_get(r, "masked_envoy", tier="lead",
                    llm=MockClient(['{"name":"墨使","one_liner":"蒙面信使","voice_register":"低哑",'
                                    '"defining_trait":"从不摘面具","core_desire":"送达密信","fatal_flaw":"多疑",'
                                    '"motif_objects":["黑漆木匣"]}']))
    assert "黑漆木匣" in c.motif_objects
    assert r.agent_holds(c.agent_id, "黑漆木匣")


def test_enrich_card_system_prefix_stable_across_characters():
    """KV-cache 不变量：人物卡加厚时，system 不含角色名 → 同 tier 的多个角色
    共享逐字节一致的 system 前缀，命中 DeepSeek 自动前缀缓存。角色名只在 user 里。"""
    r = _repo()
    r.add_bible_section("settingCore", "设定内核", "一个门派林立的江湖世界。")
    for slot, name in (("lead_a", "云鹤子"), ("lead_b", "季拾遗")):
        cast_or_get(r, slot, tier="lead",
                    llm=MockClient([f'{{"name":"{name}","one_liner":"x","defining_trait":"y"}}']))
    cap = _Cap()
    enrich_character_cards(r, llm=cap)
    lead_systems = [s for s, _ in cap.calls if "主角极详" in s]
    assert len(lead_systems) >= 2                       # 两个主角都加厚了
    assert len(set(lead_systems)) == 1                  # system 跨角色逐字节一致
    assert all("云鹤子" not in s and "季拾遗" not in s for s in lead_systems)  # 角色名不在 system
    assert any("云鹤子" in u for _, u in cap.calls)     # 角色名在 user


def test_ensure_cards_for_personas_tiers():
    r = _repo()
    for aid, name in [("hero", "云鹤子"), ("ally", "季拾遗")]:
        r.insert_entity(Entity(aid, "character", name, {}))
        r.insert_persona(Persona(agent_id=aid, name=name, want="求道", voice="冷峻",
                                 values=[{"name": "道心", "weight": 0.8}], mannerisms=["以指叩盘"]))
    ensure_cards_for_personas(r)
    assert r.get_card_for_agent("hero").tier == "lead"
    assert r.get_card_for_agent("ally").tier == "supporting"
    # 幂等：再调不重复建
    ensure_cards_for_personas(r)
    assert len([c for c in r.list_cards() if c.agent_id == "hero"]) == 1


def test_agent_prompt_uses_card_identity():
    r = _repo()
    cast_or_get(r, "gate_guard", tier="supporting",
                llm=MockClient(['{"name":"铁山","one_liner":"北门守卫","voice_register":"粗声大气",'
                                '"defining_trait":"认死理","core_desire":"守好城门","verbal_habits":"嗯、哼"}']))
    card = r.get_card_by_slot("gate_guard")
    sys = CharacterAgent(r, MockClient()).build_system_prompt(card.agent_id, [], "有人闯关")
    assert "铁山" in sys and "北门守卫" in sys and "粗声大气" in sys  # 入戏式提示词取自卡
