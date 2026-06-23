from __future__ import annotations

import json

from novel_engine import db
from novel_engine.chapter_scope_validator import compile_chapter_package, validate_chapter_scope
from novel_engine.data_reconciliation import reconcile_legacy_conflicts
from novel_engine.llm.base import LLMClient
from novel_engine.llm.logging_wrapper import LoggingLLMClient
from novel_engine.models import (
    AcceptedChapterRecord,
    ChapterDraftRecord,
    ChapterPlan,
    Entity,
    WritingSettings,
)
from novel_engine.narration.audit import audit_chapter_result
from novel_engine.models import Scene
from novel_engine.repository import Repository
from novel_engine.story_bible.drafts import DraftManager


def _repo() -> Repository:
    return Repository(db.connect(":memory:"))


class _CountingLLM(LLMClient):
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        if "Generate a short Chinese chapter title" in system:
            return '{"title":"风停之前"}'
        if "length only" in system:
            return "风声沿着窗框缓慢移动。" * 24
        return "风声停在窗边。"


def test_bad_project_name_not_in_current_chapter_does_not_block():
    repo = _repo()
    repo.insert_entity(Entity("hero", "character", "林川"))
    repo.insert_entity(Entity("unused", "character", "铁肺哥"))
    plan = ChapterPlan("c2", "a", 2, cast=["hero"], title="夜路")
    result = audit_chapter_result(
        repo,
        plan,
        [Scene("s", 1, prose_text="林川沿着夜路继续往前。")],
        None,
        None,
    )
    assert result.checks["name_quality"].ok is True


def test_longest_legal_item_name_wins_but_real_short_item_is_still_blocked():
    # 用非通用短道具（玉佩/血玉佩）；通用杂物如"纸条"已被 _GENERIC_OBJECT_NOUNS 豁免。
    repo = _repo()
    repo.insert_entity(Entity("short", "object", "玉佩"))
    repo.insert_entity(Entity("long", "object", "血玉佩"))
    plan = ChapterPlan("c2", "a", 2, items_present=["long"])

    legal = validate_chapter_scope(repo, plan, "桌上放着一块血玉佩。")
    illegal = validate_chapter_scope(repo, plan, "门边另有一块玉佩。")

    assert not any(row["type"] == "unauthorized_item" for row in legal["violations"])
    assert any(
        row["type"] == "unauthorized_item" and row["text"] == "玉佩"
        for row in illegal["violations"]
    )


def test_parent_location_allowed_when_child_is_authorized():
    # 授权子级地点（按"·"层级），正文写父级地点不应判越界；无关地点仍越界。
    repo = _repo()
    repo.insert_entity(Entity("loc_parent", "location", "槐荫巷44号"))
    repo.insert_entity(Entity("loc_child", "location", "槐荫巷44号·售后服务处"))
    repo.insert_entity(Entity("loc_other", "location", "锦澜湾18号别墅"))
    plan = ChapterPlan("c1", "a", 1, location_ids=["loc_child"])

    ok = validate_chapter_scope(repo, plan, "陈野走进槐荫巷44号，拉开卷帘门。")
    bad = validate_chapter_scope(repo, plan, "他随后赶往锦澜湾18号别墅。")

    assert not any(v["type"] == "unauthorized_location" for v in ok["violations"])
    assert any(
        v["type"] == "unauthorized_location" and v["text"] == "锦澜湾18号别墅"
        for v in bad["violations"]
    )


def test_planning_conflict_blocks_future_entity_before_llm():
    # 规划点名了一个本章尚未披露的未来实体（stage<2）→ 仍 P0 阻断，不进 LLM。
    repo = _repo()
    repo.insert_entity(
        Entity("future_ledger", "object", "幽灵账本", attributes={"available_from_chapter": 5})
    )
    repo.upsert_chapter_plan(
        ChapterPlan(
            "c1",
            "a",
            1,
            must_happen=["主角翻开幽灵账本。"],
            scene_flow=["主角翻开幽灵账本。"],
        )
    )
    llm = _CountingLLM()
    draft = DraftManager(repo, llm, project_id="p").generate(target_words=200)

    assert draft.status == "blocked"
    assert draft.context_snapshot_json["combinedAudit"]["classification"] == "planning_conflict"
    assert draft.context_snapshot_json["automaticAuditRewriteCount"] == 0
    assert llm.calls == []


def test_planning_reference_to_disclosed_entity_auto_authorizes():
    # 规划点名了一个本章已可登场的老实体（无排期，stage=2）但忘了授权 →
    # 自动补授权而非 P0 阻断，正文照常生成。
    repo = _repo()
    repo.insert_entity(Entity("key", "object", "银钥匙"))
    repo.upsert_chapter_plan(
        ChapterPlan(
            "c1",
            "a",
            1,
            must_happen=["主角使用银钥匙打开门。"],
            scene_flow=["主角使用银钥匙打开门。"],
        )
    )
    package = compile_chapter_package(repo, repo.list_chapter_plans()[0])
    diagnostics = package["diagnostics"]

    assert not diagnostics["planning_conflicts"]
    assert "key" in package["allowed_entity_ids"]
    assert any(
        row["type"] == "auto_authorized_reference" and row["entity_id"] == "key"
        for row in diagnostics["data_conflicts"]
    )


def test_future_detection_ignores_common_bigrams_but_keeps_full_event():
    repo = _repo()
    current = ChapterPlan("c1", "a", 1, beat_goals=["收到提示"], exit_state="决定等待")
    future = ChapterPlan(
        "c3",
        "a",
        3,
        beat_goals=["潜入地下室后发现保险柜中的完整账本"],
    )
    repo.upsert_chapter_plan(current)
    repo.upsert_chapter_plan(future)

    common = validate_chapter_scope(repo, current, "他不能离开，只能看着下一条提示。")
    leaked = validate_chapter_scope(repo, current, "他潜入地下室后发现保险柜中的完整账本。")

    assert not any(row["type"] == "future_event_leak" for row in common["violations"])
    assert any(row["type"] == "future_event_leak" for row in leaked["violations"])


def test_low_word_count_expands_before_permission_audit_and_logs_match_snapshot():
    conn = db.connect(":memory:")
    repo = Repository(conn)
    repo.set_writing_settings(
        WritingSettings(target_words=220, min_words=180, max_words=280)
    )
    repo.upsert_chapter_plan(
        ChapterPlan("c1", "a", 1, title="风停之前", beat_goals=["等待消息"])
    )
    inner = _CountingLLM()
    llm = LoggingLLMClient(inner, conn)
    draft = DraftManager(repo, llm, project_id="project-x").generate()

    word_count = len("".join(draft.prose.split()))
    assert 180 <= word_count <= 280
    assert draft.context_snapshot_json["wordCountHistory"][0]["action"] == "expand"
    assert draft.context_snapshot_json["wordCountHistory"][0]["after"] == word_count
    assert draft.context_snapshot_json["combinedAudit"]["decision"] == "accept"

    rows = conn.execute("SELECT caller, meta FROM llm_logs ORDER BY id").fetchall()
    callers = [row["caller"] for row in rows]
    assert callers[0] == "chapter_writer"
    assert "word_count_expand" in callers
    assert "chapter_title" in callers
    for row in rows:
        meta = json.loads(row["meta"])
        assert meta["project_id"] == "project-x"
        assert meta["chapter_no"] == 1
        assert meta["phase"]
        assert meta["attempt"] >= 1


def test_first_chapter_quality_gate_is_not_applied_to_later_chapters():
    repo = _repo()
    repo.set_world_bible(
        setting_core="人们通过梦境契约交换记忆。",
        protagonist_want="找回失去的记忆",
        theme="身份与代价",
    )
    first = ChapterPlan(
        "c1",
        "a",
        1,
        beat_goals=["主角检查现场"],
        exit_state="现场留下痕迹",
    )
    later = ChapterPlan(
        "c2",
        "a",
        2,
        beat_goals=["主角检查现场"],
        exit_state="现场留下痕迹",
    )
    first_issues = compile_chapter_package(repo, first)["diagnostics"]["planning_conflicts"]
    later_issues = compile_chapter_package(repo, later)["diagnostics"]["planning_conflicts"]

    assert {row["type"] for row in first_issues} >= {
        "opening_missing_motivation",
        "opening_missing_core_mechanism",
        "opening_missing_conflict_entry",
        "opening_missing_next_action",
    }
    assert not any(row["type"].startswith("opening_") for row in later_issues)


def test_legacy_reconciliation_preserves_rows_and_hides_obsolete_drafts():
    repo = _repo()
    repo.insert_entity(Entity("generic", "object", "纸条"))
    repo.insert_entity(Entity("specific", "object", "染血纸条"))
    repo.upsert_chapter_plan(
        ChapterPlan("c2", "a", 2, items_present=["generic"], allowed_entity_ids=["generic"])
    )
    report = reconcile_legacy_conflicts(repo, apply=True)

    assert len(repo.list_entities()) == 2
    assert report["replacement_map"]
    merged_id = next(iter(report["replacement_map"]))
    assert repo.get_entity(merged_id).attributes["preserve_for_history"] is True

    rejected = ChapterDraftRecord(
        project_id="p",
        chapter_no=2,
        status="rejected",
        prose="旧稿",
        created_at="before",
    )
    rejected.id = repo.create_chapter_draft(rejected)
    accepted_draft = ChapterDraftRecord(
        project_id="p",
        chapter_no=2,
        status="accepted",
        prose="确认稿",
        created_at="after",
    )
    accepted_draft.id = repo.create_chapter_draft(accepted_draft)
    repo.insert_accepted_chapter(
        AcceptedChapterRecord(
            project_id="p",
            draft_id=accepted_draft.id,
            chapter_no=2,
            prose="确认稿",
            created_at="after",
        )
    )
    visible = {row.id for row in repo.list_visible_chapter_drafts()}
    assert rejected.id not in visible
    assert accepted_draft.id in visible
