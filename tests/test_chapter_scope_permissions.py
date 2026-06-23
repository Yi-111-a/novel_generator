from __future__ import annotations

from types import SimpleNamespace

import pytest

from novel_engine import db
from novel_engine.chapter_scope_validator import (
    build_chapter_scope,
    build_prose_chapter_scope,
    validate_chapter_scope,
)
from novel_engine.coherence import check_drift
from novel_engine.llm.base import LLMClient
from novel_engine.models import ChapterDraftRecord, ChapterPlan, Entity
from novel_engine.repository import Repository
from novel_engine.story_bible.chapter_writer import ChapterWriter
from novel_engine.story_bible.drafts import DraftManager
from novel_engine.worldbible import sanitize_worldbuilding_text


class _CountingLLM(LLMClient):
    def __init__(self, response: str = '{"score":0.2,"guidance":"拉回主线"}') -> None:
        self.calls = 0
        self.response = response

    def complete(self, system: str, user: str) -> str:
        self.calls += 1
        return self.response


def _repo() -> Repository:
    return Repository(db.connect(":memory:"))


def test_world_config_sanitizer_moves_future_plot_out_of_canon():
    canon, planning = sanitize_worldbuilding_text(
        "店铺每天午夜接单。第4章发现尸骨位置，最终真凶认罪。"
    )
    assert "午夜接单" in canon
    assert "第4章" not in canon and "尸骨" not in canon
    assert "第4章" in planning and "尸骨" in planning


def test_drift_check_reads_only_done_chapters_and_skips_fewer_than_three():
    repo = _repo()
    repo.set_world_bible(protagonist_want="查清真相")
    for seq, status in [(1, "done"), (2, "planned"), (3, "active"), (4, "planned")]:
        repo.upsert_chapter_plan(ChapterPlan(
            chapter_id=f"c{seq}", arc_id="a", sequence_order=seq,
            status=status, exit_state=f"出口{seq}",
        ))
    llm = _CountingLLM()
    assert check_drift(repo, llm) == ""
    assert llm.calls == 0


def test_guidance_is_constraint_not_an_extra_beat():
    writer = ChapterWriter(_repo())
    plan = ChapterPlan(
        chapter_id="c1", arc_id="a", sequence_order=1,
        beat_goals=["查记录", "发现矛盾"],
    )
    assert writer._beat_lines(plan, "不要提前写尸骨") == ["查记录", "发现矛盾"]


def test_generated_prose_cleanup_removes_heading_and_duplicate_paragraph():
    prose = "差评必回，生死不误\n\n陈野推开门。\n\n陈野推开门。\n\n冷风迎面扑来。"
    assert ChapterWriter._clean_generated_prose(prose) == "陈野推开门。\n\n冷风迎面扑来。"


def test_generated_title_cleanup_removes_model_supplied_chapter_number():
    assert DraftManager._sanitize_generated_title("第六章 无忧售后") == "无忧售后"


def test_future_chapters_are_locked_and_first_chapter_cannot_consume_them():
    repo = _repo()
    repo.upsert_chapter_plan(ChapterPlan(
        chapter_id="c1", arc_id="a", sequence_order=1,
        beat_goals=["接到工单"], exit_state="决定查记录",
    ))
    repo.upsert_chapter_plan(ChapterPlan(
        chapter_id="c2", arc_id="a", sequence_order=2,
        beat_goals=["查到死亡记录"], exit_state="死亡与签收记录矛盾",
    ))
    repo.upsert_chapter_plan(ChapterPlan(
        chapter_id="c4", arc_id="a", sequence_order=4,
        beat_goals=["发现地下室尸骨"], exit_state="确认凶手身份",
    ))
    contract = build_chapter_scope(repo, repo.list_chapter_plans()[0])
    assert [row["chapter"] for row in contract["future_locked"]] == [2, 4]
    result = validate_chapter_scope(repo, repo.list_chapter_plans()[0], "他在地下室发现了尸骨。")
    assert result["ok"] is False
    assert any(v["type"] == "future_event_leak" and v["belongs_to_chapter"] == 4 for v in result["violations"])
    prose_contract = build_prose_chapter_scope(repo, repo.list_chapter_plans()[0])
    assert prose_contract["future_locked"] == [
        {"chapter": 2, "locked": True},
        {"chapter": 4, "locked": True},
    ]
    assert all("exit_state" not in row for row in prose_contract["future_locked"])


def test_common_short_phrases_do_not_combine_into_false_future_leak():
    repo = _repo()
    repo.upsert_chapter_plan(ChapterPlan(
        chapter_id="c1", arc_id="a", sequence_order=1,
        beat_goals=["接到一条差评"], exit_state="倒计时开始",
    ))
    repo.upsert_chapter_plan(ChapterPlan(
        chapter_id="c2", arc_id="a", sequence_order=2,
        beat_goals=["不能让下一条线索消失"], exit_state="三年前的记录出现",
    ))
    result = validate_chapter_scope(
        repo,
        repo.list_chapter_plans()[0],
        "屏幕上出现一条提示。陈野不能退出，只能等倒计时开始。",
    )
    assert not any(v["type"] == "future_event_leak" for v in result["violations"])


def _fake_combined_factory(p0_sequence):
    """每次审计取下一个 P0 计数，构造一个综合审计结果。P0=0 即 accept。"""
    seq = iter(p0_sequence)

    def fake_combined_audit(*_args, **_kwargs):
        n = next(seq)
        return SimpleNamespace(
            decision="accept" if n == 0 else "rewrite",
            classification="prose_rewriteable",
            title="标题",
            scores={"scope_ok": 0.0},
            violations=[
                {"type": "premature_reveal", "text": f"问题{i}", "severity": "P0", "advice": "改掉"}
                for i in range(n)
            ],
            rewrite_targets=["改掉"],
            summary={
                "audit": {"severity": "blocker"},
                "scopeAudit": {"severity": "blocker"},
            },
        )

    return fake_combined_audit


def _wire_manager(monkeypatch, p0_sequence):
    repo = _repo()
    repo.upsert_chapter_plan(ChapterPlan(
        chapter_id="c1", arc_id="a", sequence_order=1,
        beat_goals=["接到工单"], exit_state="倒计时开始",
    ))
    manager = DraftManager(repo, None, project_id="p")
    revise_calls: list[str] = []

    def fake_revise(_plan, prose, _violations):
        revise_calls.append(prose)
        return SimpleNamespace(ok=True, prose=f"第{len(revise_calls)}版修订正文。", change_scope="scene")

    manager.writer.write_next_chapter = lambda **_k: ("标题", "初稿正文。", {})
    manager.reviser.revise = fake_revise
    monkeypatch.setattr(
        "novel_engine.story_bible.drafts.run_combined_chapter_audit",
        _fake_combined_factory(p0_sequence),
    )
    return manager, revise_calls


def test_audit_rewrite_uses_reviser_and_caps_at_three(monkeypatch):
    # 初稿 P0=4，每轮改写后严格下降但始终 >0 → 触满 3 次上限，仍被阻断。
    manager, revise_calls = _wire_manager(monkeypatch, [4, 3, 2, 1, 1, 1])
    draft = manager.generate(guidance="只写本章", target_words=1000)

    assert len(revise_calls) == 3  # 改写走 Reviser，最多三次
    assert draft.status == "blocked"
    assert draft.context_snapshot_json["automaticAuditRewriteCount"] == 3
    assert draft.context_snapshot_json["automaticAuditRewriteLimit"] == 3
    assert draft.context_snapshot_json["manualRewriteConfirmationRequired"] is True


def test_audit_rewrite_stops_early_when_not_improving(monkeypatch):
    # P0 始终为 1（改写没救回来）→ 第一轮后早停，不烧满三次。
    manager, revise_calls = _wire_manager(monkeypatch, [1, 1, 1, 1])
    draft = manager.generate(guidance="只写本章", target_words=1000)

    assert len(revise_calls) == 1
    assert draft.status == "blocked"
    assert draft.context_snapshot_json["automaticAuditRewriteCount"] == 1


def test_audit_rewrite_accepts_when_reviser_fixes_all(monkeypatch):
    # 一次改写把 P0 清零 → accept，进入待验收。
    manager, revise_calls = _wire_manager(monkeypatch, [1, 0])
    draft = manager.generate(guidance="只写本章", target_words=1000)

    assert len(revise_calls) == 1
    assert draft.status == "pending_acceptance"
    assert draft.context_snapshot_json["combinedAudit"]["decision"] == "accept"


def test_unauthorized_character_location_and_item_are_rejected():
    repo = _repo()
    for entity in [
        Entity("hero", "character", "陈野"),
        Entity("future", "character", "楚瑶"),
        Entity("shop", "location", "无忧售后"),
        Entity("villa", "location", "锦澜湾"),
        Entity("phone", "object", "黑色手机"),
        Entity("umbrella", "object", "红伞"),
    ]:
        repo.insert_entity(entity)
    chapter = ChapterPlan(
        chapter_id="c1", arc_id="a", sequence_order=1,
        cast=["hero"], location_ids=["shop"], items_present=["phone"],
    )
    result = validate_chapter_scope(repo, chapter, "楚瑶撑着红伞走进锦澜湾。")
    assert {v["type"] for v in result["violations"]} >= {
        "unauthorized_character", "unauthorized_location", "unauthorized_item",
    }


def test_canonical_name_date_and_address_drift_fail():
    repo = _repo()
    repo.insert_entity(Entity(
        "husband", "character", "程行",
        {"canonical_role": "丈夫", "forbidden_variants": ["林浩"]},
    ))
    repo.insert_entity(Entity(
        "villa", "location", "林晚别墅",
        {"canonical_address": "锦澜湾18号", "forbidden_addresses": ["锦澜湾8号"]},
    ))
    chapter = ChapterPlan(
        chapter_id="c1", arc_id="a", sequence_order=1,
        cast=["husband"], location_ids=["villa"],
    )
    result = validate_chapter_scope(
        repo, chapter, "林浩在林晚别墅的锦澜湾8号翻出2022年3月4日的记录。"
    )
    kinds = {v["type"] for v in result["violations"]}
    assert {"canonical_name_drift", "canonical_address_drift", "invented_exact_date"} <= kinds


def test_unplanned_investigation_result_is_rejected():
    repo = _repo()
    chapter = ChapterPlan(
        chapter_id="c1", arc_id="a", sequence_order=1,
        beat_goals=["接到一星差评"], exit_state="接单倒计时开始",
    )
    result = validate_chapter_scope(
        repo, chapter, "屏幕显示死因和案件编号，肇事司机身份已经确认。"
    )
    assert any(v["type"] == "new_investigation_result" for v in result["violations"])


def test_blocker_draft_cannot_use_normal_accept():
    repo = _repo()
    draft = ChapterDraftRecord(
        project_id="p", chapter_no=1, prose="越界正文", status="blocked",
        context_snapshot_json={"scopeAudit": {"severity": "blocker"}},
    )
    draft.id = repo.create_chapter_draft(draft)
    with pytest.raises(ValueError, match="draft_blocked_by_audit"):
        DraftManager(repo).accept(draft.id)


def test_force_accept_is_separate_and_records_reason():
    repo = _repo()
    draft = ChapterDraftRecord(
        project_id="p", chapter_no=1, title="第一章", prose="人工确认保留。",
        status="blocked",
        context_snapshot_json={"scopeAudit": {"severity": "blocker"}},
    )
    draft.id = repo.create_chapter_draft(draft)
    accepted = DraftManager(repo).force_accept(draft.id, reason="作者确认这是有意的倒叙")
    assert accepted.chapter_no == 1
    saved = repo.get_chapter_draft(draft.id)
    assert saved is not None
    assert saved.context_snapshot_json["forceAccept"]["reason"] == "作者确认这是有意的倒叙"
