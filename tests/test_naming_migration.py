from __future__ import annotations

from novel_engine import db
from novel_engine.models import Entity, Persona
from novel_engine.naming_migration import migrate_legacy_character_names
from novel_engine.repository import Repository


def test_migration_repairs_bad_primary_name_and_preserves_alias():
    repo = Repository(db.connect(":memory:"))
    repo.insert_entity(Entity("hero", "character", "少主", {}))
    repo.insert_persona(
        Persona(
            agent_id="hero",
            name="少主",
            want="查清真相",
            values=[],
            fatal_flaw="偏执",
            voice="冷",
        )
    )
    payload = migrate_legacy_character_names(repo)
    record = repo.get_character_name("hero")
    assert payload["updated"] == 1
    assert record is not None
    assert record.primary_name != "少主"
    assert repo.get_character_display_name("hero") == record.primary_name
    entity = repo.get_entity("hero")
    assert entity is not None
    assert "少主" in (entity.attributes or {}).get("legacy_aliases", [])
