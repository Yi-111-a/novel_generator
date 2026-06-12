"""§1 选角层：出生即建卡、功能位唯一、与 persona/agent 打通。"""
from __future__ import annotations

from novel_engine import db
from novel_engine.agent import CharacterAgent
from novel_engine.casting import cast_or_get, ensure_cards_for_personas
from novel_engine.llm.mock import MockClient
from novel_engine.models import Entity, Persona
from novel_engine.repository import Repository


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
