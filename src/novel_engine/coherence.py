from __future__ import annotations

import json
from contextlib import nullcontext
from typing import Any

from .llm.base import LLMClient
from .repository import Repository


def check_drift(
    repo: Repository,
    llm: LLMClient | None = None,
    last_n: int = 5,
    chapter_no: int = 0,
) -> str:
    """Return soft guidance when recent chapter exits drift away from the main line."""
    if llm is None:
        return ""
    chapters = [
        c for c in sorted(repo.list_chapter_plans(), key=lambda x: x.sequence_order)
        if c.status == "done" and (c.exit_state or "").strip()
    ][-max(1, last_n):]
    if len(chapters) < 3:
        return ""
    wb = repo.get_world_bible() if hasattr(repo, "get_world_bible") else {}
    protagonist_want = str((wb or {}).get("protagonist_want", "") or "").strip()
    antagonist_profile = (wb or {}).get("antagonist_profile") if isinstance(wb, dict) else {}
    antagonist_goal = ""
    if isinstance(antagonist_profile, dict):
        antagonist_goal = str(antagonist_profile.get("goal", "") or "").strip()
    if not (protagonist_want or antagonist_goal):
        return ""
    exits = "\n".join(f"- 第{c.sequence_order}章：{c.exit_state}" for c in chapters)
    system = (
        "你是长篇小说主线一致性审稿人。判断最近几章的出口状态是否服务主线。"
        "只输出 JSON：{\"score\":0到1,\"guidance\":\"\"}。"
    )
    user = (
        f"主角核心目标：{protagonist_want or '（无）'}\n"
        f"反派目标：{antagonist_goal or '（无）'}\n"
        f"最近章节出口状态：\n{exits}\n"
        "score=最近几章服务主线的程度；若 score<0.5，guidance 给一句给下一章 planner 的具体纠偏。"
    )
    try:
        scope = getattr(llm, "scope", None)
        context = (
            scope(
                caller="coherence_audit",
                meta={"chapter_no": chapter_no, "phase": "pre_generation", "attempt": 1},
            )
            if callable(scope)
            else nullcontext()
        )
        with context:
            raw = llm.complete(system, user)
        data = _parse_json(raw)
        score = float(data.get("score", 1))
        guidance = str(data.get("guidance", "") or "").strip()
    except Exception:
        return ""
    if score >= 0.5:
        return ""
    main_line = protagonist_want or antagonist_goal
    if not guidance:
        guidance = f"把焦点拉回主线：{main_line}"
    return f"【主线偏离自检】{guidance}"


def _parse_json(raw: str) -> dict[str, Any]:
    text = (raw or "").strip().strip("`")
    if text.lower().startswith("json"):
        text = text[4:].strip()
    try:
        data = json.loads(text)
    except Exception:
        start, end = text.find("{"), text.rfind("}")
        data = json.loads(text[start:end + 1] if 0 <= start < end else text)
    return data if isinstance(data, dict) else {}
