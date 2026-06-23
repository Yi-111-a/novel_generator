from __future__ import annotations

from novel_engine import db
from novel_engine.chapter_scope_validator import (
    compile_chapter_package,
    validate_chapter_scope,
)
from novel_engine.disclosure import (
    auto_schedule_disclosures,
    get_disclosure_schedule,
)
from novel_engine.models import (
    CharacterCard,
    ChapterPlan,
    Entity,
    Faction,
    Foreshadow,
    Location,
)
from novel_engine.narration.retrieval import build_context
from novel_engine.repository import Repository


def _repo() -> Repository:
    return Repository(db.connect(":memory:"))


def test_four_stage_character_rendering_and_secret_gate():
    repo = _repo()
    repo.insert_entity(Entity("guest", "character", "顾遥"))
    repo.add_card(CharacterCard(
        card_id="card_guest",
        agent_id="guest",
        name="顾遥",
        one_liner="新来的法医助理",
        appearance="总戴着旧银框眼镜",
        backstory="她曾参与被掩盖的旧案",
        foreshadow_from=2,
        reveal_chapter=3,
        secret_reveal_chapter=5,
        foreshadow_hint="走廊尽头偶尔传来镜片轻碰金属盒的声音。",
        secret_truth="她就是旧案匿名证人。",
    ))

    hidden = build_context(
        repo, {"guest"}, chapter_seq=1, allowed_entity_ids={"guest"}
    )
    hinted = build_context(
        repo, {"guest"}, chapter_seq=2, allowed_entity_ids={"guest"}
    )
    public = build_context(
        repo, {"guest"}, chapter_seq=3, allowed_entity_ids={"guest"}
    )
    revealed = build_context(
        repo, {"guest"}, chapter_seq=5, allowed_entity_ids={"guest"}
    )

    assert hidden == ""
    assert "镜片轻碰金属盒" in hinted
    assert "顾遥" not in hinted and "匿名证人" not in hinted
    assert "顾遥" in public and "新来的法医助理" in public
    assert "匿名证人" not in public and "参与被掩盖的旧案" not in public
    assert "匿名证人" in revealed


def test_package_has_full_hint_forbidden_buckets_and_detects_premature_name():
    repo = _repo()
    repo.insert_entity(Entity("hero", "character", "陈野"))
    repo.insert_entity(Entity("guest", "character", "顾遥"))
    repo.add_card(CharacterCard(card_id="hero_card", agent_id="hero", name="陈野"))
    repo.add_card(CharacterCard(
        card_id="guest_card",
        agent_id="guest",
        name="顾遥",
        foreshadow_from=1,
        reveal_chapter=3,
        foreshadow_hint="有人提前翻过了值班表。",
    ))
    chapter = ChapterPlan(
        chapter_id="c1",
        arc_id="a",
        sequence_order=1,
        cast=["hero"],
        allowed_entity_ids=["hero", "guest"],
    )

    package = compile_chapter_package(repo, chapter)
    assert package["allowed_full"] == ["hero"]
    assert package["allowed_hint"] == [
        {"entity_id": "guest", "hint": "有人提前翻过了值班表。"}
    ]
    assert "guest" not in package["forbidden_entity_ids"]

    result = validate_chapter_scope(repo, chapter, "顾遥提前翻过了值班表。")
    assert any(row["type"] == "premature_reveal" for row in result["violations"])


def test_unforeshadowed_introduction_requires_ledger_entry():
    repo = _repo()
    repo.insert_entity(Entity("guest", "character", "顾遥"))
    repo.add_card(CharacterCard(
        card_id="guest_card",
        agent_id="guest",
        name="顾遥",
        foreshadow_from=1,
        reveal_chapter=3,
        foreshadow_hint="有人提前翻过了值班表。",
    ))
    chapter = ChapterPlan(
        chapter_id="c3",
        arc_id="a",
        sequence_order=3,
        cast=["guest"],
    )
    repo.upsert_chapter_plan(chapter)

    missing = validate_chapter_scope(repo, chapter, "顾遥走进值班室。")
    assert any(
        row["type"] == "unforeshadowed_introduction"
        for row in missing["violations"]
    )

    repo.upsert_foreshadow(Foreshadow(
        foreshadow_id="disclosure:guest",
        question="有人提前翻过了值班表。",
        linked_fact_id="guest",
        planted_discourse_pos=1,
    ))
    planted = validate_chapter_scope(repo, chapter, "顾遥走进值班室。")
    assert not any(
        row["type"] == "unforeshadowed_introduction"
        for row in planted["violations"]
    )


def test_auto_schedule_plants_name_free_public_hint():
    repo = _repo()
    repo.insert_entity(Entity("hero", "character", "陈野"))
    repo.insert_entity(Entity("guest", "character", "顾遥"))
    repo.add_card(CharacterCard(card_id="hero_card", agent_id="hero", name="陈野"))
    repo.add_card(CharacterCard(
        card_id="guest_card",
        agent_id="guest",
        name="顾遥",
        defining_trait="说话前会先把眼镜推回鼻梁",
        secret_truth="她就是旧案匿名证人。",
    ))
    repo.upsert_chapter_plan(ChapterPlan(
        chapter_id="c1", arc_id="a", sequence_order=1, cast=["hero"]
    ))
    repo.upsert_chapter_plan(ChapterPlan(
        chapter_id="c2", arc_id="a", sequence_order=2, cast=["hero"]
    ))
    repo.upsert_chapter_plan(ChapterPlan(
        chapter_id="c3", arc_id="a", sequence_order=3, cast=["hero", "guest"]
    ))

    assert auto_schedule_disclosures(repo) == 1
    schedule = get_disclosure_schedule(repo, "guest")
    assert schedule.foreshadow_from == 1
    assert schedule.reveal_chapter == 3
    assert "顾遥" not in schedule.foreshadow_hint
    assert "匿名证人" not in schedule.foreshadow_hint
    assert "guest" in repo.get_chapter_plan("c2").allowed_entity_ids
    assert repo.get_foreshadow("disclosure:guest") is not None


def test_location_and_faction_schedule_round_trip():
    repo = _repo()
    repo.insert_entity(Entity("dock", "location", "旧码头"))
    repo.upsert_location(Location(
        loc_id="dock",
        name="旧码头",
        foreshadow_from=2,
        reveal_chapter=4,
        foreshadow_hint="潮湿的铁锈味先沿河飘来。",
    ))
    repo.upsert_faction(Faction(
        faction_id="watch",
        name="守夜会",
        foreshadow_from=3,
        reveal_chapter=6,
        secret_reveal_chapter=9,
        foreshadow_hint="有人在雨夜统一熄灭街灯。",
        secret_truth="守夜会一直在替真正的控制者清场。",
    ))

    assert repo.get_location("dock").reveal_chapter == 4
    assert repo.get_faction("watch").secret_reveal_chapter == 9


def test_legacy_mystery_one_liner_is_redacted_on_public_surface():
    repo = _repo()
    repo.insert_entity(Entity("client", "character", "林晚"))
    repo.add_card(CharacterCard(
        card_id="client_card",
        agent_id="client",
        name="林晚",
        one_liner="第一单亡者客户，已死亡三年，投诉丈夫和整容替身冒用她的身份。",
    ))
    context = build_context(
        repo,
        {"client"},
        chapter_seq=1,
        allowed_entity_ids={"client"},
    )
    assert "第一单亡者客户" in context
    assert "整容替身" not in context
    assert "冒用她的身份" not in context
