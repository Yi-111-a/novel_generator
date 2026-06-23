from __future__ import annotations

import json

from novel_engine import db
from novel_engine.chapter_scope_validator import compile_chapter_package, validate_chapter_scope
from novel_engine.llm.base import LLMClient
from novel_engine.llm.logging_wrapper import LoggingLLMClient
from novel_engine.models import CharacterCard, ChapterPlan, Entity, InventoryItem
from novel_engine.narration.audit import audit_chapter_result
from novel_engine.models import Scene
from novel_engine.repository import Repository
from novel_engine.story_bible.drafts import DraftManager
from novel_engine.story_bible.chapter_writer import ChapterWriter


class _FixedLLM(LLMClient):
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        return self.response


def _repo() -> Repository:
    return Repository(db.connect(":memory:"))


def test_generation_backfills_disclosure_schedule_for_legacy_project():
    repo = _repo()
    repo.insert_entity(Entity("hero", "character", "陈野"))
    repo.insert_entity(Entity("guest", "character", "顾遥"))
    repo.add_card(CharacterCard(card_id="hero_card", agent_id="hero", name="陈野"))
    repo.add_card(CharacterCard(
        card_id="guest_card",
        agent_id="guest",
        name="顾遥",
        appearance="总戴着旧银框眼镜",
    ))
    repo.upsert_chapter_plan(ChapterPlan(
        chapter_id="c1", arc_id="a", sequence_order=1, cast=["hero"]
    ))
    repo.upsert_chapter_plan(ChapterPlan(
        chapter_id="c2", arc_id="a", sequence_order=2, cast=["hero", "guest"]
    ))

    DraftManager(repo).generate(outline_only=True)

    card = repo.get_card_for_agent("guest")
    assert card is not None
    assert card.reveal_chapter == 2
    assert card.foreshadow_hint
    assert "guest" in repo.get_chapter_plan("c1").allowed_entity_ids


def test_current_chapter_concept_is_not_reclassified_as_future_leak():
    repo = _repo()
    current = ChapterPlan(
        chapter_id="c2",
        arc_id="a",
        sequence_order=2,
        must_happen=["核对物业签收记录，确认记录存在冲突。"],
    )
    future = ChapterPlan(
        chapter_id="c7",
        arc_id="a",
        sequence_order=7,
        beat_goals=["警方调取物业记录后进入别墅。"],
    )
    repo.upsert_chapter_plan(current)
    repo.upsert_chapter_plan(future)

    package = compile_chapter_package(repo, current)

    assert all(
        "物业记录" not in marker
        for row in package["future_locked"]
        for marker in row["forbidden"]
    )
    result = validate_chapter_scope(repo, current, "陈野核对了物业签收记录。")
    assert not any(row["type"] == "future_event_leak" for row in result["violations"])


def test_rewrite_targets_do_not_echo_sensitive_values():
    targets = DraftManager._safe_rewrite_targets([
        {"type": "invented_exact_date", "text": "2021年11月17日"},
        {"type": "new_investigation_result", "text": "死因"},
        {"type": "unauthorized_character", "text": "程行"},
    ])
    joined = "\n".join(targets)
    assert "2021年11月17日" not in joined
    assert "程行" not in joined
    assert "具体年月日" in joined
    assert "调查结论" in joined


def test_pov_inventory_and_allowed_image_do_not_trigger_phantom_item():
    repo = _repo()
    for entity in [
        Entity("hero", "character", "陈野"),
        Entity("keyboard", "object", "褪色键盘"),
        Entity("ring", "object", "断裂婚戒"),
        Entity("ring_image", "object", "断裂婚戒影像"),
    ]:
        repo.insert_entity(entity)
    repo.set_inventory(InventoryItem("keyboard", "hero", "held"))
    chapter = ChapterPlan(
        chapter_id="c2",
        arc_id="a",
        sequence_order=2,
        pov_agent="hero",
        cast=["hero"],
        items_present=["ring_image"],
    )
    prose = "陈野敲了敲褪色键盘，屏幕上浮出一段断裂婚戒影像。"

    scope = validate_chapter_scope(repo, chapter, prose)
    structural = audit_chapter_result(
        repo, chapter, [Scene("s1", 1, prose_text=prose)], None, None
    )

    assert not any(row["type"] == "unauthorized_item" for row in scope["violations"])
    assert structural.ok


def test_duplicate_llm_title_falls_back_instead_of_being_accepted():
    repo = _repo()
    repo.upsert_chapter_plan(ChapterPlan(
        chapter_id="c1", arc_id="a", sequence_order=1, title="二叔的烂摊子"
    ))
    llm = _FixedLLM('{"title":"二叔的烂摊子"}')
    manager = DraftManager(repo, llm)
    chapter = ChapterPlan(chapter_id="c2", arc_id="a", sequence_order=2)

    title = manager._llm_chapter_title("正文内容足够长。", chapter, fallback="第2章")

    assert title == "第2章"


def test_logging_scope_records_precise_caller_and_metadata():
    conn = db.connect(":memory:")
    llm = LoggingLLMClient(_FixedLLM("ok"), conn, caller="default")

    with llm.scope(caller="chapter_writer", meta={"chapter_no": 2, "rewrite_attempt": 3}):
        assert llm.complete("system", "user") == "ok"

    row = conn.execute(
        "SELECT caller, meta FROM llm_logs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row["caller"] == "chapter_writer"
    assert json.loads(row["meta"]) == {"chapter_no": 2, "rewrite_attempt": 3}


def test_writer_prompt_contains_precision_and_nonphysical_boundaries():
    repo = _repo()
    repo.insert_entity(Entity("hero", "character", "陈野"))
    repo.insert_entity(Entity(
        "ring_image",
        "object",
        "断裂婚戒影像",
        {"non_physical": True, "source": "记忆碎片"},
    ))
    llm = _FixedLLM("陈野只在屏幕上看见一段模糊影像。")
    writer = ChapterWriter(repo, llm)
    chapter = ChapterPlan(
        chapter_id="c2",
        arc_id="a",
        sequence_order=2,
        pov_agent="hero",
        cast=["hero"],
        items_present=["ring_image"],
        beat_goals=["核对记录之间的冲突。"],
    )

    writer.write_next_chapter(chapter_plan=chapter, target_words=300)

    system, user = llm.calls[-1]
    assert "do not invent exact dates" in system
    assert "Never make up realistic-looking database rows" in user
    assert "non_physical=true" in user
    assert '"non_physical": true' in user
