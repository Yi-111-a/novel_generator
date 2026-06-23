"""Rebuild one existing project database from its saved seed draft.

The project id and seed in projects.json are preserved. The previous database
and index are backed up before a fresh world/outline build starts.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, "src")
sys.path.insert(0, ".")

from server import config_store  # noqa: E402
from server.projects import INDEX_PATH, Project, set_config_provider  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("project_id")
    args = parser.parse_args()

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    projects = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    target = next((row for row in projects if row.get("id") == args.project_id), None)
    if target is None:
        raise SystemExit(f"project not found: {args.project_id}")
    if not isinstance(target.get("draft"), dict) or not target["draft"]:
        raise SystemExit("project has no saved seed draft")

    index_backup = INDEX_PATH.with_name(f"projects.before_seed_rebuild_{stamp}.json")
    shutil.copy2(INDEX_PATH, index_backup)

    project = Project.from_snapshot(target)
    db_path = Path(project.db_path)
    db_backup = db_path.with_name(f"{db_path.stem}.before_seed_rebuild_{stamp}.db")
    if db_path.exists():
        shutil.copy2(db_path, db_backup)
        db_path.unlink()

    set_config_provider(config_store.load_config)
    project.status = "seeding"
    project.analysis_status = "idle"
    project.lock_and_build()
    target.clear()
    target.update(project.snapshot())
    INDEX_PATH.write_text(
        json.dumps(projects, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    parts_count = len(project.repo.list_parts()) if project.repo else 0
    chapters_count = len(project.repo.list_chapter_plans()) if project.repo else 0
    if project.read_repo is not None:
        project.read_repo.conn.close()
    if project.repo is not None:
        project.repo.conn.close()

    print(json.dumps({
        "project_id": args.project_id,
        "database": str(db_path),
        "database_backup": str(db_backup) if db_backup.exists() else "",
        "index_backup": str(index_backup),
        "parts": parts_count,
        "chapters": chapters_count,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
