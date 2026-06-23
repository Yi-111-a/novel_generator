"""Chapter-level package compilation and permission validation."""
from __future__ import annotations

import json
import re
from typing import Any, Iterable

from .disclosure import disclosure_stage, get_disclosure_schedule
from .entity_matching import is_garbled_name, longest_name_matches, normalize_entity_name
from .llm.base import LLMClient
from .models import ChapterPlan
from .narration.text_integrity import contains_cjk, scan_text_integrity
from .planning_quality import first_chapter_quality_issues
from .repository import Repository


_EXACT_DATE_RE = re.compile(r"(?:19|20)\d{2}\s*[年/-]\s*\d{1,2}\s*[月/-]\s*\d{1,2}\s*日?")
_PLOT_SIGNAL_RE = re.compile(
    r"第\s*[一二三四五六七八九十百千万两零\d]+\s*(?:章|回|节)|随后|最终|真凶|凶手|尸骨|尸体|认罪|结案|监控证据"
)
_INVESTIGATION_RESULT_MARKERS = (
    "死因",
    "死亡时间",
    "DNA",
    "指纹比对",
    "监控显示",
    "警方确认",
    "凶手身份",
    "案件编号",
)
_CJK_CHUNK_RE = re.compile(r"[\u4e00-\u9fff]{2,}")
_GENERIC_FUTURE_WORDS = {
    "发现", "确认", "决定", "准备", "开始", "继续", "知道", "看到", "进入", "离开", "调查", "查到",
    "东西", "这里", "那里", "上面", "下面", "里面", "外面", "回来", "过去", "现在", "以后",
    "不能", "可以", "一条", "下一", "一个", "这个", "那个", "他们", "自己", "已经", "仍然",
}
_CONCEPT_SUFFIXES = {
    "记录", "资料", "档案", "信息", "地址", "身份", "地点", "公司",
    "名单", "证据", "婚戒", "雨伞", "尸骨", "合同", "钥匙",
}
# Throwaway everyday props are not plot-tracked items: a story should be free to
# mention a 纸条 / 杯子 / 椅子 without registering it as an authorized prop first.
# Any *secret* carried by such an object (e.g. the content of a note) is still
# guarded by the premature-reveal / secret-truth checks, so exempting the bare
# noun from the item-permission gate does not open a real leak. Extend as needed.
_GENERIC_OBJECT_NOUNS = {
    "纸条", "字条", "便条", "纸", "便签", "卡片",
    "杯子", "茶杯", "水杯", "碗", "盘子", "碟子", "筷子", "勺子", "叉子", "瓶子",
    "椅子", "凳子", "板凳", "桌子", "茶几", "沙发", "床",
    "毛巾", "手帕", "帕子", "抹布", "镜子", "梳子", "牙刷",
    "香烟", "烟", "烟头", "火柴", "蜡烛",
    "报纸", "杂志", "本子", "笔", "袋子", "塑料袋", "盒子", "绳子",
    "帽子", "围巾", "手套", "扇子",
    "水", "茶", "饭", "菜", "汤",
    "扫帚", "拖把", "垃圾桶", "板凳",
}


def is_generic_object_name(name: str) -> bool:
    """True for incidental everyday props that should never be permission-gated."""
    return normalize_entity_name(name) in _GENERIC_OBJECT_NOUNS


def _location_allowed(name: str, allowed_locations: set[str]) -> bool:
    """A mentioned location is allowed when it is exactly authorized, or when it
    is an **ancestor** of an authorized location by the "·" naming hierarchy
    (e.g. "槐荫巷44号" is licensed because its child "槐荫巷44号·售后服务处" is).
    Authorizing a specific room implicitly licenses the building it sits in.
    Descendants are NOT auto-allowed — a more specific sub-location may be a spoiler.
    """
    name = (name or "").strip()
    if not name or name in allowed_locations:
        return True
    prefix = name + "·"
    return any(allowed.startswith(prefix) for allowed in allowed_locations)


def _dedupe_texts(values: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = str(value or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _entity_map(repo: Repository) -> dict[str, Any]:
    return {entity.entity_id: entity for entity in repo.list_entities()}


def _all_disclosure_ids(repo: Repository) -> list[str]:
    ids = [entity.entity_id for entity in repo.list_entities()]
    ids.extend(
        faction.faction_id
        for faction in getattr(repo, "list_factions", lambda: [])()
    )
    return _dedupe_texts(ids)


def _entity_name(repo: Repository, entity_id: str) -> str:
    ent = _entity_map(repo).get(entity_id)
    if ent is not None:
        return str(ent.name or "").strip()
    faction = getattr(repo, "get_faction", lambda _id: None)(entity_id)
    return str(getattr(faction, "name", "") or "").strip()


def _entity_names(repo: Repository, ids: Iterable[str]) -> list[str]:
    entities = _entity_map(repo)
    return _dedupe_texts(
        entities[eid].name
        for eid in ids
        if eid in entities and (entities[eid].name or "").strip()
    )


def _future_keywords(markers: Iterable[str]) -> set[str]:
    out: set[str] = set()
    for marker in markers:
        text = str(marker or "").strip()
        if not text:
            continue
        for chunk in _CJK_CHUNK_RE.findall(text):
            if len(chunk) >= 4:
                out.add(chunk)
            for size in (2, 3, 4):
                if len(chunk) < size:
                    continue
                for idx in range(0, len(chunk) - size + 1):
                    piece = chunk[idx: idx + size]
                    if piece not in _GENERIC_FUTURE_WORDS:
                        out.add(piece)
    return {item for item in out if len(item) >= 2}


def _future_marker_evidence(
    marker: str,
    prose: str,
    ignored_grams: set[str] | None = None,
) -> list[str]:
    """Return meaningful overlap with one future marker.

    Evidence must come from the same marker.  The previous implementation
    pooled every 2-4 character n-gram from an entire future chapter, so common
    phrases such as “一条”, “不能”, and “下一” could combine into a false leak.
    """
    evidence: list[str] = []
    for chunk in _CJK_CHUNK_RE.findall(str(marker or "")):
        if len(chunk) < 6:
            continue
        if chunk in prose:
            evidence.append(chunk)
            continue
        trigrams = {
            chunk[idx: idx + 3]
            for idx in range(len(chunk) - 2)
            if chunk[idx: idx + 3] not in _GENERIC_FUTURE_WORDS
        }
        bigrams = {
            chunk[idx: idx + 2]
            for idx in range(len(chunk) - 1)
            if chunk[idx: idx + 2] not in _GENERIC_FUTURE_WORDS
        }
        grams = trigrams | bigrams
        grams.difference_update(ignored_grams or set())
        occurrences = sorted(
            (match.start(), gram)
            for gram in grams
            for match in re.finditer(re.escape(gram), prose)
        )
        window_size = max(24, min(64, len(chunk) * 2))
        for idx, (start, _gram) in enumerate(occurrences):
            local = {
                gram
                for position, gram in occurrences[idx:]
                if position <= start + window_size
            }
            hits = sorted(local, key=chunk.find)
            # A paraphrased leak must retain at least one distinctive
            # three-character sequence plus another nearby piece. A pile of
            # common two-character words is never sufficient evidence.
            trigram_hits = [item for item in hits if len(item) >= 3]
            covered_chars = sum(len(item) for item in hits)
            if (
                trigram_hits
                and len(hits) >= 2
                and covered_chars >= 5
                and len(hits) / max(1, len(grams)) >= 0.30
            ):
                evidence.extend(hits[:4])
                break
    return _dedupe_texts(evidence)


def _marker_overlaps_text(marker: str, text: str) -> bool:
    """marker 是否与 text（本章自己的 must_happen/scene_flow/exit）实质重叠。

    用于把「本章自己就要写、却又被某未来章列为 forbidden」的**改写式自冲突**剔除，
    避免契约自相矛盾（既命令写 X 又禁止 X）。精确子串命中、或模糊证据非空均算重叠。
    """
    marker = str(marker or "").strip()
    if not marker or not text:
        return False
    if marker in text:
        return True
    # Short concept labels are often expanded by the current chapter with one
    # modifier, e.g. "物业记录" -> "物业签收记录". Treat the ordered first/last
    # bigrams as the same concept when they occur in one local phrase.
    if len(marker) == 4:
        left, right = marker[:2], marker[-2:]
        left_at = text.find(left)
        right_at = text.find(right, left_at + 2) if left_at >= 0 else -1
        if left_at >= 0 and right_at >= 0 and right_at - left_at <= 12:
            return True
    # Future markers are usually full sentences. Detect a compact noun concept
    # inside them when the current chapter inserts a modifier into that concept:
    # "物业记录" -> "物业签收记录".
    for cjk in _CJK_CHUNK_RE.findall(marker):
        for idx in range(max(0, len(cjk) - 3)):
            concept = cjk[idx: idx + 4]
            left, right = concept[:2], concept[-2:]
            if right not in _CONCEPT_SUFFIXES:
                continue
            left_at = text.find(left)
            right_at = text.find(right, left_at + 2) if left_at >= 0 else -1
            if left_at >= 0 and right_at >= 0 and right_at - left_at <= 12:
                return True
    return bool(_future_marker_evidence(marker, text))


def _future_rows(repo: Repository, chapter: ChapterPlan) -> list[dict[str, Any]]:
    current_markers = set(
        _dedupe_texts(
            list(chapter.must_happen or [])
            + list(chapter.beat_goals or [])
            + list(chapter.scene_flow or [])
            + list(chapter.forbidden or [])
            + [chapter.required_exit_state or "", chapter.exit_state or ""]
        )
    )
    rows: list[dict[str, Any]] = []
    for future in sorted(repo.list_chapter_plans(), key=lambda item: item.sequence_order):
        if future.sequence_order <= chapter.sequence_order:
            continue
        markers = _dedupe_texts(
            list(future.must_happen or [])
            + list(future.beat_goals or [])
            + list(future.scene_flow or [])
            + list(future.forbidden or [])
            + [future.required_exit_state or "", future.exit_state or "", future.ending_hook or ""]
        )
        current_text = "\n".join(sorted(current_markers))
        filtered = [
            marker for marker in markers
            if marker not in current_markers
            and not _marker_overlaps_text(marker, current_text)
        ]
        rows.append(
            {
                "chapter": future.sequence_order,
                "must_happen": list(future.must_happen or future.beat_goals or []),
                "required_exit_state": future.required_exit_state or future.exit_state or "",
                "scene_flow": list(future.scene_flow or future.beat_goals or []),
                "forbidden": filtered,
                "keywords": sorted(_future_keywords(filtered)),
            }
        )
    return rows


def _canonical_table(
    repo: Repository,
    allowed_entity_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    allowed = set(allowed_entity_ids or [])
    aliases: dict[str, dict[str, Any]] = {}
    locations: dict[str, str] = {}
    dates: dict[str, str] = {}
    for ent in repo.list_entities():
        if allowed and ent.entity_id not in allowed:
            continue
        attrs = ent.attributes or {}
        legacy = list(attrs.get("forbidden_variants") or attrs.get("legacy_aliases") or [])
        role = str(attrs.get("canonical_role") or attrs.get("role_label") or "").strip()
        if ent.type == "character" and (legacy or role):
            aliases[role or ent.name] = {
                "canonical": ent.name,
                "forbidden_variants": legacy,
            }
        if ent.type == "location":
            address = str(attrs.get("canonical_address") or attrs.get("address") or "").strip()
            if address:
                locations[ent.name] = address
        for label, value in (attrs.get("canonical_dates") or {}).items():
            if str(value).strip():
                dates[str(label)] = str(value).strip()
    for history in getattr(repo, "list_character_name_history", lambda: [])():
        agent_id = str(history.get("agent_id") or "")
        if allowed and agent_id not in allowed:
            continue
        entity = repo.get_entity(agent_id)
        old_name = str(history.get("old_primary_name") or "").strip()
        if entity and old_name and old_name != entity.name:
            row = aliases.setdefault(
                entity.name,
                {"canonical": entity.name, "forbidden_variants": []},
            )
            row["forbidden_variants"] = _dedupe_texts(
                list(row.get("forbidden_variants") or []) + [old_name]
            )
    wb = repo.get_world_bible() if hasattr(repo, "get_world_bible") else {}
    canonical = (wb or {}).get("canonical") if isinstance(wb, dict) else {}
    if isinstance(canonical, dict):
        allowed_names = {
            _entity_name(repo, entity_id)
            for entity_id in allowed
            if _entity_name(repo, entity_id)
        }
        aliases.update(
            {
                role: row
                for role, row in (canonical.get("character_aliases") or {}).items()
                if not allowed
                or role in allowed_names
                or str((row or {}).get("canonical") or "") in allowed_names
            }
        )
        locations.update(
            {
                label: value
                for label, value in (canonical.get("locations") or {}).items()
                if not allowed or label in allowed_names
            }
        )
        dates.update(canonical.get("dates") or {})
    return {
        "character_aliases": aliases,
        "locations": locations,
        "dates": dates,
    }


def _item_sources(repo: Repository, chapter: ChapterPlan) -> dict[str, Any]:
    entities = _entity_map(repo)
    rows: dict[str, Any] = {}
    item_ids = _dedupe_texts(
        list(chapter.items_present or [])
        + list(chapter.available_items or [])
        + list(chapter.items_introduced or [])
    )
    for object_id in item_ids:
        entity = entities.get(object_id)
        if entity is None:
            continue
        inventory = getattr(repo, "get_inventory_item", lambda _id: None)(object_id)
        holder_name = ""
        status = "available"
        if inventory is not None:
            status = inventory.status
            if inventory.holder_agent_id:
                holder_name = next(
                    (
                        item.name
                        for item in repo.list_entities()
                        if item.entity_id == inventory.holder_agent_id
                    ),
                    inventory.holder_agent_id,
                )
        location_name = ""
        for location in getattr(repo, "list_locations", lambda: [])():
            if object_id in (location.notable_items or []):
                location_name = location.name
                break
        rows[object_id] = {
            "name": entity.name,
            "holder_id": inventory.holder_agent_id if inventory else None,
            "holder_name": holder_name,
            "location_name": location_name,
            "status": status,
            "note": inventory.note if inventory else "",
            "non_physical": bool((entity.attributes or {}).get("non_physical")),
            "source": str((entity.attributes or {}).get("source") or ""),
        }
    return rows


def _authorized_context_text(repo: Repository, entity_ids: Iterable[str]) -> str:
    """Public descriptors already licensed by the current package."""
    allowed = set(entity_ids)
    parts: list[str] = []
    for entity in repo.list_entities():
        if entity.entity_id not in allowed:
            continue
        parts.append(entity.name)
        attrs = entity.attributes or {}
        for key in ("summary", "public_summary", "canon", "source"):
            value = str(attrs.get(key) or "").strip()
            if value:
                parts.append(value)
    for card in getattr(repo, "list_cards", lambda: [])():
        if card.agent_id not in allowed:
            continue
        parts.extend(
            str(value or "").strip()
            for value in (
                card.name,
                card.one_liner,
                card.appearance,
                card.social_role,
                card.defining_trait,
            )
            if str(value or "").strip()
        )
    for location in getattr(repo, "list_locations", lambda: [])():
        if location.loc_id in allowed:
            parts.extend(
                value
                for value in (
                    location.name,
                    location.summary,
                    location.detail,
                    location.geo_full,
                )
                if value
            )
    for faction in getattr(repo, "list_factions", lambda: [])():
        if faction.faction_id in allowed:
            parts.extend(
                value
                for value in (faction.name, faction.summary, faction.detail)
                if value
            )
    return "\n".join(_dedupe_texts(parts))


def _implicit_item_ids(repo: Repository, chapter: ChapterPlan) -> list[str]:
    """Recover permissions omitted by legacy plans without opening all cast inventory."""
    ids: list[str] = []
    if chapter.pov_agent:
        ids.extend(
            item.object_id
            for item in getattr(repo, "items_held_by", lambda _id: [])(chapter.pov_agent)
        )
    for location_id in chapter.location_ids or []:
        location = getattr(repo, "get_location", lambda _id: None)(location_id)
        if location:
            ids.extend(location.notable_items or [])
    return _dedupe_texts(ids)


def _implicit_related_entity_ids(repo: Repository, chapter: ChapterPlan) -> list[str]:
    ids: list[str] = []
    factions = getattr(repo, "list_factions", lambda: [])()
    by_name = {normalize_entity_name(faction.name): faction.faction_id for faction in factions if faction.name}
    by_id = {faction.faction_id: faction.faction_id for faction in factions}
    def _resolve_faction(value: str) -> str:
        raw = str(value or "").strip()
        return by_id.get(raw) or by_name.get(normalize_entity_name(raw)) or ""

    for location_id in chapter.location_ids or []:
        location = getattr(repo, "get_location", lambda _id: None)(location_id)
        if location and location.controlling_faction:
            resolved = _resolve_faction(location.controlling_faction)
            if resolved:
                ids.append(resolved)
    for agent_id in chapter.cast or []:
        entity = getattr(repo, "get_entity", lambda _id: None)(agent_id)
        attrs = (getattr(entity, "attributes", None) or {}) if entity else {}
        faction_ref = next(
            (
                str(attrs.get(key) or "").strip()
                for key in ("faction_id", "faction", "faction_name", "organization")
                if str(attrs.get(key) or "").strip()
            ),
            "",
        )
        resolved = _resolve_faction(faction_ref)
        if resolved:
            ids.append(resolved)
    return _dedupe_texts(ids)


def _continuity_mention_ids(repo: Repository, chapter: ChapterPlan) -> list[str]:
    """Allow already-public persistent systems/organizations, never all entities."""
    previous_text = "\n".join(
        row.prose
        for row in getattr(repo, "list_accepted_chapters", lambda: [])()
        if row.chapter_no < chapter.sequence_order
    )
    source_text = "\n".join(
        row.text
        for row in getattr(repo, "list_source_chapters", lambda: [])()
        if row.chapter_no < chapter.sequence_order
        or getattr(repo.get_continuation_meta(), "write_mode", "") == "continue_current_book"
    )
    previous_text = "\n".join(part for part in (source_text, previous_text) if part)
    if not previous_text:
        return []
    ids: list[str] = []
    for faction in getattr(repo, "list_factions", lambda: [])():
        if faction.name and faction.name in previous_text:
            ids.append(faction.faction_id)
    for entity in repo.list_entities():
        attrs = entity.attributes or {}
        persistent = entity.type in {"faction", "organization", "system", "institution"} or bool(
            attrs.get("persistent_public_name")
        )
        if persistent and entity.name and entity.name in previous_text:
            ids.append(entity.entity_id)
    return _dedupe_texts(ids)


def _plan_reference_diagnostics(
    repo: Repository,
    chapter: ChapterPlan,
    allowed_entity_ids: Iterable[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Find package/planning contradictions before prose generation."""
    allowed = set(allowed_entity_ids)
    planning_text = "\n".join(
        _dedupe_texts(
            list(chapter.must_happen or [])
            + list(chapter.beat_goals or [])
            + list(chapter.scene_flow or [])
            + [chapter.required_exit_state or "", chapter.exit_state or ""]
        )
    )
    candidate_rows = [
        (entity.entity_id, entity.type, entity.name)
        for entity in repo.list_entities()
        if entity.name and not (entity.attributes or {}).get("merged_into")
    ]
    candidate_rows.extend(
        (faction.faction_id, "faction", faction.name)
        for faction in getattr(repo, "list_factions", lambda: [])()
        if faction.name
    )
    rows_by_name: dict[str, tuple[str, str, str]] = {}
    for row in sorted(candidate_rows, key=lambda item: item[0] not in allowed):
        rows_by_name.setdefault(normalize_entity_name(row[2]), row)
    rows = list(rows_by_name.values())
    planning_conflicts: list[dict[str, Any]] = []
    data_conflicts: list[dict[str, Any]] = []
    auto_authorized: list[str] = []
    for match in longest_name_matches(planning_text, rows):
        if match.entity_id in allowed:
            continue
        # 规划里点名了一个已知实体却没授权：分流而非一律 P0 阻断。
        # 本章已可完整登场的老实体（stage>=2）= 规划忘了同步白名单 → 自动补授权，仅 P1 提示。
        # 未来/未披露实体（stage<2，如被未来材料污染的"假林晚/锦澜湾"）→ 维持 P0 planning_conflict。
        if disclosure_stage(repo, match.entity_id, chapter.sequence_order) >= 2:
            allowed.add(match.entity_id)
            auto_authorized.append(match.entity_id)
            data_conflicts.append(
                {
                    "type": "auto_authorized_reference",
                    "entity_id": match.entity_id,
                    "entity_type": match.entity_type,
                    "name": match.name,
                    "message": "规划引用了本章已可登场的实体但未显式授权，已自动补授权。",
                }
            )
        else:
            planning_conflicts.append(
                {
                    "type": "planning_package_conflict",
                    "entity_id": match.entity_id,
                    "entity_type": match.entity_type,
                    "name": match.name,
                    "message": "规划要求出现未来/未披露实体，但章节权限包未授权该实体。",
                }
            )
    known_ids = {row[0] for row in rows}
    for entity_id in _chapter_entity_ids(chapter):
        if entity_id and entity_id not in known_ids:
            data_conflicts.append(
                {
                    "type": "missing_entity_reference",
                    "entity_id": entity_id,
                    "message": "章节计划引用了不存在的实体 ID。",
                }
            )
    for entity in repo.list_entities():
        attrs = entity.attributes or {}
        if entity.entity_id in allowed and (is_garbled_name(entity.name) or attrs.get("merged_into")):
            data_conflicts.append(
                {
                    "type": "stale_or_garbled_entity",
                    "entity_id": entity.entity_id,
                    "name": entity.name,
                    "message": "章节权限包含乱码名称或已归并的旧实体。",
                }
            )
    active_by_name: dict[str, list[str]] = {}
    for entity in repo.list_entities():
        if entity.entity_id not in allowed or (entity.attributes or {}).get("merged_into"):
            continue
        normalized = normalize_entity_name(entity.name)
        if normalized:
            active_by_name.setdefault(normalized, []).append(entity.entity_id)
    for name, entity_ids in active_by_name.items():
        if len(entity_ids) > 1:
            data_conflicts.append(
                {
                    "type": "duplicate_entity_residual",
                    "entity_ids": entity_ids,
                    "name": name,
                    "message": "章节权限包含多个同名活动实体，需先归并或确认其身份。",
                }
            )
    planning_conflicts = [
        dict(item)
        for item in {
            (
                row.get("type"),
                row.get("entity_id"),
                row.get("name"),
            ): row
            for row in planning_conflicts
        }.values()
    ]
    return planning_conflicts, data_conflicts, _dedupe_texts(auto_authorized)


def compile_chapter_package(repo: Repository, chapter: ChapterPlan) -> dict[str, Any]:
    must_happen = _dedupe_texts(chapter.must_happen or chapter.beat_goals or [])
    scene_flow = _dedupe_texts(chapter.scene_flow or must_happen)
    required_exit_state = str(chapter.required_exit_state or chapter.exit_state or "").strip()
    implicit_item_ids = _implicit_item_ids(repo, chapter)
    implicit_related_ids = _implicit_related_entity_ids(repo, chapter)
    continuity_mention_ids = _continuity_mention_ids(repo, chapter)
    allowed_entity_ids = _dedupe_texts(
        list(chapter.allowed_entity_ids or [])
        + list(chapter.cast or [])
        + list(chapter.location_ids or [])
        + list(chapter.items_present or [])
        + list(chapter.available_items or [])
        + list(chapter.items_introduced or [])
        + implicit_item_ids
        + implicit_related_ids
        + continuity_mention_ids
    )
    planning_conflicts, data_conflicts, auto_authorized = _plan_reference_diagnostics(
        repo, chapter, allowed_entity_ids
    )
    # 已可登场却忘了授权的实体被自动补进白名单，下游 allowed_full/may_mention 同步生效。
    if auto_authorized:
        allowed_entity_ids = _dedupe_texts(list(allowed_entity_ids) + auto_authorized)
    planning_conflicts.extend(first_chapter_quality_issues(repo, chapter))
    allowed_fact_ids = _dedupe_texts(chapter.allowed_fact_ids or chapter.reveal_gate or [])
    allowed_full = [
        entity_id
        for entity_id in allowed_entity_ids
        if disclosure_stage(repo, entity_id, chapter.sequence_order) >= 2
        or entity_id in auto_authorized
    ]
    allowed_hint_ids = [
        entity_id
        for entity_id in allowed_entity_ids
        if disclosure_stage(repo, entity_id, chapter.sequence_order) == 1
    ]
    allowed_hint = [
        {
            "entity_id": entity_id,
            "hint": get_disclosure_schedule(repo, entity_id).foreshadow_hint,
        }
        for entity_id in allowed_hint_ids
        if get_disclosure_schedule(repo, entity_id).foreshadow_hint
    ]
    forbidden_entity_ids = [
        entity_id
        for entity_id in _all_disclosure_ids(repo)
        if entity_id not in allowed_full and entity_id not in allowed_hint_ids
    ]
    future_locked = _future_rows(repo, chapter)
    forbidden = _dedupe_texts(
        list(chapter.forbidden or [])
        + [marker for row in future_locked for marker in row.get("forbidden", [])]
    )
    allowed_names = _dedupe_texts(_entity_name(repo, entity_id) for entity_id in allowed_full)
    entity_types = {
        entity.entity_id: entity.type
        for entity in repo.list_entities()
    }
    # may_mention 只从**本章 allowed 集**派生（地点/道具等可被提及的实体）。
    # 旧实现还扫 must_happen/scene_flow 里出现的任意实体名——一旦 beat 被未来材料污染
    # （如假林晚/锦澜湾），这些未来实体就被悄悄放行，校验再也拦不住。改为白名单内派生。
    may_mention = _dedupe_texts(
        [_entity_name(repo, entity_id) for entity_id in chapter.location_ids or [] if entity_id in allowed_full]
        + [_entity_name(repo, entity_id) for entity_id in chapter.items_present or [] if entity_id in allowed_full]
        + [_entity_name(repo, entity_id) for entity_id in chapter.available_items or [] if entity_id in allowed_full]
    )
    item_sources = chapter.item_sources or _item_sources(repo, chapter)
    established_facts: list[dict[str, Any]] = []
    latest_by_slot: dict[tuple[str, str], dict[str, Any]] = {}
    for fact in repo.list_facts():
        if int(fact.story_time or 0) >= int(chapter.sequence_order or 0):
            continue
        structured = fact.structured if isinstance(fact.structured, dict) else {}
        for row in structured.get("assertions") or []:
            if not isinstance(row, dict) or row.get("fact_class") != "narration":
                continue
            subject = str(row.get("subject", "")).strip()
            predicate = str(row.get("predicate", "")).strip()
            value = str(row.get("value", "")).strip()
            if not subject or not predicate or not value:
                continue
            latest_by_slot[(subject, predicate)] = {
                "subject": subject,
                "predicate": predicate,
                "value": value,
                "source_chapter": int(row.get("source_chapter", fact.story_time or 0)),
                "source_text": str(row.get("source_text", fact.canonical_content))[:100],
            }
    established_facts = list(latest_by_slot.values())[-160:]
    return {
        "chapter": chapter.sequence_order,
        "chapter_id": chapter.chapter_id,
        "package_version": int(chapter.package_version or 1),
        "must_happen": must_happen,
        "required_exit_state": required_exit_state,
        "scene_flow": scene_flow,
        "allowed_entity_ids": allowed_entity_ids,
        "allowed_full": allowed_full,
        "allowed_hint": allowed_hint,
        "forbidden_entity_ids": forbidden_entity_ids,
        "allowed_fact_ids": allowed_fact_ids,
        "forbidden": forbidden,
        "item_sources": item_sources,
        "established_facts": established_facts,
        "forbidden_inventions": [
            "未在章节合同或既有事实中出现的关键前史、婚史、亲属、长期经历",
            "没有来源的钥匙、线人、证件、证据、能力和关键道具",
            "未授权的精确时间、地点、身份关系、死因和调查结论",
            "把角色说法、文件说法或推测直接写成客观真相",
        ],
        "allowed_cast": _dedupe_texts(
            _entity_name(repo, entity_id)
            for entity_id in allowed_full
            if entity_types.get(entity_id) == "character"
        ),
        "allowed_locations": _dedupe_texts(
            _entity_name(repo, entity_id)
            for entity_id in allowed_full
            if entity_types.get(entity_id) == "location"
        ),
        "allowed_items": _dedupe_texts(
            _entity_name(repo, entity_id)
            for entity_id in allowed_full
            if entity_types.get(entity_id) == "object"
        ),
        "allowed_names": allowed_names,
        "may_mention": may_mention,
        "continuity_mention_ids": continuity_mention_ids,
        "future_locked": future_locked,
        "canonical": _canonical_table(repo, allowed_entity_ids),
        "diagnostics": {
            "planning_conflicts": planning_conflicts,
            "data_conflicts": data_conflicts,
        },
    }


def build_chapter_scope(repo: Repository, chapter: ChapterPlan) -> dict[str, Any]:
    return compile_chapter_package(repo, chapter)


def build_prose_chapter_scope(repo: Repository, chapter: ChapterPlan) -> dict[str, Any]:
    full = compile_chapter_package(repo, chapter)
    allowed_names = set(full["allowed_names"]) | set(full["may_mention"])
    canonical = full.get("canonical", {})
    redacted_canonical = {
        "character_aliases": {
            role: row
            for role, row in (canonical.get("character_aliases") or {}).items()
            if role in allowed_names or row.get("canonical") in allowed_names
        },
        "locations": {
            label: value
            for label, value in (canonical.get("locations") or {}).items()
            if label in allowed_names
        },
        "dates": canonical.get("dates", {}),
    }
    # P2.5：写手契约**不下发 forbidden 明细**。旧实现把每条未来章事件（"丈夫程行突然回家"
    # "白骨和红色雨伞残片"…）逐条塞进写作 prompt，等于把后续整本剧情报菜名——模型反而从中
    # 学到未来人物名/道具并写进正文。写手只需正向白名单（allowed_*/may_mention）+ system 里
    # "不得引入越界人物/地点/道具/真相"的总约束即可。forbidden 仅供 auditor（其独立重算契约）。
    return {
        key: value
        for key, value in full.items()
        if key not in {"future_locked", "canonical", "forbidden"}
    } | {
        "forbidden": [],
        "future_locked": [{"chapter": row["chapter"], "locked": True} for row in full["future_locked"]],
        "canonical": redacted_canonical,
    }


def _append(
    rows: list[dict[str, Any]],
    kind: str,
    text: str,
    belongs_to_chapter: int | None = None,
) -> None:
    item: dict[str, Any] = {"type": kind, "text": text}
    if belongs_to_chapter is not None:
        item["belongs_to_chapter"] = belongs_to_chapter
    rows.append(item)


def _chapter_entity_ids(chapter: ChapterPlan) -> set[str]:
    return set(
        list(chapter.allowed_entity_ids or [])
        + list(chapter.cast or [])
        + list(chapter.location_ids or [])
        + list(chapter.items_present or [])
        + list(chapter.available_items or [])
        + list(chapter.items_introduced or [])
    )


def _is_first_introduction(repo: Repository, chapter: ChapterPlan, entity_id: str) -> bool:
    if entity_id not in _chapter_entity_ids(chapter):
        return False
    return not any(
        entity_id in _chapter_entity_ids(previous)
        for previous in repo.list_chapter_plans()
        if previous.sequence_order < chapter.sequence_order
    )


def _has_required_foreshadow(
    repo: Repository,
    entity_id: str,
    foreshadow_from: int,
    reveal_chapter: int,
) -> bool:
    return any(
        row.status != "abandoned"
        and foreshadow_from <= int(row.planted_discourse_pos or 0) < reveal_chapter
        for row in getattr(repo, "foreshadows_for_fact", lambda _id: [])(entity_id)
    )


def validate_chapter_scope(
    repo: Repository,
    chapter: ChapterPlan,
    prose: str,
    llm: LLMClient | None = None,
) -> dict[str, Any]:
    contract = compile_chapter_package(repo, chapter)
    violations: list[dict[str, Any]] = []
    entities = list(repo.list_entities())
    prose = prose or ""

    expected_cjk = contains_cjk(
        "\n".join(
            contract["must_happen"]
            + contract["scene_flow"]
            + [contract["required_exit_state"]]
            + contract["allowed_names"]
        )
    )
    integrity = scan_text_integrity(prose, label="prose", expected_cjk=expected_cjk)
    for issue in integrity.issues:
        _append(violations, "text_encoding_corruption", issue.message)

    allowed_cast = set(contract["allowed_cast"]) | set(contract["may_mention"])
    allowed_locations = set(contract["allowed_locations"])
    allowed_items = set(contract["allowed_items"])
    hint_ids = {
        row.get("entity_id", "")
        for row in contract.get("allowed_hint", [])
        if row.get("entity_id")
    }
    for row in contract.get("allowed_hint", []):
        entity_id = row.get("entity_id", "")
        name = _entity_name(repo, entity_id)
        if name and name in prose:
            _append(violations, "premature_reveal", name)

    allowed_ids = set(contract.get("allowed_full", [])) | hint_ids
    entity_rows_by_name: dict[str, tuple[str, str, str]] = {}
    for ent in sorted(entities, key=lambda row: row.entity_id not in allowed_ids):
        if not ent.name or (ent.attributes or {}).get("merged_into"):
            continue
        entity_rows_by_name.setdefault(
            normalize_entity_name(ent.name),
            (ent.entity_id, ent.type, ent.name),
        )
    matched_entities = longest_name_matches(prose, entity_rows_by_name.values())
    entity_by_id = {ent.entity_id: ent for ent in entities}
    for match in matched_entities:
        ent = entity_by_id.get(match.entity_id)
        if ent is None or ent.entity_id in hint_ids:
            continue
        if ent.type == "character" and ent.name not in allowed_cast:
            _append(violations, "unauthorized_character", ent.name)
        elif ent.type == "location" and not _location_allowed(ent.name, allowed_locations):
            _append(violations, "unauthorized_location", ent.name)
        elif ent.type == "object" and ent.name not in allowed_items and not is_generic_object_name(ent.name):
            _append(violations, "unauthorized_item", ent.name)
    faction_rows = list(getattr(repo, "list_factions", lambda: [])())
    faction_by_id = {row.faction_id: row for row in faction_rows}
    allowed_names = set(contract.get("allowed_names", []))
    for match in longest_name_matches(
        prose,
        ((row.faction_id, "faction", row.name) for row in faction_rows if row.name),
    ):
        faction = faction_by_id.get(match.entity_id)
        if faction is not None:
            if faction.faction_id not in set(contract.get("allowed_full", [])):
                # A location/institution may have a same-name faction record
                # left by legacy materialization. One authorized exact-name
                # entity is enough to license the textual mention; identity
                # disambiguation belongs to the data reconciliation layer.
                if faction.name in allowed_names:
                    continue
                kind = (
                    "premature_reveal"
                    if any(
                        row.get("entity_id") == faction.faction_id
                        for row in contract.get("allowed_hint", [])
                    )
                    else "unauthorized_faction"
                )
                _append(violations, kind, faction.name)

    for entity_id in _all_disclosure_ids(repo):
        schedule = get_disclosure_schedule(repo, entity_id)
        stage = disclosure_stage(repo, entity_id, chapter.sequence_order)
        if (
            stage < 3
            and schedule.secret_truth
            and len(schedule.secret_truth) >= 4
            and schedule.secret_truth in prose
        ):
            _append(violations, "premature_reveal", schedule.secret_truth)
        if (
            entity_id in set(contract.get("allowed_full", []))
            and schedule.reveal_chapter > schedule.foreshadow_from
            and chapter.sequence_order >= schedule.reveal_chapter
            and _is_first_introduction(repo, chapter, entity_id)
            and not _has_required_foreshadow(
                repo,
                entity_id,
                schedule.foreshadow_from,
                schedule.reveal_chapter,
            )
        ):
            _append(
                violations,
                "unforeshadowed_introduction",
                _entity_name(repo, entity_id) or entity_id,
            )

    for row in contract["future_locked"]:
        leak_found = False
        current_text = "\n".join(
            contract["must_happen"]
            + contract["scene_flow"]
            + [contract["required_exit_state"]]
            + [_authorized_context_text(repo, contract.get("allowed_full", []))]
        )
        ignored_grams = {
            chunk[idx: idx + 2]
            for chunk in _CJK_CHUNK_RE.findall(current_text)
            for idx in range(len(chunk) - 1)
        }
        for marker in row["forbidden"]:
            compact_marker = re.sub(r"\s+", "", str(marker or ""))
            if len(compact_marker) >= 6 and marker in prose:
                _append(violations, "future_event_leak", marker, row["chapter"])
                leak_found = True
                break
            evidence = _future_marker_evidence(marker, prose, ignored_grams)
            if evidence:
                _append(
                    violations,
                    "future_event_leak",
                    " / ".join(evidence),
                    row["chapter"],
                )
                leak_found = True
                break
        if leak_found:
            continue

    allowed_facts = set(contract["allowed_fact_ids"])
    for fact in repo.list_facts():
        if fact.fact_id in allowed_facts:
            continue
        content = (fact.canonical_content or "").strip()
        if len(content) >= 4 and content in prose:
            _append(violations, "unauthorized_truth_reveal", content)

    canonical = contract["canonical"]
    for row in canonical["character_aliases"].values():
        for variant in row.get("forbidden_variants", []) or []:
            if variant and variant in prose:
                _append(violations, "canonical_name_drift", variant)
    for label, _address in canonical["locations"].items():
        if label not in prose:
            continue
        ent = next((item for item in entities if item.type == "location" and item.name == label), None)
        variants = list((ent.attributes or {}).get("forbidden_addresses") or []) if ent else []
        for variant in variants:
            if variant and variant in prose:
                _append(violations, "canonical_address_drift", variant)

    exact_dates = _EXACT_DATE_RE.findall(prose)
    locked_dates = set(canonical["dates"].values())
    for date in exact_dates:
        if not locked_dates or date not in locked_dates:
            _append(violations, "invented_exact_date", date)

    current_contract_text = "\n".join(
        contract["must_happen"] + contract["scene_flow"] + [contract["required_exit_state"]]
    )
    for marker in _INVESTIGATION_RESULT_MARKERS:
        if marker in prose and marker not in current_contract_text:
            _append(violations, "new_investigation_result", marker)

    if llm is not None:
        violations.extend(_llm_scope_check(contract, prose, llm))

    unique: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for item in violations:
        key = (item.get("type"), item.get("text"), item.get("belongs_to_chapter"))
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return {
        "ok": not unique,
        "severity": "pass" if not unique else "blocker",
        "contract": contract,
        "violations": unique,
        "rewriteAdvice": _rewrite_advice(unique),
    }


def _llm_scope_check(
    contract: dict[str, Any],
    prose: str,
    llm: LLMClient,
) -> list[dict[str, Any]]:
    system = (
        "You are a chapter-scope auditor for a Chinese fiction pipeline. "
        "Only detect permission violations, future leaks, unauthorized truth reveals, "
        "and text corruption. Return JSON only with keys ok and violations."
    )
    user = (
        f"[chapter_package]\n{json.dumps(contract, ensure_ascii=False, indent=2)[:12000]}\n\n"
        f"[prose]\n{(prose or '')[:16000]}"
    )
    try:
        raw = llm.complete(system, user).strip().strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
        start, end = raw.find("{"), raw.rfind("}")
        data = json.loads(raw[start:end + 1] if 0 <= start < end else raw)
    except Exception:
        return []
    rows = data.get("violations") if isinstance(data, dict) else []
    return [
        item for item in (rows or [])
        if isinstance(item, dict) and str(item.get("type") or "").strip()
    ][:20]


def _rewrite_advice(violations: list[dict[str, Any]]) -> str:
    if not violations:
        return ""
    details = []
    for item in violations[:12]:
        suffix = f"(belongs to chapter {item['belongs_to_chapter']})" if item.get("belongs_to_chapter") else ""
        details.append(f"{item.get('type', 'scope_violation')}: {item.get('text', '')}{suffix}")
    return (
        "Delete or delay every out-of-scope detail. "
        "Do not preserve the leak via paraphrase. "
        "Only complete the required exit state. Problems: "
        + "; ".join(details)
    )


def contains_plotting_content(text: str) -> bool:
    return bool(_PLOT_SIGNAL_RE.search(text or ""))
