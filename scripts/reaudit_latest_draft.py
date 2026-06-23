"""Re-audit the latest draft and persist the exact adopted result."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from server import config_store
from server.projects import INDEX_PATH, Project, set_config_provider
from novel_engine.narration.audit import run_combined_chapter_audit


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("project_id")
    args = parser.parse_args()
    set_config_provider(config_store.load_config)
    rows = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    snapshot = next((row for row in rows if row.get("id") == args.project_id), None)
    if snapshot is None:
        raise SystemExit(f"project_not_found:{args.project_id}")
    project = Project.from_snapshot(snapshot)
    project.ensure_writing_repo()
    if project.repo is None:
        raise SystemExit("project_repo_unavailable")
    draft = project.repo.list_chapter_drafts()[0]
    plan = next(
        row for row in project.repo.list_chapter_plans()
        if row.sequence_order == draft.chapter_no
    )
    previous = next(
        (
            row for row in reversed(project.repo.list_chapter_plans())
            if row.sequence_order < plan.sequence_order
        ),
        None,
    )
    llm = project._gen_llm
    scope = getattr(llm, "scope", None)
    if callable(scope):
        context = scope(
            caller="chapter_scope_audit",
            meta={
                "project_id": project.id,
                "chapter_no": draft.chapter_no,
                "phase": "permission_reaudit",
                "attempt": 1,
            },
        )
    else:
        from contextlib import nullcontext
        context = nullcontext()
    with context:
        combined = run_combined_chapter_audit(
            project.repo, plan, draft.prose, previous, llm
        )
    adopted = {
        "decision": combined.decision,
        "classification": combined.classification,
        "title": combined.title,
        "scores": combined.scores,
        "violations": combined.violations,
        "rewriteTargets": combined.rewrite_targets,
    }
    next_snapshot = {
        **(draft.context_snapshot_json or {}),
        **combined.summary,
        "combinedAudit": adopted,
        "automaticAuditRewriteCount": int(
            (draft.context_snapshot_json or {}).get("automaticAuditRewriteCount", 0)
        ),
        "manualRewriteConfirmationRequired": combined.decision != "accept",
    }
    status = "pending_acceptance" if combined.decision == "accept" else "blocked"
    next_snapshot["pipelineAudit"] = {
        "status": status,
        "wordCount": next_snapshot.get("wordCount") or {},
        "permission": adopted,
    }
    project.repo.update_chapter_draft_snapshot(draft.id, next_snapshot)
    project.repo.update_chapter_draft_status(draft.id, status)
    title = str(combined.title or draft.title).strip()
    if title:
        project.repo.conn.execute(
            "UPDATE chapter_drafts SET title=? WHERE id=?",
            (title, draft.id),
        )
        project.repo.conn.commit()
    print(
        json.dumps(
            {
                "draft_id": draft.id,
                "status": status,
                "title": title,
                "audit": adopted,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
