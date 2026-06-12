"""§4.3 叙述嗓音连续性：禁用词/意象累积去重、定调一次性、注入渲染提示词。"""
from __future__ import annotations

from novel_engine import db
from novel_engine.llm.base import LLMClient
from novel_engine.models import Entity, Event, Persona
from novel_engine.narration.narrator import Narrator
from novel_engine.repository import Repository


class _CapturingLLM(LLMClient):
    """记录最后一次提示词，便于断言 anchor 是否注入。"""
    def __init__(self) -> None:
        self.last_user = ""

    def complete(self, system: str, user: str) -> str:
        self.last_user = user
        return "他停在原地，没有说话。"  # 干净散文，过反抽象闸门


def _repo() -> Repository:
    r = Repository(db.connect(":memory:"))
    r.insert_entity(Entity("hero", "character", "云鹤子", {}))
    r.insert_entity(Entity("obj_bell", "object", "铜铃", {}))
    r.insert_persona(Persona(agent_id="hero", name="云鹤子", motif_objects=["obj_bell"]))
    return r


def test_motifs_and_banned_accumulate_dedup():
    r = _repo()
    r.add_motifs(["铜铃", "残卷"])
    r.add_motifs(["铜铃", "古镜"])          # 铜铃 去重
    r.add_banned_words(["轮回"])
    r.add_banned_words(["轮回", "命运"])    # 轮回 去重
    a = r.get_style_anchor()
    assert a["motif_lexicon"] == ["铜铃", "残卷", "古镜"]
    assert a["banned_words"] == ["轮回", "命运"]


def test_tone_sample_set_once():
    r = _repo()
    r.set_tone_sample("第一段定调的文字。")
    r.set_tone_sample("后来想覆盖——不应生效。")
    assert r.get_style_anchor()["tone_sample"] == "第一段定调的文字。"


def test_anchor_injected_into_render_prompt():
    r = _repo()
    r.set_tone_sample("夜色压低了檐角。")
    r.add_banned_words(["轮回"])
    r.add_motifs(["铜铃"])
    llm = _CapturingLLM()
    Narrator(r, llm).render("hero", [Event("e1", 1, ["hero"], "试探")], "", [], [])
    assert "[嗓音一致性]" in llm.last_user
    # 架构修复：意象/禁用词仍注入，但**首场正文片段(tone_sample)不再注入**（防内容泄漏复刻）
    assert "铜铃" in llm.last_user and "轮回" in llm.last_user
    assert "夜色压低了檐角" not in llm.last_user


def test_tone_sample_not_leaked_into_prompt():
    """架构修复：tone_sample 仍可存（兼容），但不得出现在渲染提示词里（防每场复刻首场画面）。"""
    r = _repo()
    r.set_tone_sample("书架第三排，他拂过《海鸥》的书脊。")
    assert "书架第三排" not in r.style_anchor_prompt()


def test_empty_anchor_no_block():
    r = _repo()
    assert r.style_anchor_prompt() == ""  # 无意象/禁用词 → 不注入（tone_sample 单独存在也不注入）
    r.set_tone_sample("只有定调样例。")
    assert r.style_anchor_prompt() == ""  # 仅有 tone_sample 也不再注入
