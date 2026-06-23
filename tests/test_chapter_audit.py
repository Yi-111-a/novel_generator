"""章级审计闸门：道具合规（确定性）+ LLM 衔接/转场判定 + 不合格重渲。"""
from __future__ import annotations

from novel_engine import db
from novel_engine.llm.base import LLMClient
from novel_engine.models import AcceptedChapterRecord, ChapterPlan, Entity, Scene
from novel_engine.narration.audit import (
    audit_chapter,
    audit_chapter_result,
    run_combined_chapter_audit,
    _phantom_items,
)
from novel_engine.repository import Repository


def test_repeated_opening_flagged_as_p1_advisory():
    """跨章复述开头：报 repeated_opening，但只是 P1 提示，不阻断、不交 Reviser。"""
    r = Repository(db.connect(":memory:"))
    shared = "六月的临江市像个蒸笼，陈野坐在写字楼的奶茶店里，面前一杯喝到底的杨枝甘露，人事部的消息措辞礼貌。"
    r.insert_accepted_chapter(AcceptedChapterRecord(
        project_id="p", draft_id=1, chapter_no=1,
        prose=shared + "他端着纸箱离开了办公楼。", summary="", created_at="",
    ))
    ch2 = ChapterPlan(chapter_id="c2", arc_id="a", sequence_order=2, title="第二章")
    prose = shared + "但此刻他已经在老城区的店铺里，对着一台不该亮的电脑。"
    result = run_combined_chapter_audit(r, ch2, prose, None, llm=None)

    repeated = [v for v in result.violations if v["type"] == "repeated_opening"]
    assert repeated and repeated[0]["severity"] == "P1"
    assert result.decision == "accept"  # P1 不阻断


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


def test_bug_signal_fails_structured_audit():
    r = _repo()
    ch = _ch()
    s = Scene(
        scene_id="s1",
        discourse_order=1,
        source_events=[],
        pov="hero",
        prose_text="只输出 JSON。fac_shadow 站在门后，钢笔落在地上。",
    )
    result = audit_chapter_result(r, ch, [s], None, llm=None)
    assert result.ok is False
    assert result.checks["bug_signals"].ok is False
