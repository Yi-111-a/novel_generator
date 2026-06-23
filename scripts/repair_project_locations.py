"""Repair structured chapter locations and accepted event/fact location indexes."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, "src")

from novel_engine import db  # noqa: E402
from novel_engine.models import Entity, Location  # noqa: E402
from novel_engine.repository import Repository  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("database")
    parser.add_argument(
        "--map",
        action="append",
        default=[],
        metavar="CHAPTER_ID=LOCATION_NAME",
    )
    args = parser.parse_args()
    conn = db.connect(Path(args.database))
    repo = Repository(conn)
    changed: list[tuple[str, str]] = []
    try:
        with repo.transaction():
            for item in args.map:
                chapter_id, sep, location_name = item.partition("=")
                if not sep or not chapter_id.strip() or not location_name.strip():
                    raise ValueError(f"invalid mapping: {item}")
                chapter_id = chapter_id.strip()
                location_name = location_name.strip()
                entity = next(
                    (
                        row
                        for row in repo.list_entities()
                        if row.type == "location" and row.name == location_name
                    ),
                    None,
                )
                if entity is None:
                    suffix = hashlib.sha1(location_name.encode("utf-8")).hexdigest()[:10]
                    location_id = f"loc_manual_{suffix}"
                    repo.insert_entity(Entity(location_id, "location", location_name))
                    repo.upsert_location(
                        Location(loc_id=location_id, name=location_name, level="建筑")
                    )
                else:
                    location_id = entity.entity_id

                chapter = repo.get_chapter_plan(chapter_id)
                if chapter is None:
                    raise ValueError(f"chapter not found: {chapter_id}")
                chapter.location_ids = [location_id]
                repo.upsert_chapter_plan(chapter)
                conn.execute(
                    "UPDATE events SET location_id=? WHERE beat_id=?",
                    (location_id, chapter_id),
                )
                conn.execute(
                    "UPDATE facts SET location_id=? WHERE source_event_id IN "
                    "(SELECT event_id FROM events WHERE beat_id=?)",
                    (location_id, chapter_id),
                )
                changed.append((chapter_id, location_name))
    finally:
        conn.close()
    print(json.dumps(changed, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
