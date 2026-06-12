"""第四批修复：隐藏身份称谓闸门（问题5）——未揭示只能用中性称呼，揭示后才可用真实头衔。"""
from __future__ import annotations

from novel_engine import db
from novel_engine.casting import lock_hidden_identities
from novel_engine.models import Entity, Fact, KnowledgeItem, Persona, ReaderKnowledge
from novel_engine.narration.narrator import Narrator
from novel_engine.repository import Repository


def _repo() -> Repository:
    r = Repository(db.connect(":memory:"))
    r.insert_entity(Entity("hero", "character", "沈砚", {}))
    r.insert_persona(Persona(agent_id="hero", name="沈砚"))
    # 苏静：表面书局女子，实则七十六号队长；身份 fact 只有她自己知道
    r.insert_entity(Entity("foe", "character", "苏静",
                           {"identity": {"public": "书局的女子", "true": "苏队长",
                                         "fact_id": "f_idn"}}))
    r.insert_persona(Persona(agent_id="foe", name="苏静"))
    r.append_fact(Fact("f_idn", "state", "苏静是七十六号特工队长", involved_entities=["foe"]))
    return r


def _narrator(r: Repository) -> Narrator:
    n = Narrator.__new__(Narrator)
    n.repo = r
    return n


# ---- 无 LLM 不臆造身份 ----
def test_lock_hidden_identities_no_llm_returns_empty():
    r = _repo()
    assert lock_hidden_identities(r, llm=None) == {}


# ---- 未揭示：只能用中性称呼，禁止真实头衔 ----
def test_appellation_locked_before_reveal():
    r = _repo()
    n = _narrator(r)
    lines = n._identity_lines(["hero", "foe"], pov="hero", pov_fids=set(), reader_fids=set())
    assert len(lines) == 1
    line = lines[0]
    assert "书局的女子" in line
    assert "尚未揭示" in line
    assert "苏队长" in line  # 出现是为了禁止，但措辞应为"绝不可称"
    assert "绝不可" in line


# ---- 主角已知该身份 fact → 解锁，可用真实头衔 ----
def test_appellation_unlocked_when_pov_knows():
    r = _repo()
    n = _narrator(r)
    lines = n._identity_lines(["hero", "foe"], pov="hero",
                              pov_fids={"f_idn"}, reader_fids=set())
    assert any("已揭示" in l and "苏队长" in l for l in lines)
    assert not any("绝不可" in l for l in lines)


# ---- 读者已知（reader_knowledge）→ 也解锁 ----
def test_appellation_unlocked_when_reader_knows():
    r = _repo()
    n = _narrator(r)
    lines = n._identity_lines(["foe"], pov="hero", pov_fids=set(), reader_fids={"f_idn"})
    assert any("已揭示" in l for l in lines)


# ---- POV 是本人 → 自然解锁（知道自己是谁）----
def test_appellation_unlocked_for_self_pov():
    r = _repo()
    n = _narrator(r)
    lines = n._identity_lines(["foe"], pov="foe", pov_fids=set(), reader_fids=set())
    assert any("已揭示" in l for l in lines)


# ---- 无隐藏身份的普通角色 → 不产生约束行 ----
def test_no_identity_no_lines():
    r = _repo()
    n = _narrator(r)
    lines = n._identity_lines(["hero"], pov="hero", pov_fids=set(), reader_fids=set())
    assert lines == []
