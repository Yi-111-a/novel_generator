"""关键场景档案（捕获/快照）。

悬疑等强连续性题材里，犯罪现场、主角据点这类反复出现的场景需要一份结构化档案。
capture_scene_anchors 在章节验收后从正文抽取重要场景并**锁定其不变事实**（首次确立即
锁定，后续章节只追加新事实、不覆盖），作为人物/场景参考快照（前端可展示、可人工编辑）。

跨章一致性审计已统一由 fact_delta（断言级、含 narration/claim/hypothesis 区分）负责，
本模块不再做一致性判定，只保留档案捕获。无 LLM 或无档案时是安全空操作。
"""
from __future__ import annotations

import json
import uuid
from contextlib import nullcontext
from datetime import datetime, timezone

from ..entity_matching import normalize_entity_name
from ..llm.base import LLMClient
from ..models import SceneAnchor
from ..repository import Repository

_VALID_KINDS = {"crime_scene", "base", "landmark", "scene"}


def _safe_json(raw: str) -> dict | None:
    text = (raw or "").strip().strip("`")
    if text.lower().startswith("json"):
        text = text[4:].strip()
    start, end = text.find("{"), text.rfind("}")
    try:
        data = json.loads(text[start : end + 1] if 0 <= start < end else text)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _scope(llm: LLMClient, caller: str, chapter_no: int):
    scope = getattr(llm, "scope", None)
    if callable(scope):
        return scope(caller=caller, meta={"chapter_no": chapter_no})
    return nullcontext()


def _clean_facts(raw) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in raw or []:
        text = str(item or "").strip()
        key = normalize_entity_name(text)
        if text and key not in seen:
            seen.add(key)
            out.append(text)
    return out


def capture_scene_anchors(
    repo: Repository,
    chapter,
    prose: str,
    llm: LLMClient | None,
) -> list[SceneAnchor]:
    """从已验收章节抽取关键场景并锁定/追加其不变事实。返回写入的档案。"""
    if llm is None or not (prose or "").strip() or chapter is None:
        return []
    chapter_no = int(getattr(chapter, "sequence_order", 0) or 0)
    system = (
        "你是小说连续性档案员。从正文里识别**反复出现或剧情关键**的场景"
        "（犯罪现场、主角据点、关键地标等），为每个场景提炼**后续章节不该改变的事实**："
        "所处位置/相对方位、内部布局、关键物证或物件、出入通道等，逐条简短客观。"
        "一次性的普通背景（路边、随便一家店）不要登记。"
        '只输出 JSON：{"scenes":[{"name":"场景名","kind":"crime_scene|base|landmark|scene","facts":["…","…"]}]}'
    )
    user = f"【正文】\n{(prose or '')[:8000]}\n\n只输出 JSON。"
    try:
        with _scope(llm, "scene_anchor_capture", chapter_no):
            data = _safe_json(llm.complete(system, user))
    except Exception:
        return []
    if not data:
        return []

    existing = repo.list_scene_anchors()
    by_key: dict[str, SceneAnchor] = {}
    for anchor in existing:
        for label in [anchor.name, *anchor.aliases]:
            key = normalize_entity_name(label)
            if key:
                by_key.setdefault(key, anchor)

    written: list[SceneAnchor] = []
    for scene in data.get("scenes") or []:
        if not isinstance(scene, dict):
            continue
        name = str(scene.get("name", "")).strip()
        facts = _clean_facts(scene.get("facts"))
        if not name or not facts:
            continue
        kind = str(scene.get("kind", "scene")).strip()
        if kind not in _VALID_KINDS:
            kind = "scene"
        key = normalize_entity_name(name)
        anchor = by_key.get(key)
        if anchor is None:
            # 首次确立：锁定本章给出的事实。
            anchor = SceneAnchor(
                scene_id=f"scn_{uuid.uuid4().hex[:8]}",
                name=name,
                kind=kind,
                canonical_facts=facts,
                established_chapter=chapter_no,
                created_at=datetime.now(timezone.utc).isoformat(),
            )
        else:
            # 已存在：只**追加**未记录过的新事实，不覆盖已锁定内容。
            merged = list(anchor.canonical_facts)
            known = {normalize_entity_name(f) for f in merged}
            for fact in facts:
                if normalize_entity_name(fact) not in known:
                    merged.append(fact)
                    known.add(normalize_entity_name(fact))
            if name and normalize_entity_name(name) != normalize_entity_name(anchor.name):
                if name not in anchor.aliases:
                    anchor.aliases = [*anchor.aliases, name]
            anchor.canonical_facts = merged
        repo.upsert_scene_anchor(anchor)
        by_key[key] = anchor
        written.append(anchor)
    return written
