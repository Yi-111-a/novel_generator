"""Generic deterministic quality gates for chapter plans."""
from __future__ import annotations

import re
from typing import Any

from .models import ChapterPlan
from .repository import Repository


_MOTIVE_RE = re.compile(
    r"为了|因为|想要|必须|不得不|受托|接下|接受|保护|寻找|查清|挽回|逃离|"
    r"失业|房租|生计|债务|继承|被迫|求助|责任|威胁|救人|自保"
)
_ENTRY_RE = re.compile(r"接到|收到|遇到|卷入|发现|被迫|受邀|委托|冲突|袭击|失踪|异常|请求")
_ACTION_RE = re.compile(
    r"决定|前往|寻找|调查|追查|行动|接下|进入|赶往|设法|准备|接入|接单|启动|开始|锁定"
)
# Override legacy mojibake patterns with real Unicode patterns. Keeping this
# assignment close to the gate makes old databases and current source layouts
# compatible without relying on the shell's code page.
_MOTIVE_RE = re.compile(
    r"为了|因为|想要|必须|不得不|受托|接下|接受|保护|寻找|查清|挽回|逃离|"
    r"失业|房租|生计|债务|继承|被迫|求助|责任|威胁|救人|自保"
)
_ENTRY_RE = re.compile(
    r"接到|收到|遇到|卷入|发现|被迫|受邀|委托|冲突|袭击|失踪|异常|请求|差评|工单"
)
_ACTION_RE = re.compile(
    r"决定|前往|寻找|调查|追查|行动|接下|进入|赶往|设法|准备|接入|接单|启动|开始|锁定"
)
_CJK_RE = re.compile(r"[\u4e00-\u9fff]{4,}")


def first_chapter_quality_issues(repo: Repository, chapter: ChapterPlan) -> list[dict[str, Any]]:
    if chapter.sequence_order != 1:
        return []
    world = repo.get_world_bible() if hasattr(repo, "get_world_bible") else {}
    if not any(
        str((world or {}).get(key) or "").strip()
        for key in ("setting_core", "protagonist_want", "theme", "physics_rules")
    ):
        return []
    beats = list(chapter.must_happen or chapter.beat_goals or []) + list(chapter.scene_flow or [])
    plan_text = "\n".join(
        str(item or "").strip()
        for item in [
            chapter.summary,
            chapter.dramatic_question,
            *beats,
            chapter.required_exit_state or chapter.exit_state,
        ]
        if str(item or "").strip()
    )
    issues: list[dict[str, Any]] = []
    if not _MOTIVE_RE.search(plan_text):
        issues.append(
            {
                "type": "opening_missing_motivation",
                "message": "第一章计划没有说明主角为何采取行动。",
            }
        )

    mechanism_source = "\n".join(
        str((world or {}).get(key) or "")
        for key in ("setting_core", "physics_rules", "theme")
    )
    mechanism_terms = {
        chunk[index:index + size]
        for chunk in _CJK_RE.findall(mechanism_source)
        for size in (3, 4, 5, 6)
        for index in range(max(0, len(chunk) - size + 1))
    }
    mechanism_present = bool(
        chapter.reveal_gate
        or chapter.allowed_fact_ids
        or any(term in plan_text for term in mechanism_terms)
    )
    if mechanism_source.strip() and not mechanism_present:
        issues.append(
            {
                "type": "opening_missing_core_mechanism",
                "message": "第一章计划没有最低限度引入本书的核心机制或题材规则。",
            }
        )
    if not _ENTRY_RE.search(plan_text):
        issues.append(
            {
                "type": "opening_missing_conflict_entry",
                "message": "第一章计划没有交代当前案件或冲突如何进入主角生活。",
            }
        )
    exit_state = str(chapter.required_exit_state or chapter.exit_state or "").strip()
    if not exit_state or not _ACTION_RE.search(exit_state):
        issues.append(
            {
                "type": "opening_missing_next_action",
                "message": "第一章章末没有形成明确的下一步行动目标。",
            }
        )
    return issues
