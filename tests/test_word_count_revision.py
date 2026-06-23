"""Word-count revision: the length gate must always land in [min, max].

Regression guard for the compress-undershoot bug where an over-aggressive
compress dropped a chapter below minWords and nothing pushed it back, so the
draft was wrongly blocked on word count.
"""
from __future__ import annotations

import re

from novel_engine import db
from novel_engine.llm.base import LLMClient
from novel_engine.models import ChapterPlan
from novel_engine.repository import Repository
from novel_engine.story_bible import chapter_writer as cw_mod
from novel_engine.story_bible.chapter_writer import ChapterWriter


def _wc(text: str) -> int:
    return len(re.sub(r"\s+", "", text or ""))


def _plan() -> ChapterPlan:
    return ChapterPlan(chapter_id="c1", arc_id="a1", sequence_order=1)


# ---------------- layer 3: deterministic clamp guarantees both bounds -------

def test_clamp_pads_when_compress_undershot():
    out = ChapterWriter._deterministic_word_revision(
        "句子。" * 10, min_words=80, max_words=200, mode="compress"
    )
    assert 80 <= _wc(out) <= 200


def test_clamp_trims_when_expand_overshot():
    out = ChapterWriter._deterministic_word_revision(
        "字" * 500, min_words=50, max_words=200, mode="expand"
    )
    assert _wc(out) <= 200


def test_clamp_trims_when_too_long_regardless_of_mode():
    out = ChapterWriter._deterministic_word_revision(
        "第一段也不算短。\n\n" + ("字" * 500), min_words=10, max_words=120, mode="compress"
    )
    assert _wc(out) <= 120


def test_clamp_keeps_in_band_text_unchanged():
    text = "正好合适的一段话。" * 5
    out = ChapterWriter._deterministic_word_revision(
        text, min_words=10, max_words=500, mode="compress"
    )
    assert out == text.strip()


# ---------------- layer 2: corrective reverse pass on overshoot -------------

class _QueueLLM(LLMClient):
    """Returns queued responses for successive complete/complete_at calls."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls = 0

    def complete(self, system: str, user: str) -> str:
        return self.complete_at(system, user, 0.0)

    def complete_at(self, system: str, user: str, temperature: float | None = None) -> str:
        self.calls += 1
        return self.responses.pop(0) if self.responses else ""


def test_compress_overshoot_triggers_one_expand_correction(monkeypatch):
    monkeypatch.setattr(cw_mod, "build_prose_chapter_scope", lambda repo, plan: {})
    short = "短句。" * 5  # ~15 chars, well below minWords
    longer = "".join(f"第{i}段的内容在这里慢慢展开。" for i in range(30))  # comfortably in band
    llm = _QueueLLM([short, longer])
    writer = ChapterWriter(Repository(db.connect(":memory:")), llm=llm)
    out = writer.revise_word_count(
        prose="原始很长的文本。" * 80,
        chapter_plan=_plan(),
        target_words=200,
        min_words=120,
        max_words=400,
        mode="compress",
    )
    assert llm.calls == 2  # one compress + exactly one corrective expand
    assert 120 <= _wc(out) <= 400


def test_no_correction_when_revision_in_band(monkeypatch):
    monkeypatch.setattr(cw_mod, "build_prose_chapter_scope", lambda repo, plan: {})
    in_band = "".join(f"第{i}段的内容在这里慢慢展开。" for i in range(20))
    llm = _QueueLLM([in_band])
    writer = ChapterWriter(Repository(db.connect(":memory:")), llm=llm)
    out = writer.revise_word_count(
        prose="原始文本。" * 80,
        chapter_plan=_plan(),
        target_words=200,
        min_words=50,
        max_words=600,
        mode="compress",
    )
    assert llm.calls == 1  # no corrective pass needed
    assert 50 <= _wc(out) <= 600
