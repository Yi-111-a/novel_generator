"""章级审计闸门：道具合规（确定性）+ LLM 衔接/转场判定 + 不合格重渲。"""
from __future__ import annotations

from novel_engine import db
from novel_engine.llm.base import LLMClient
from novel_engine.models import ChapterPlan, Entity, Scene
from novel_engine.narration.audit import audit_chapter, _phantom_items
from novel_engine.repository import Repository


def _repo() -> Repository:
    r = Repository(db.connect(":memory:"))
    r.insert_entity(Entity("obj_pen", "object", "钢笔", {}))
    r.insert_entity(Entity("obj_code", "object", "密码本", {}))  # 已登记但不在本章
    return r


def _ch() -> ChapterPlan:
    return ChapterPlan(chapter_id="c1", arc_id="a1", sequence_order=1,
                       cast=["hero"], items_present=["obj_pen"])


def test_phantom_item_detected_deterministically():
    r = _repo()
    ch = _ch()
    s = Scene(scene_id="s1", discourse_order=1, source_events=[], pov="hero",
              prose_text="他摸出那本密码本，翻开。")  # 密码本不在 items_present
    assert "密码本" in _phantom_items(r, ch, s.prose_text)
    ok, fb = audit_chapter(r, ch, [s], None, llm=None)
    assert ok is False and "密码本" in fb


def test_clean_chapter_passes_offline():
    r = _repo()
    ch = _ch()
    s = Scene(scene_id="s1", discourse_order=1, source_events=[], pov="hero",
              prose_text="他拧开钢笔的笔帽，写了几个字。")
    ok, fb = audit_chapter(r, ch, [s], None, llm=None)
    assert ok is True and fb == ""


class _RejectLLM(LLMClient):
    def complete(self, system: str, user: str) -> str:
        return '{"ok": false, "feedback": "开头没有承接上一章的钩子。"}'


def test_llm_rejects_bad_continuity():
    r = _repo()
    ch = _ch()
    s = Scene(scene_id="s1", discourse_order=1, source_events=[], pov="hero",
              prose_text="他拧开钢笔写字。")  # 道具干净，但 LLM 判衔接不过
    ok, fb = audit_chapter(r, ch, [s], None, llm=_RejectLLM())
    assert ok is False and "钩子" in fb


class _PassLLM(LLMClient):
    def complete(self, system: str, user: str) -> str:
        return '{"ok": true, "feedback": ""}'


def test_llm_pass_clean():
    r = _repo()
    ch = _ch()
    s = Scene(scene_id="s1", discourse_order=1, source_events=[], pov="hero",
              prose_text="他拧开钢笔写字。")
    ok, fb = audit_chapter(r, ch, [s], None, llm=_PassLLM())
    assert ok is True
