from __future__ import annotations

from novel_engine import db
from novel_engine.models import ChapterPlan, Entity, Event, Fact, Faction, InventoryItem, Persona, Scene
from novel_engine.narration.batch_audit import BatchAuditor
from novel_engine.repository import Repository


def test_batch_audit_flags_destroyed_item_reuse_and_persists():
    repo = Repository(db.connect(":memory:"))
    repo.insert_entity(Entity("hero", "character", "陆沉", {"faction_id": "fac_school"}))
    repo.insert_entity(Entity("obj_watch", "object", "怀表", {}))
    repo.insert_persona(Persona(agent_id="hero", name="陆沉"))
    repo.upsert_faction(Faction(
        faction_id="fac_school",
        name="深渊学院",
        territory=["loc_main"],
        key_members=[{"name": "陆沉", "agent_id": "hero", "role": "student"}],
    ))
    repo.set_inventory(InventoryItem("obj_watch", None, "destroyed", acquired_chapter=3, note="献祭"))
    repo.append_fact(Fact("f1", "event", "怀表被献祭", story_time=3))

    for seq in range(1, 11):
        ch = ChapterPlan(
            chapter_id=f"ch_{seq}",
            arc_id="arc_1",
            sequence_order=seq,
            cast=["hero"],
            location_ids=["loc_main"],
            beat_goals=[f"第{seq}章事件"],
            summary=f"第{seq}章摘要",
            status="done",
        )
        repo.upsert_chapter_plan(ch)
        ev_id = f"ev_{seq}"
        repo.append_event(Event(
            event_id=ev_id,
            story_time=seq,
            actors=["hero"],
            action_type="narrated",
            payload={"content": "推进剧情"},
            beat_id=ch.chapter_id,
        ))
        prose = "他又看见那只怀表。" if seq == 8 else f"第{seq}章正文。"
        repo.insert_scene(Scene(
            scene_id=f"sc_{seq}",
            discourse_order=seq,
            source_events=[ev_id],
            pov="hero",
            prose_text=prose,
        ))

    result = BatchAuditor(repo, llm=None).run(10, tick=10)
    assert result.item_violations
    assert result.item_violations[0]["name"] == "怀表"
    assert 8 in result.item_violations[0]["seen_after"]
    assert result.reveal_progress["total"] == 0

    stored = repo.latest_batch_audit()
    assert stored is not None
    assert stored.chapter_seq == 10
    assert "plot" in stored.summary_json
