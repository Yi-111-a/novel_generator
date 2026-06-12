"""§4.2 情绪余温：跨场传递、注入提示词、随 decay 衰减回落。"""
from __future__ import annotations

from novel_engine import db
from novel_engine.agent import CharacterAgent
from novel_engine.engine import Engine
from novel_engine.llm.mock import MockClient
from novel_engine.models import Entity, Persona
from novel_engine.repository import Repository
from novel_engine.validator import Validator


def _repo() -> Repository:
    r = Repository(db.connect(":memory:"))
    r.insert_entity(Entity("hero", "character", "云鹤子", {}))
    r.insert_persona(Persona(agent_id="hero", name="云鹤子"))
    return r


def test_residue_text_and_decay():
    r = _repo()
    r.bump_emotion("hero", "沉重", 0.6, cause="牺牲了「旧情」", tick=1, decay=0.25)
    txt = r.emotional_residue_text("hero")
    assert "沉重" in txt and "旧情" in txt
    # 随拍衰减：几拍后回落到平复（空）
    for _ in range(3):
        r.decay_emotions()
    assert r.emotional_residue_text("hero") == ""


def test_residue_injected_into_prompt():
    r = _repo()
    r.bump_emotion("hero", "愧疚", 0.8, cause="见死不救", tick=1)
    sys = CharacterAgent(r, MockClient()).build_system_prompt(
        "hero", [], "下一场", r.emotional_residue_text("hero")
    )
    assert "愧疚" in sys and "见死不救" in sys  # 上一场情绪带进了下一场


def test_engine_reads_residue():
    r = _repo()
    r.bump_emotion("hero", "亢奋", 0.7, cause="侥幸逃生", tick=1)
    eng = Engine(r, CharacterAgent(r, MockClient()), Validator(r))
    assert "亢奋" in eng._emotional_residue("hero")
    # 无情绪的角色 → 空
    r.insert_entity(Entity("x", "character", "路人", {}))
    r.insert_persona(Persona(agent_id="x", name="路人"))
    assert eng._emotional_residue("x") == ""
