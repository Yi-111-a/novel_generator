"""§3 出戏检测：明显违背本性且无外因的动作被拦/重抽；离线宽松不拦。"""
from __future__ import annotations

from novel_engine import db
from novel_engine.agent import CharacterAgent
from novel_engine.consistency import InCharacterChecker
from novel_engine.engine import Engine
from novel_engine.llm.mock import MockClient
from novel_engine.models import Action, Entity, Persona
from novel_engine.validator import Validator
from novel_engine.repository import Repository


def _repo() -> Repository:
    r = Repository(db.connect(":memory:"))
    r.insert_entity(Entity("hero", "character", "云鹤子", {}))
    r.insert_persona(Persona(agent_id="hero", name="云鹤子", want="查真相",
                             fatal_flaw="怯懦避战", values=[{"name": "活命", "weight": 0.8}]))
    return r


def test_offline_checker_is_lenient():
    r = _repo()
    chk = InCharacterChecker(r, llm=None)  # 无 LLM → 永不拦
    ok, _ = chk.check("hero", Action(intent="独自冲阵搏命"))
    assert ok


def test_llm_checker_flags_out_of_character():
    r = _repo()
    chk = InCharacterChecker(r, MockClient(['{"ooc": true, "reason": "怯懦者无端搏命"}']))
    ok, reason = chk.check("hero", Action(intent="独自冲阵搏命"))
    assert not ok and "怯懦" in reason


def test_llm_checker_passes_in_character():
    r = _repo()
    chk = InCharacterChecker(r, MockClient(['{"ooc": false}']))
    ok, _ = chk.check("hero", Action(intent="悄悄退到人群后"))
    assert ok


def test_engine_retries_on_ooc_then_commits():
    r = _repo()
    # agent 总产出同一个（合法但"出戏"）动作；consistency 总判 ooc
    agent = CharacterAgent(r, MockClient.from_actions([{"intent": "冲阵", "referenced_entities": []}]))
    chk = InCharacterChecker(r, MockClient(['{"ooc": true, "reason": "x"}']))
    eng = Engine(r, agent, Validator(r), max_retries=2, consistency=chk)
    res = eng.run_tick("hero", "强敌当前", ["hero"])
    # 出戏触发重抽：用满重抽次数；最终仍提交（不丢拍，校验本身通过）
    assert res.attempts == 3
    assert res.committed
