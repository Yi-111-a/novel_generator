"""Apply exact text replacements across accepted narrative persistence fields."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, "src")

from novel_engine import db


TEXT_COLUMNS = {
    "accepted_chapters": ["prose", "summary"],
    "chapter_drafts": ["prose", "outline", "context_snapshot_json"],
    "facts": ["canonical_content", "structured"],
    "events": ["payload"],
    "scenes": ["prose_text"],
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("database")
    parser.add_argument(
        "--replace",
        action="append",
        default=[],
        metavar="OLD=NEW",
    )
    args = parser.parse_args()
    replacements: list[tuple[str, str]] = []
    for item in args.replace:
        old, sep, new = item.partition("=")
        if not sep or not old:
            raise ValueError(f"invalid replacement: {item}")
        replacements.append((old, new))

    conn = db.connect(Path(args.database))
    counts: dict[str, int] = {}
    try:
        conn.execute("BEGIN IMMEDIATE")
        for table, columns in TEXT_COLUMNS.items():
            available = {
                row["name"]
                for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
            for column in columns:
                if column not in available:
                    continue
                for old, new in replacements:
                    cur = conn.execute(
                        f"UPDATE {table} SET {column}=REPLACE({column}, ?, ?) "
                        f"WHERE INSTR({column}, ?) > 0",
                        (old, new, old),
                    )
                    counts[f"{table}.{column}"] = (
                        counts.get(f"{table}.{column}", 0) + cur.rowcount
                    )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    print(counts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
