"""Back up and reset generated prose/state while preserving plans and canon."""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("database")
    args = parser.parse_args()
    path = Path(args.database).resolve()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = path.with_name(f"{path.stem}.before_prose_reset_{stamp}.db")

    source = sqlite3.connect(path)
    target = sqlite3.connect(backup)
    try:
        source.backup(target)
    finally:
        target.close()

    source.row_factory = sqlite3.Row
    try:
        source.execute("BEGIN IMMEDIATE")
        plan_ids = [
            row["chapter_id"]
            for row in source.execute("SELECT chapter_id FROM chapter_plans")
        ]
        event_ids = [
            row["event_id"]
            for row in source.execute(
                "SELECT event_id FROM events WHERE beat_id IN "
                f"({','.join('?' for _ in plan_ids)})",
                plan_ids,
            )
        ] if plan_ids else []
        fact_ids = [
            row["fact_id"]
            for row in source.execute(
                "SELECT fact_id FROM facts WHERE story_time>0"
                + (
                    " OR source_event_id IN "
                    f"({','.join('?' for _ in event_ids)})"
                    if event_ids else ""
                ),
                event_ids,
            )
        ]

        if fact_ids:
            marks = ",".join("?" for _ in fact_ids)
            source.execute(f"DELETE FROM agent_knowledge WHERE fact_id IN ({marks})", fact_ids)
            source.execute(f"DELETE FROM reader_knowledge WHERE fact_id IN ({marks})", fact_ids)
            source.execute(f"DELETE FROM facts WHERE fact_id IN ({marks})", fact_ids)
        source.execute("DELETE FROM scenes")
        if event_ids:
            marks = ",".join("?" for _ in event_ids)
            source.execute(f"DELETE FROM events WHERE event_id IN ({marks})", event_ids)
        source.execute("DELETE FROM character_chapter_logs")
        source.execute("DELETE FROM accepted_chapters")
        source.execute("DELETE FROM chapter_drafts")
        source.execute("DELETE FROM batch_audits")
        source.execute(
            "UPDATE reveal_chain SET discovered=0, discovered_chapter=NULL "
            "WHERE discovered_chapter IS NOT NULL"
        )
        source.execute(
            "UPDATE foreshadows SET status='open', payoff_discourse_pos=NULL "
            "WHERE payoff_discourse_pos IS NOT NULL"
        )
        source.execute(
            """UPDATE chapter_plans
                  SET title='', summary='', scene_ids='[]', status='planned', audited=0"""
        )

        # Accepted-chapter extraction may mutate inventory and graph attention.
        # Keep pre-story inventory rows; remove rows introduced only by prose.
        source.execute("DELETE FROM inventory WHERE acquired_chapter>0")
        source.execute(
            """UPDATE graph_edges
                  SET until_chapter=NULL,
                      last_active_chapter=CASE WHEN since_chapter=0 THEN 0 ELSE last_active_chapter END
                WHERE until_chapter IS NOT NULL"""
        )
        source.commit()
    except Exception:
        source.rollback()
        raise
    finally:
        source.close()

    print(
        json.dumps(
            {
                "database": str(path),
                "backup": str(backup),
                "reset": True,
                "preserved": ["chapter_plans", "world_bible", "entities", "character_cards"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
