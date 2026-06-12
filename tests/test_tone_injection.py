"""§16.5 文风前置块注入表演层(agent)与渲染层(narrator)；§16.4 tone_gate 重渲。"""
from __future__ import annotations

from novel_engine import db
from novel_engine.agent import CharacterAgent
from novel_engine.llm.base import LLMClient
from novel_engine.models import Entity, Event, Persona, ToneProfile
from novel_engine.narration.narrator import Narrator
from novel_engine.repository import Repository
from novel_engine.tone import build_tone_profile


class _CapSysLLM(LLMClient):
    def __init__(self, reply: str = "他停在原地，没有说话。") -> None:
        self.reply, self.last_system, self.last_user = reply, "", ""

    def complete(self, system: str, user: str) -> str:
        self.last_system, self.last_user = system, user
        return self.reply


def _repo() -> Repository:
    r = Repository(db.connect(":memory:"))
    r.insert_entity(Entity("hero", "character", "云鹤子", {}))
    r.insert_persona(Persona(agent_id="hero", name="云鹤子", want="求道", voice="清冷"))
    return r


def test_agent_prompt_has_tone_block_when_set():
    r = _repo()
    build_tone_profile(r, llm=None, genre="horror")
    agent = CharacterAgent(r, _CapSysLLM())
    sys = agent.build_system_prompt("hero", ["hero"], situation="门后有声响")
    assert "文风契约" in sys and "dread" in sys
    assert "自检" in sys  # §16.5 尾注


def test_agent_prompt_no_tone_block_when_unset():
    r = _repo()
    agent = CharacterAgent(r, _CapSysLLM())
    sys = agent.build_system_prompt("hero", ["hero"], situation="门后有声响")
    assert "文风契约" not in sys


def test_narrator_system_has_tone_block():
    r = _repo()
    build_tone_profile(r, llm=None, genre="comedy")
    llm = _CapSysLLM()
    Narrator(r, llm).render("hero", [Event("e1", 1, ["hero"], "试探")], "", [], [])
    assert "文风契约" in llm.last_system and "laugh" in llm.last_system


def test_tone_gate_triggers_rerender_on_banned_word():
    r = _repo()
    # 设一个禁忌词，且让首稿命中它 → 触发一次重渲
    r.set_tone_profile(ToneProfile(genre="horror", primary_effect="dread",
                                   diction_dont=["哈哈"]))

    class _TwoShot(LLMClient):
        def __init__(self) -> None:
            self.n = 0

        def complete(self, system: str, user: str) -> str:
            self.n += 1
            return "他哈哈大笑起来。" if self.n == 1 else "走廊尽头传来呼吸声。"

    llm = _TwoShot()
    out = Narrator(r, llm).render("hero", [Event("e1", 1, ["hero"], "试探")], "", [], [])
    assert llm.n >= 2           # 触发了重渲
    assert "哈哈" not in out     # 最终稿不含禁忌词
