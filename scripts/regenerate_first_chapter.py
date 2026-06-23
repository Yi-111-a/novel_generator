"""Generate a fresh chapter-one draft through the production pipeline."""
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
from novel_engine.story_bible import DraftManager


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
    if project.repo is None or project._gen_llm is None:
        raise SystemExit("project_generation_client_unavailable")
    if project.repo.list_accepted_chapters() or project.repo.list_chapter_drafts():
        raise SystemExit("project_prose_not_empty")

    settings = project.repo.get_writing_settings()
    draft = DraftManager(
        project.repo,
        project._gen_llm,
        project_id=project.id,
    ).generate(
        guidance=(
            "作为全书第一章，清楚建立主角当前困境与行动动机，"
            "让核心题材机制通过事件自然显现，并在章末形成明确的下一步行动。"
            "严格按本章权限包写作，不提前解释后续案件答案。"
            "本章不要展示未授权的随身物品或前雇主物品；"
            "后台只呈现投诉原文与接单倒计时，不展示死因、死亡时间等调查字段。"
        ),
        target_words=settings.target_words,
        mode="manual",
    )
    print(
        json.dumps(
            {
                "draft_id": draft.id,
                "chapter_no": draft.chapter_no,
                "title": draft.title,
                "status": draft.status,
                "characters": len("".join(draft.prose.split())),
                "combined_audit": (
                    draft.context_snapshot_json.get("combinedAudit") or {}
                ),
                "word_count": draft.context_snapshot_json.get("wordCount") or {},
                "pipeline_audit": draft.context_snapshot_json.get("pipelineAudit") or {},
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
