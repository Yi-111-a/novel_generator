"""Diagnose or safely reconcile a project database copy.

Usage:
  python scripts/diagnose_chapter_pipeline.py path/to/project.db
  python scripts/diagnose_chapter_pipeline.py path/to/project.db --apply
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from novel_engine import db
from novel_engine.chapter_scope_validator import compile_chapter_package
from novel_engine.data_reconciliation import reconcile_legacy_conflicts
from novel_engine.repository import Repository


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("database")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    path = Path(args.database).resolve()
    repo = Repository(db.connect(path, check_same_thread=False))
    try:
        reconciliation = reconcile_legacy_conflicts(repo, apply=args.apply)
        chapters = []
        for plan in repo.list_chapter_plans():
            package = compile_chapter_package(repo, plan)
            diagnostics = package.get("diagnostics") or {}
            if diagnostics.get("planning_conflicts") or diagnostics.get("data_conflicts"):
                chapters.append(
                    {
                        "chapter": plan.sequence_order,
                        "chapter_id": plan.chapter_id,
                        "diagnostics": diagnostics,
                    }
                )
        payload = {
            "database": str(path),
            "applied": args.apply,
            "reconciliation": reconciliation,
            "chapter_diagnostics": chapters,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    finally:
        repo.conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
