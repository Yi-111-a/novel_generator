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
import re
import sys
from enum import Enum
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from mcp.server.fastmcp import FastMCP

from novel_engine import db
from novel_engine.chapter_scope_validator import compile_chapter_package, validate_chapter_scope
from novel_engine.config import LLMConfig
from novel_engine.llm import build_client
from novel_engine.llm.logging_wrapper import LoggingLLMClient
from novel_engine.models import ChapterDraftRecord, ChapterPlan
from novel_engine.narration.audit import _P0_VIOLATION_TYPES, _phantom_items, _violation_severity, run_combined_chapter_audit
from novel_engine.narration.retrieval import build_context, chapter_seeds
from novel_engine.repository import Repository
from novel_engine.narration.story_clock import audit_time_regression
from novel_engine.story_bible.chapter_indexer import ChapterIndexer
from novel_engine.disclosure import disclosure_stage
from server import config_store

mcp = FastMCP("novelworld_mcp")

DATA_DIR = ROOT / "server" / ".data"
PROJECTS_DIR = DATA_DIR / "projects"
HANDWRITTEN_DRAFTS_DIR = ROOT / "drafts"

_READONLY = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}

_MUTATING = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _llm(repo: Repository):
    cfg = config_store.load_config()
    key = str(cfg.get("llmApiKey") or "").strip()
    if not key:
        raise RuntimeError("缺少 LLM API key，无法运行原生 combined audit / FactExtractor")
    raw = build_client(
        LLMConfig(
            provider="deepseek",
            model=cfg["modelName"],
            base_url=cfg["baseUrl"],
            api_key=key,
        )
    )
    return LoggingLLMClient(raw, repo.conn, caller="mcp_agent_handwritten")


def _plan_by_seq(repo: Repository, chapter_seq: int) -> ChapterPlan:
    plan = next((row for row in repo.list_chapter_plans() if row.sequence_order == chapter_seq), None)
    if plan is None:
        raise ValueError(f"找不到第 {chapter_seq} 章 chapter_plan")
    return plan


def _previous_plan(repo: Repository, chapter_seq: int) -> ChapterPlan | None:
    previous = [row for row in repo.list_chapter_plans() if row.sequence_order < chapter_seq]
    return max(previous, key=lambda row: row.sequence_order, default=None)


def _entity_name(repo: Repository, entity_id: str | None) -> str:
    if not entity_id:
        return ""
    entity = repo.get_entity(entity_id)
    if entity:
        return entity.name
    for faction in getattr(repo, "list_factions", lambda: [])():
        if faction.faction_id == entity_id:
            return faction.name
    return entity_id


def _validate_agent_prose(prose: str, *, allow_short: bool) -> int:
    if not prose.strip():
        raise ValueError("正文为空")
    if re.search(r"(?:\r?\n)[ \t]*(?:\r?\n)", prose):
        raise ValueError("正文含空行；本项目要求 Markdown 正文不得留空行")
    cjk = len(re.findall(r"[\u3400-\u9fff]", prose))
    if not allow_short and not 2000 <= cjk <= 3500:
        raise ValueError(f"CJK 字数={cjk}，不在建议区间 2000-3500；需要例外时设置 allow_short=true")
    return cjk


def _handwritten_draft_path(chapter_seq: int) -> Path:
    return HANDWRITTEN_DRAFTS_DIR / f"laozu_ch{chapter_seq}.md"


# 可读章节 = 已采纳章节(accepted_chapters)，与读者阅读页一致，按章序号 1:1 成章。
# 章节计划(chapter_plans) 是规划侧，accepted_chapters 是正文侧，两者按章序号配对。


def _entity_names(repo: Repository) -> dict[str, str]:
    names = {e.entity_id: e.name for e in repo.list_entities()}
    for fac in getattr(repo, "list_factions", lambda: [])():
        names.setdefault(fac.faction_id, fac.name)
    return names


def _readable_chapters(repo: Repository) -> list[dict[str, Any]]:
    """可读章节 = 已采纳章节(accepted_chapters)，按章序号 1:1 成章。

    每次 ChapterIndexer.accept 写入 1 章正文 + 1 个场景，故章节数应等于已采纳数。
    不可再用「按张力峰值合并场景」的旧启发式——那假设“一章=多场景”，在当前
    “一章=一场景”的生成模型下会把多章揉成一章（曾把 20 章错并成 3 章）。
    无已采纳章节的在制项目回退到场景流（每个场景自成一章）。
    """
    accepted = sorted(repo.list_accepted_chapters(), key=lambda r: r.chapter_no)
    scene_by_order = {s.discourse_order: s for s in repo.list_scenes()}
    if accepted:
        return [
            {
                "index": row.chapter_no,
                "status": "done",
                "prose": (row.prose or "").strip(),
                "title": row.title or "",
                "scenes": ([scene_by_order[row.chapter_no]]
                           if row.chapter_no in scene_by_order else []),
            }
            for row in accepted
        ]
    # 回退：纯场景在制项目，每个场景自成一章（仍 1:1，不做张力合并）。
    scenes = sorted(repo.list_scenes(), key=lambda s: s.discourse_order)
    return [
        {"index": i, "status": "done", "prose": (s.prose_text or "").strip(),
         "title": "", "scenes": [s]}
        for i, s in enumerate(scenes, 1)
    ]


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


class AgentChapterInput(_Base):
    project_id: str = Field(..., description="项目 id", min_length=1, max_length=64)
    chapter_seq: int = Field(..., description="全书章序", ge=1)
    recent_limit: int = Field(default=3, description="返回最近多少章已采纳正文尾部", ge=1, le=8)


class SubmitAgentChapterInput(_Base):
    project_id: str = Field(..., description="项目 id", min_length=1, max_length=64)
    chapter_seq: int = Field(..., description="全书章序", ge=1)
    prose: str = Field(..., description="Agent 亲手写的正文 Markdown；不得含空行", min_length=1)
    title: str = Field(default="", description="可选标题；留空则使用 chapter_plan 标题", max_length=80)
    allow_short: bool = Field(default=False, description="允许低于 2000 CJK；剧情完整但短章时才设 true")


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
        prose = ch.get("prose") or _chapter_prose(ch["scenes"])
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
    prose = rc.get("prose") or _chapter_prose(rc["scenes"])
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


@mcp.tool(name="novel_prepare_agent_chapter", annotations={"title": "Prepare chapter package for agent writer", **_READONLY})
def novel_prepare_agent_chapter(params: AgentChapterInput) -> str:
    """为“Agent 亲手写正文”准备完整项目上下文。

    这个工具不生成正文、不调用 ChapterWriter、不推进剧情；它只读取项目原生
    chapter_plan、chapter_package、最近已采纳章节、人物卡、地点、世界观、
    知识图谱、故事时钟和道具状态，供外部 agent 作为写手使用。

    Returns:
        JSON: 包含 chapter_plan / chapter_package / recent_accepted_chapters /
        personas / locations / world_bible / knowledge_graph_context /
        story_clock / inventory_and_techniques。
    """
    try:
        repo = _open_repo(params.project_id)
        plan = _plan_by_seq(repo, params.chapter_seq)
        package = compile_chapter_package(repo, plan)
        accepted = sorted(repo.list_accepted_chapters(), key=lambda row: row.chapter_no)
        recent = accepted[-params.recent_limit:]
        seeds = chapter_seeds(plan)
        graph_context = build_context(
            repo,
            seeds,
            budget=5000,
            chapter_seq=params.chapter_seq,
            allowed_entity_ids=set(package.get("allowed_entity_ids") or []),
        )
        world = repo.get_world_bible()
        world_payload = (
            dict(world)
            if isinstance(world, dict)
            else {
                "setting": getattr(world, "setting", ""),
                "culture": getattr(world, "culture", {}),
                "geography": getattr(world, "geography", {}),
                "physics_rules": getattr(world, "physics_rules", []),
                "protagonist_want": getattr(world, "protagonist_want", ""),
                "theme": getattr(world, "theme", ""),
            }
        )
        world_payload["sections"] = repo.list_bible_sections()
        clock_rows = repo.conn.execute(
            """SELECT event_id, story_time, action_type, payload, location_id
                 FROM events
                WHERE action_type='story_clock'
                ORDER BY story_time DESC, rowid DESC LIMIT 12"""
        ).fetchall()
        inventory = [
            {
                "object_id": item.object_id,
                "name": _entity_name(repo, item.object_id),
                "holder_agent_id": item.holder_agent_id,
                "holder": _entity_name(repo, item.holder_agent_id) if item.holder_agent_id else None,
                "status": item.status,
                "acquired_chapter": item.acquired_chapter,
                "note": item.note,
            }
            for item in repo.list_inventory()
        ]
        payload = {
            "project_id": params.project_id,
            "project_database": str((PROJECTS_DIR / f"{params.project_id}.db").resolve()),
            "chapter_no": params.chapter_seq,
            "chapter_plan": {
                "chapter_id": plan.chapter_id,
                "title": plan.title,
                "summary": plan.summary,
                "beat_goals": plan.beat_goals,
                "ending_hook": plan.ending_hook,
                "pov_agent": plan.pov_agent,
                "cast": plan.cast,
                "location_ids": plan.location_ids,
                "reveal_gate": plan.reveal_gate,
                "status": plan.status,
                "target_words": plan.target_words,
                "time_hint": plan.time_hint,
                "items_present": plan.items_present,
            },
            "chapter_package": package,
            "recent_accepted_chapters": [
                {
                    "chapter_no": row.chapter_no,
                    "title": row.title,
                    "summary": row.summary,
                    "tail": row.prose[-1200:],
                }
                for row in recent
            ],
            "personas": [
                {
                    "agent_id": row.agent_id,
                    "name": row.name,
                    "want": row.want,
                    "values": row.values,
                    "fatal_flaw": row.fatal_flaw,
                    "voice": row.voice,
                    "mannerisms": row.mannerisms,
                    "arc_state": row.arc_state,
                }
                for row in repo.list_personas()
            ],
            "locations": [
                {
                    "loc_id": row.loc_id,
                    "name": row.name,
                    "summary": row.summary,
                    "detail": row.detail,
                    "notable_items": row.notable_items,
                }
                for row in repo.list_locations()
            ],
            "world_bible": world_payload,
            "knowledge_graph_context": graph_context,
            "story_clock": [dict(row) for row in clock_rows],
            "inventory_and_techniques": inventory,
            "writer_contract": [
                "只写 prose，不调用 auto_write/write_next_chapter/ChapterWriter。",
                "不得引入 chapter_package 未授权的人物、地点、道具和未来真相。",
                "正文不得留空行。",
                "林凡限制性第三人称；轻松搞笑但危机真实。",
                "众生补史不是言出法随，必须受相信人数、传播范围、现实依托和成本限制。",
            ],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)
    except Exception as e:
        return _err(str(e))
    finally:
        try:
            repo.conn.close()
        except Exception:
            pass


@mcp.tool(name="novel_submit_agent_chapter", annotations={"title": "Submit agent-written chapter", **_MUTATING})
def novel_submit_agent_chapter(params: SubmitAgentChapterInput) -> str:
    """提交 Agent 亲手写的正文，并走项目原生审计/采纳/索引/知识图谱流程。

    这个工具是“Agent 只替代 ChapterWriter 写 prose”的正式接入口：
    chapter_plan -> compile_chapter_package -> agent prose ->
    run_combined_chapter_audit -> ChapterIndexer.accept -> FactExtractor 更新图谱。

    它不会调用 ChapterWriter、auto_write、write_next_chapter 或推进引擎。
    如果该章已被 accepted，会拒绝覆盖，避免重复事件/事实。
    """
    try:
        repo = _open_repo(params.project_id)
        if any(row.chapter_no == params.chapter_seq for row in repo.list_accepted_chapters()):
            return _err(f"第 {params.chapter_seq} 章已经采纳，MCP 提交工具拒绝自动覆盖")
        plan = _plan_by_seq(repo, params.chapter_seq)
        prose = params.prose.strip()
        cjk = _validate_agent_prose(prose, allow_short=params.allow_short)
        draft_path = _handwritten_draft_path(params.chapter_seq)
        HANDWRITTEN_DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
        draft_path.write_text(prose, encoding="utf-8")
        package = compile_chapter_package(repo, plan)
        planning_conflicts = (package.get("diagnostics") or {}).get("planning_conflicts") or []
        if planning_conflicts:
            return _err(f"chapter_package 存在 planning_conflicts：{planning_conflicts}")
        scope = validate_chapter_scope(repo, plan, prose, llm=None)
        if not scope.get("ok"):
            return json.dumps(
                {
                    "ok": False,
                    "stage": "scope_validation",
                    "chapter_no": params.chapter_seq,
                    "cjk": cjk,
                    "scope": scope,
                },
                ensure_ascii=False,
                indent=2,
            )
        llm = _llm(repo)
        combined = run_combined_chapter_audit(
            repo,
            plan,
            prose,
            _previous_plan(repo, params.chapter_seq),
            llm,
        )
        snapshot = {
            "source": "mcp_agent_handwritten",
            "surface_text": prose,
            "chapter_package": package,
            **combined.summary,
            "combinedAudit": {
                "decision": combined.decision,
                "classification": combined.classification,
                "title": combined.title,
                "scores": combined.scores,
                "violations": combined.violations,
                "rewriteTargets": combined.rewrite_targets,
            },
            "pipelineAudit": {
                "status": "pending_acceptance" if combined.decision == "accept" else "blocked",
                "wordCount": {"cjk": cjk, "ok": True},
                "permission": {
                    "decision": combined.decision,
                    "classification": combined.classification,
                },
            },
        }
        draft = ChapterDraftRecord(
            project_id=params.project_id,
            chapter_no=params.chapter_seq,
            title=params.title or plan.title or f"第{params.chapter_seq}章",
            outline=plan.summary or "",
            prose=prose,
            guidance="mcp-agent-handwritten-via-native-harness",
            target_words=cjk,
            mode="manual",
            status="pending_acceptance" if combined.decision == "accept" else "blocked",
            context_snapshot_json=snapshot,
            created_at=_now(),
        )
        draft.id = repo.create_chapter_draft(draft)
        if combined.decision != "accept":
            return json.dumps(
                {
                    "ok": False,
                    "stage": "combined_audit",
                    "draft_id": draft.id,
                    "chapter_no": params.chapter_seq,
                    "cjk": cjk,
                    "decision": combined.decision,
                    "classification": combined.classification,
                    "violations": combined.violations,
                },
                ensure_ascii=False,
                indent=2,
            )
        accepted = ChapterIndexer(repo, llm).accept(draft)
        stored = next(row for row in repo.list_accepted_chapters() if row.chapter_no == params.chapter_seq)
        stored_plan = _plan_by_seq(repo, params.chapter_seq)
        if stored.prose != prose:
            return _err("采纳后数据库正文与提交正文不一致")
        if draft_path.read_text(encoding="utf-8").strip() != prose:
            return _err("采纳后 Markdown 正文与提交正文不一致")
        if stored_plan.status != "done":
            return _err("采纳后 chapter_plan 未标记 done")
        counts = {
            "accepted_chapters": len(repo.list_accepted_chapters()),
            "events": len(repo.list_events()),
            "facts": len(repo.list_facts()),
            "graph_edges": len(repo.list_edges()),
        }
        return json.dumps(
            {
                "ok": True,
                "accepted_id": accepted.id,
                "draft_id": draft.id,
                "chapter_no": accepted.chapter_no,
                "title": accepted.title,
                "draft_markdown": str(draft_path.resolve()),
                "cjk": cjk,
                "verified": counts,
                "audit": {
                    "decision": combined.decision,
                    "classification": combined.classification,
                    "violations": combined.violations,
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    except Exception as e:
        return _err(str(e))
    finally:
        try:
            repo.conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    mcp.run()
