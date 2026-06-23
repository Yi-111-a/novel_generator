from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from . import db

from .naming_generator import reconcile_project_names
from .repository import Repository


def migrate_legacy_character_names(repo: Repository) -> dict:
    records, batch = reconcile_project_names(repo)
    return {
        "updated": len(records),
        "records": [asdict(record) for record in records],
        "batchAudit": batch,
    }


def migrate_project_database(db_path: str | Path) -> dict:
    repo = Repository(db.connect(db_path, check_same_thread=False))
    try:
        result = migrate_legacy_character_names(repo)
        result["dbPath"] = str(db_path)
        return result
    finally:
        repo.conn.close()
