from __future__ import annotations

from novel_engine import db
from novel_engine.models import Arc, ChapterDraftRecord, ChapterPlan, Entity, Part, Persona, ToneProfile
from novel_engine.narration.scene_writer import SceneWriter
from novel_engine.planner import Planner
from novel_engine.repository import Repository
from novel_engine.story_bible.chapter_writer import ChapterWriter
from novel_engine.templates import get as get_template


def _repo() -> Repository:
    r = Repository(db.connect(":memory:"))
    r.insert_entity(Entity("loc_main", "location", "山门", {"part": "part1"}))
    r.insert_entity(Entity("loc_yard", "location", "破院", {"part": "part1"}))
    for aid, name in [
        ("p_xiao", "萧守拙"),
        ("p_gu", "顾长夜"),
        ("p_su", "苏照雪"),
        ("named_7abdcc", "骨寒渊"),
    ]:
        r.insert_entity(Entity(aid, "character", name, {}))
        r.insert_persona(Persona(agent_id=aid, name=name, want="活下去"))
    r.upsert_part(Part("part1", 1, title="入局", goal="师徒被推上台前", region="山门", status="active"))
    r.upsert_arc(Arc("arc1", "part1", 1, title="第一局", summary="主角护住徒弟并打脸", target_chapters=5,
                     focus_agents=[{"agent_id": "p_xiao", "weight": 0.8}, {"agent_id": "named_7abdcc", "weight": 0.6}],
                     status="active"))
    return r


def test_template_requires_breath_beat():
    tmpl = get_template("shuangwen_zhuangbi")
    assert tmpl is not None
    assert "breath_beat" in tmpl.structural["chapter_must_have_beats"]


def test_offline_chapter_plan_has_two_main_beats_plus_breath_and_apprentice():
    r = _repo()
    ch = Planner(r, llm=None, template_id="shuangwen_zhuangbi").next_chapter()
    assert ch is not None
    assert len(ch.beat_goals) == 3
    assert "p_gu" in ch.cast
    assert "呼吸拍" in ch.beat_goals[-1]
    assert ch.target_scenes == 3


def test_writing_settings_default_short_chapter():
    ws = _repo().get_writing_settings()
    assert ws.target_words == 3000
    assert ws.min_words == 2600
    assert ws.max_words == 4000


def test_styled_beat_marks_next_beat_as_boundary_not_current_content():
    r = _repo()
    writer = ChapterWriter(r, llm=None)
    beat = writer._compose_styled_beat(
        beat_lines=["陈野继承破店，只发现门口招牌。"],
        style_packet={},
        hint="稳住开场",
        prev_tail="电脑屏幕还没有亮。",
        next_beat_constraint="午夜十二点电脑自动开机，后台弹出林晚一星差评。",
    )
    assert "下一拍边界" in beat
    assert "当前拍不要完整提前展开" in beat
    assert "上一拍尾部" in beat


def test_draft_generate_reuses_pending_draft_before_llm_work():
    from novel_engine.story_bible import DraftManager

    class ExplodingLLM:
        @property
        def name(self):
            return "explode"

        def complete(self, system: str, user: str) -> str:
            raise AssertionError("should not call llm")

        def complete_at(self, system: str, user: str, temperature=None) -> str:
            raise AssertionError("should not call llm")

    r = _repo()
    pending_id = r.create_chapter_draft(ChapterDraftRecord(
        project_id="p",
        chapter_no=1,
        title="已有草稿",
        prose="已经写过。",
        status="pending_acceptance",
        created_at="2026-01-01T00:00:00+00:00",
    ))
    draft = DraftManager(r, ExplodingLLM(), project_id="p").generate(mode="auto")
    assert draft.id == pending_id
    assert draft.title == "已有草稿"


def test_system_broadcast_gate_requires_one_per_500_chars_for_system_stories():
    r = _repo()
    r.set_tone_profile(ToneProfile(genre="xuanhuan_powerfantasy", primary_effect="catharsis_satisfaction",
                                   register="系统播报"))
    writer = SceneWriter(r, llm=None)
    assert writer._requires_system_broadcasts()
    ok, feedback = writer._system_broadcast_gate("正文" * 300)
    assert not ok and "系统播报频次不足" in feedback
    ok, _ = writer._system_broadcast_gate("叮——一次。\n\n" + "正文" * 200 + "\n\n【黑化值+1】")
    assert ok
