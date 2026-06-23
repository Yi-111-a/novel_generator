#!/usr/bin/env python3
"""MCP server exposing the Novel World engine as agent-callable tools.

Turns the long-form-writing Harness into tools any MCP client (Claude
Desktop, IDE agents, etc.) can call: inspect a project's world state and
knowledge graph, and run the deterministic continuity guard-rails over a
chapter's prose — no LLM calls, all read-only over the existing project
databases under server/.data/projects/.

Run (stdio):  python mcp_server/novelworld_mcp.py
"""
from __future__ import annotations

import json
import sys
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from mcp.server.fastmcp import FastMCP

from novel_engine import db
from novel_engine.models import ChapterPlan
from novel_engine.repository import Repository
from novel_engine.chapter_scope_validator import validate_chapter_scope
from novel_engine.narration.audit import _P0_VIOLATION_TYPES, _phantom_items, _violation_severity
from novel_engine.narration.story_clock import audit_time_regression
from novel_engine.disclosure import disclosure_stage

mcp = FastMCP("novelworld_mcp")

DATA_DIR = ROOT / "server" / ".data"
PROJECTS_DIR = DATA_DIR / "projects"

_READONLY = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #
def _load_projects() -> list[dict]:
    f = DATA_DIR / "projects.json"
    if not f.exists():
        return []
    data = json.loads(f.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else data.get("projects", [])


def _open_repo(project_id: str) -> Repository:
    """Open a project's SQLite DB as a Repository, or raise a clear error."""
    path = PROJECTS_DIR / f"{project_id}.db"
    if not path.exists():
        known = ", ".join(p.get("id", "") for p in _load_projects()) or "(none)"
        raise FileNotFoundError(
            f"未找到项目「{project_id}」。可用项目 id：{known}。"
            "先用 novel_list_projects 查看。"
        )
    return Repository(db.connect(str(path)))


# 可读章节由场景按「高潮处断章」分组得到（与服务端 group_chapters 对齐）；
# 章节计划(chapter_plans) 是规划侧，场景(scenes) 是正文侧，两者按章序号配对。
PEAK_TENSION = 0.66
MIN_SCENES_PER_CHAPTER = 2


def _entity_names(repo: Repository) -> dict[str, str]:
    names = {e.entity_id: e.name for e in repo.list_entities()}
    for fac in getattr(repo, "list_factions", lambda: [])():
        names.setdefault(fac.faction_id, fac.name)
    return names


def _readable_chapters(repo: Repository) -> list[dict[str, Any]]:
    """把场景（按话语顺序）分组成可读章节：累积到高张力峰值场即收束成一章。"""
    scenes = sorted(repo.list_scenes(), key=lambda s: s.discourse_order)
    chapters: list[list[Any]] = []
    cur: list[Any] = []
    for s in scenes:
        cur.append(s)
        if (s.target_tension or 0) >= PEAK_TENSION and len(cur) >= MIN_SCENES_PER_CHAPTER:
            chapters.append(cur); cur = []
    out = [{"index": i, "scenes": ch, "status": "done"} for i, ch in enumerate(chapters, 1)]
    if cur:
        out.append({"index": len(chapters) + 1, "scenes": cur, "status": "ongoing"})
    return out


def _chapter_prose(scenes: list[Any]) -> str:
    return "\n".join((s.prose_text or "") for s in scenes).strip()


def _plans_by_seq(repo: Repository) -> dict[int, Any]:
    return {c.sequence_order: c for c in repo.list_chapter_plans()}


def _err(msg: str) -> str:
    return json.dumps({"error": msg}, ensure_ascii=False)


# --------------------------------------------------------------------------- #
# Input models
# --------------------------------------------------------------------------- #
class _Base(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


class ProjectInput(_Base):
    project_id: str = Field(..., description="项目 id（如 'proj_31157567'）", min_length=1, max_length=64)


class GraphInput(_Base):
    project_id: str = Field(..., description="项目 id（如 'proj_31157567'）", min_length=1, max_length=64)
    entity_id: Optional[str] = Field(
        default=None,
        description="只看与该实体相关的边（作为 src 或 dst）。留空=返回全部边。",
    )
    rel: Optional[str] = Field(
        default=None,
        description="按关系类型过滤，如 member_of|controls|allied|hostile|knows|located_in。",
    )
    limit: int = Field(default=50, description="最多返回多少条边", ge=1, le=500)
    offset: int = Field(default=0, description="分页偏移", ge=0)


class ChaptersInput(_Base):
    project_id: str = Field(..., description="项目 id", min_length=1, max_length=64)
    limit: int = Field(default=50, description="最多返回多少章", ge=1, le=500)
    offset: int = Field(default=0, description="分页偏移", ge=0)


class AuditInput(_Base):
    project_id: str = Field(..., description="项目 id", min_length=1, max_length=64)
    chapter_seq: int = Field(..., description="要审计的章号（全书章序，从 novel_list_chapters 取）", ge=1)


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #
@mcp.tool(name="novel_list_projects", annotations={"title": "List novel projects", **_READONLY})
def novel_list_projects() -> str:
    """列出所有小说项目（原创/续写）。

    Returns:
        JSON: {"count": int, "projects": [{"id","title","type","status"}]}
        用 id 调用其它工具。
    """
    projects = [
        {k: p.get(k, "") for k in ("id", "title", "type", "status")}
        for p in _load_projects()
    ]
    return json.dumps({"count": len(projects), "projects": projects}, ensure_ascii=False, indent=2)


@mcp.tool(name="novel_get_world_bible", annotations={"title": "Get project world bible", **_READONLY})
def novel_get_world_bible(params: ProjectInput) -> str:
    """读取项目的世界观设定（Story Bible 各分节文本：设定内核/风土/规则/历史等）。

    Args:
        params.project_id: 项目 id。

    Returns:
        JSON: {"project_id", "world_bible": str, "entity_count": int}
        若项目不存在 → {"error": "..."}。
    """
    try:
        repo = _open_repo(params.project_id)
    except FileNotFoundError as e:
        return _err(str(e))
    text = repo.bible_sections_text(max_chars=8000) or "（该项目尚无世界观设定）"
    return json.dumps(
        {"project_id": params.project_id, "world_bible": text,
         "entity_count": len(repo.list_entities())},
        ensure_ascii=False, indent=2,
    )


@mcp.tool(name="novel_query_knowledge_graph", annotations={"title": "Query knowledge graph", **_READONLY})
def novel_query_knowledge_graph(params: GraphInput) -> str:
    """查询项目知识图谱（W5）：实体之间的关系边（势力/同盟/敌对/隶属/知情等），
    含注意力强度 intensity 与最近激活章 last_active_chapter。

    Args:
        params: project_id（必填）、entity_id（可选，按实体过滤）、rel（可选，按关系过滤）、
                limit/offset（分页）。

    Returns:
        JSON: {"count","total","offset","has_more","edges":[
                 {"src","src_name","rel","dst","dst_name","intensity",
                  "since_chapter","last_active_chapter","note"}]}
    """
    try:
        repo = _open_repo(params.project_id)
    except FileNotFoundError as e:
        return _err(str(e))

    names = _entity_names(repo)
    if params.entity_id:
        edges = repo.list_edges(src=params.entity_id, rel=params.rel) + \
            repo.list_edges(dst=params.entity_id, rel=params.rel)
        seen, uniq = set(), []
        for e in edges:                       # de-dup（自指边两侧都命中）
            key = (e.src, e.rel, e.dst)
            if key not in seen:
                seen.add(key); uniq.append(e)
        edges = uniq
    else:
        edges = repo.list_edges(rel=params.rel)

    total = len(edges)
    page = edges[params.offset:params.offset + params.limit]
    rows = [
        {
            "src": e.src, "src_name": names.get(e.src, e.src),
            "rel": e.rel,
            "dst": e.dst, "dst_name": names.get(e.dst, e.dst),
            "intensity": round(e.intensity, 3),
            "since_chapter": e.since_chapter,
            "last_active_chapter": e.last_active_chapter,
            "note": (e.meta or {}).get("note", ""),
        }
        for e in page
    ]
    return json.dumps(
        {"count": len(rows), "total": total, "offset": params.offset,
         "has_more": total > params.offset + len(rows), "edges": rows},
        ensure_ascii=False, indent=2,
    )


@mcp.tool(name="novel_list_chapters", annotations={"title": "List project chapters", **_READONLY})
def novel_list_chapters(params: ChaptersInput) -> str:
    """列出项目章节计划（章号/标题/在场角色/本章节拍目标/状态/是否已有正文）。

    Args:
        params: project_id（必填）、limit/offset（分页）。

    Returns:
        JSON: {"count","total","offset","has_more","chapters":[
                 {"seq","chapter_id","title","status","cast","beat_goals","has_prose"}]}
    """
    try:
        repo = _open_repo(params.project_id)
    except FileNotFoundError as e:
        return _err(str(e))

    names = _entity_names(repo)
    plans = _plans_by_seq(repo)
    chapters = _readable_chapters(repo)
    total = len(chapters)
    page = chapters[params.offset:params.offset + params.limit]
    rows = []
    for ch in page:
        plan = plans.get(ch["index"])
        prose = _chapter_prose(ch["scenes"])
        rows.append({
            "seq": ch["index"],
            "title": (plan.title if plan and plan.title else f"第{ch['index']}章"),
            "status": ch["status"],
            "scene_count": len(ch["scenes"]),
            "prose_chars": len(prose),
            "cast": [names.get(a, a) for a in (plan.cast or [])] if plan else [],
            "beat_goals": (plan.beat_goals or [])[:4] if plan else [],
        })
    return json.dumps(
        {"count": len(rows), "total": total, "offset": params.offset,
         "has_more": total > params.offset + len(rows), "chapters": rows},
        ensure_ascii=False, indent=2,
    )


@mcp.tool(name="novel_audit_chapter", annotations={"title": "Audit chapter continuity", **_READONLY})
def novel_audit_chapter(params: AuditInput) -> str:
    """对某一章的已存正文跑确定性连续性护栏，返回按类型聚合的连续性报告（不调用 LLM）。

    硬信号（cross_chapter，对重审稳健）：故事时钟时间倒流。
    建议项（scope_advisory，相对该章规划白名单）：角色/地点/道具越权登场、提前揭示、
    道具复活——这些是生成期闸门，对已存正文做事后重审会偏严（白名单未必齐全），
    故按类型给计数+样例，不据此判 blocked。

    Args:
        params: project_id、chapter_seq（可读章号，来自 novel_list_chapters）。

    Returns:
        JSON: {"project_id","chapter_seq","prose_chars","verdict":"clean|time_conflict",
               "time_regression": bool,
               "scope_advisory": {"<type>": {"count": int, "examples": [str]}},
               "note": str}
        若该章不存在或无正文 → {"error": "..."}。
    """
    try:
        repo = _open_repo(params.project_id)
    except FileNotFoundError as e:
        return _err(str(e))

    readable = {c["index"]: c for c in _readable_chapters(repo)}
    rc = readable.get(params.chapter_seq)
    if rc is None:
        avail = sorted(readable) or "(无)"
        return _err(f"项目「{params.project_id}」没有第 {params.chapter_seq} 章正文。"
                    f"可审计章号：{avail}。用 novel_list_chapters 查看。")
    prose = _chapter_prose(rc["scenes"])
    if not prose:
        return _err(f"第 {params.chapter_seq} 章尚无正文，无法审计。")
    # 配对规划侧 ChapterPlan（提供白名单/章序）；缺失则用最小计划兜底（仍可查时间倒流）。
    ch = _plans_by_seq(repo).get(params.chapter_seq) or ChapterPlan(
        chapter_id=f"c{params.chapter_seq}", arc_id="", sequence_order=params.chapter_seq)

    # —— 硬信号：时间倒流（跨章，对重审稳健）——
    time_regression = False
    try:
        time_regression = audit_time_regression(repo, ch, prose) is not None
    except Exception:
        pass

    # —— 建议项：相对该章规划白名单的范围/道具检查（事后重审偏严，按类型聚合，不判 blocked）——
    by_type: dict[str, list[str]] = {}

    def _add(vtype: str, text: str) -> None:
        bucket = by_type.setdefault(vtype, [])
        if text and text not in bucket:
            bucket.append(text)

    try:
        for v in validate_chapter_scope(repo, ch, prose, llm=None).get("violations", []):
            _add(str(v.get("type", "")), str(v.get("text") or v.get("name") or ""))
    except Exception:
        pass
    try:
        for name in _phantom_items(repo, ch, prose):
            _add("item_revival", name)
    except Exception:
        pass

    advisory = {t: {"count": len(xs), "examples": xs[:5]} for t, xs in sorted(by_type.items())}
    verdict = "time_conflict" if time_regression else "clean"
    return json.dumps(
        {"project_id": params.project_id, "chapter_seq": params.chapter_seq,
         "prose_chars": len(prose), "verdict": verdict,
         "time_regression": time_regression,
         "scope_advisory": advisory,
         "note": "verdict 仅由时间倒流（硬信号）决定；scope_advisory 是相对规划白名单的"
                 "生成期闸门，对已存正文为事后重审、可能偏严，仅供参考。"},
        ensure_ascii=False, indent=2,
    )


if __name__ == "__main__":
    mcp.run()
