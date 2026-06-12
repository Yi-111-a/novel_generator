"""C1+C2：单场目标字数注入提示词；过短触发扩写重渲。"""
from __future__ import annotations

from novel_engine import db
from novel_engine.llm.base import LLMClient
from novel_engine.models import ChapterPlan, Entity, Event, Persona
from novel_engine.narration.narrator import Narrator
from novel_engine.repository import Repository


def _repo() -> Repository:
    r = Repository(db.connect(":memory:"))
    r.insert_entity(Entity("hero", "character", "云鹤子", {}))
    r.insert_persona(Persona(agent_id="hero", name="云鹤子"))
    # 章：目标 2400 字 / 3 场 → 单场 800 字
    r.upsert_chapter_plan(ChapterPlan(chapter_id="ch1", arc_id="a1", sequence_order=1,
                                      target_words=2400, target_scenes=3))
    return r


class _CapLLM(LLMClient):
    def __init__(self, reply: str) -> None:
        self.reply, self.last_user, self.n = reply, "", 0

    def complete(self, system: str, user: str) -> str:
        self.last_user = user
        self.n += 1
        return self.reply


def test_word_target_in_prompt():
    r = _repo()
    llm = _CapLLM("正文。" * 300)  # 足够长，不触发扩写
    ev = Event("e1", 1, ["hero"], "试探", beat_id="ch1")
    Narrator(r, llm).render("hero", [ev], "", [], [])
    assert "约 800 字" in llm.last_user  # 2400/3


class _LongThenShort(LLMClient):
    def __init__(self) -> None:
        self.n = 0

    def complete(self, system: str, user: str) -> str:
        self.n += 1
        # 第一稿注水超长（> 1.3×800=1040）→ 触发压缩；第二稿精简达标
        return ("注水的超长正文。" * 200) if self.n == 1 else ("精简后的正文。" * 20)


def test_long_scene_triggers_compress_rerender():
    r = _repo()
    llm = _LongThenShort()
    ev = Event("e1", 1, ["hero"], "试探", beat_id="ch1")
    out = Narrator(r, llm).render("hero", [ev], "", [], [])
    assert llm.n >= 2                  # 过长 → 压缩重渲
    assert len(out) <= int(800 * 1.3)  # 最终稿在软上限内


def test_short_scene_not_force_expanded():
    """A：过短不再强行扩写（留白即节奏）——短稿一次过，不重渲。"""
    r = _repo()
    llm = _CapLLM("很短的一场。")
    ev = Event("e1", 1, ["hero"], "试探", beat_id="ch1")
    Narrator(r, llm).render("hero", [ev], "", [], [])
    assert llm.n == 1


def test_no_chapter_no_length_constraint():
    r = _repo()
    llm = _CapLLM("短")
    ev = Event("e1", 1, ["hero"], "试探")  # 无 beat_id → 不约束篇幅
    Narrator(r, llm).render("hero", [ev], "", [], [])
    assert "字）" not in llm.last_user and llm.n == 1
