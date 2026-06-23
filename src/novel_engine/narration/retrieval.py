"""W6 RAG 注入：基于知识图谱的子图检索 + Lorebook 风格 token 预算。

核心 API：
  build_context(repo, seeds, budget) -> str
    seeds = 本场/本章涉及的实体 id 集合（cast + location + faction 等）
    budget = 最大注入字符数
    返回拼好的上下文字符串，可直接塞进 system/user prompt。

分级策略（仿 SillyTavern Lorebook）：
  - 常驻层（always-on）：世界观各节 summary + 在场地点 summary + 涉及势力 summary
  - 种子层（seed-detail）：直接种子实体的 detail（人物卡/地点/势力）
  - 图谱层（graph-expand）：1-2 跳邻居按 intensity 排序，取 detail 片段
  - 关键词层（keyword-trigger）：beat 文本命中实体名 → 触发该实体 detail

每层在 budget 内依优先级填充，超 budget 截断。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..chapter_scope_validator import contains_plotting_content
from ..disclosure import (
    disclosure_stage,
    get_disclosure_schedule,
    split_legacy_mystery_one_liner,
)
from ..repository import Repository

DEFAULT_BUDGET = 3000


@dataclass
class ContextBlock:
    """一个待注入的上下文片段。"""
    label: str
    text: str
    priority: int = 0  # 越小越优先
    entity_id: str = ""


def _char_len(s: str) -> int:
    return len(s)


def _available_in_chapter(repo: Repository, eid: str, chapter_seq: int | None) -> bool:
    """Backward-compatible boolean view of the disclosure policy."""
    return _disclosure_stage(repo, eid, chapter_seq) > 0


def _disclosure_stage(repo: Repository, eid: str, chapter_seq: int | None) -> int:
    return disclosure_stage(repo, eid, chapter_seq)


def _safe_world_text(text: str) -> str:
    """Drop plot demonstrations from context while retaining rules/environment."""
    if not text:
        return ""
    rows = re.split(r"(?<=[。！？；\n])", text)
    return "".join(row for row in rows if not contains_plotting_content(row)).strip()


def _strip_secret_overlap(text: str, secret: str, n: int = 6) -> str:
    """Drop sentences that verbatim-overlap a not-yet-revealed secret.

    Safety net for the case where a public card field (one_liner / social_role /
    defining_trait) was mistakenly populated with the character's twist: even if the
    field is dirty, the secret cannot be injected before its stage-3 reveal.
    """
    secret_compact = re.sub(r"\s+", "", secret or "")
    if not text or len(secret_compact) < n:
        return text
    kept: list[str] = []
    for line in re.split(r"(?<=[。！？；\n])", text):
        compact = re.sub(r"\s+", "", line)
        leaks = len(compact) >= n and any(
            compact[i: i + n] in secret_compact for i in range(len(compact) - n + 1)
        )
        if not leaks:
            kept.append(line)
    return "".join(kept).strip()


def _assemble_with_disclosure(public: str, stage: int, secret: str) -> str:
    """Stage-aware assembly: redact verbatim secret leaks below stage 3, append the
    explicit reveal only at stage 3."""
    if not public:
        return public
    if stage < 3 and secret:
        public = _strip_secret_overlap(public, secret)
    if stage == 3 and secret:
        return f"{public}\n揭秘：{secret}"
    return public


# ---------------------------------------------------------------------------
# 种子扩展：从 seeds 出发在图谱上 1-2 跳扩展
# ---------------------------------------------------------------------------

def _expand_seeds(repo: Repository, seeds: set[str], hops: int = 1,
                  per_seed_limit: int = 8) -> list[tuple[str, float]]:
    """从种子节点扩展邻居，返回 [(entity_id, max_intensity)] 去重后按 intensity 降序。"""
    fn = getattr(repo, "attention_ranked_neighbors", None)
    if fn is None:
        return []
    seen: dict[str, float] = {}
    frontier = list(seeds)
    for _hop in range(hops):
        next_frontier = []
        for s in frontier:
            for edge in fn(s, limit=per_seed_limit):
                other = edge.dst if edge.src == s else edge.src
                if other not in seeds:
                    old = seen.get(other, 0.0)
                    seen[other] = max(old, edge.intensity)
                    if _hop == 0:
                        next_frontier.append(other)
        frontier = next_frontier
    ranked = sorted(seen.items(), key=lambda x: -x[1])
    return ranked


# ---------------------------------------------------------------------------
# 实体 detail 提取
# ---------------------------------------------------------------------------

def _card_snippet(repo: Repository, agent_id: str, prose_safe: bool = False) -> str:
    """人物卡摘要：名字 + one_liner + 三维度精简。"""
    fn = getattr(repo, "get_card_for_agent", None)
    card = fn(agent_id) if fn else None
    if not card:
        return ""
    one_liner = card.one_liner or ""
    if prose_safe:
        one_liner, _legacy_secret = split_legacy_mystery_one_liner(one_liner)
    parts = [f"【{card.name}】{one_liner}"]
    if card.tier == "lead":
        if card.appearance:
            parts.append(f"外貌：{card.appearance[:120]}")
        if card.social_role:
            parts.append(f"社会：{card.social_role[:120]}")
        if card.psychology:
            parts.append(f"心理：{card.psychology[:120]}")
        if card.backstory and not prose_safe:
            parts.append(f"小传：{card.backstory[:200]}")
        if card.arc and not prose_safe:
            parts.append(f"弧线：{card.arc[:100]}")
    else:
        if card.defining_trait:
            parts.append(f"特质：{card.defining_trait}")
        if card.backstory and not prose_safe:
            parts.append(f"小传：{card.backstory[:100]}")
    if card.voice_register:
        parts.append(f"语域：{card.voice_register}")
    if card.verbal_habits:
        parts.append(f"口头禅：{card.verbal_habits}")
    return "\n".join(parts)


def _location_snippet(repo: Repository, loc_id: str, prose_safe: bool = False) -> str:
    fn = getattr(repo, "get_location", None)
    loc = fn(loc_id) if fn else None
    if not loc:
        return ""
    parts = [f"【{loc.name}】"]
    if loc.detail:
        parts.append((_safe_world_text(loc.detail) if prose_safe else loc.detail)[:300])
    elif loc.geo_full:
        parts.append(loc.geo_full[:300])
    if loc.culture_local:
        parts.append(f"风土：{loc.culture_local[:150]}")
    return "\n".join(parts)


def _faction_snippet(repo: Repository, fac_id: str, prose_safe: bool = False) -> str:
    fn = getattr(repo, "get_faction", None)
    fac = fn(fac_id) if fn else None
    if not fac:
        return ""
    parts = [f"【{fac.name}】{fac.summary or ''}"]
    if fac.ideology:
        parts.append(f"信条：{fac.ideology[:100]}")
    if fac.goals and not prose_safe:
        parts.append(f"目标：{fac.goals[:100]}")
    if fac.methods and not prose_safe:
        parts.append(f"手段：{fac.methods[:100]}")
    if fac.detail and not prose_safe:
        parts.append(fac.detail[:200])
    return "\n".join(parts)


def _display_name(repo: Repository, eid: str) -> str:
    if not eid:
        return ""
    ent = next((e for e in repo.list_entities() if e.entity_id == eid), None)
    if ent and ent.name:
        return ent.name
    fac_fn = getattr(repo, "get_faction", None)
    if fac_fn:
        fac = fac_fn(eid)
        if fac and fac.name:
            return fac.name
    persona = getattr(repo, "get_persona", lambda _id: None)(eid)
    if persona and persona.name:
        return persona.name
    return eid


def _object_snippet(repo: Repository, object_id: str) -> str:
    ent = next((e for e in repo.list_entities() if e.entity_id == object_id), None)
    if not ent:
        return ""
    item = getattr(repo, "get_inventory_item", lambda _id: None)(object_id)
    attrs = ent.attributes or {}
    parts = [f"【{ent.name}】物品"]
    detail = (
        attrs.get("canon_detail")
        or attrs.get("detail")
        or attrs.get("description")
        or attrs.get("note")
        or ""
    )
    if detail:
        parts.append(f"设定：{str(detail)[:220]}")
    if item:
        holder = _display_name(repo, item.holder_agent_id or "")
        if holder:
            parts.append(f"当前持有者：{holder}")
        else:
            loc_holder = _object_location_holder(repo, object_id)
            if loc_holder:
                parts.append(f"当前所在：{loc_holder}")
            else:
                parts.append("当前持有者：无主/场景固有")
        parts.append(f"状态：{item.status}")
        if item.acquired_chapter:
            parts.append(f"取得章节：第{item.acquired_chapter}章")
        if item.note:
            parts.append(f"备注：{item.note[:160]}")
    else:
        loc_holder = _object_location_holder(repo, object_id)
        if loc_holder:
            parts.append(f"当前所在：{loc_holder}")
    if attrs:
        keys = ("source", "origin", "use", "function", "symbol", "owner_hint")
        extras = []
        for key in keys:
            val = attrs.get(key)
            if val:
                extras.append(f"{key}={str(val)[:80]}")
        if extras:
            parts.append("附加：" + "；".join(extras))
    return "\n".join(parts)


def _object_location_holder(repo: Repository, object_id: str) -> str:
    loc_fn = getattr(repo, "list_locations", None)
    if not loc_fn:
        return ""
    for loc in loc_fn():
        if object_id in (loc.notable_items or []):
            return loc.name
    return ""


def _entity_snippet(
    repo: Repository,
    eid: str,
    prose_safe: bool = False,
    chapter_seq: int | None = None,
) -> str:
    """根据实体类型返回对应的 detail 片段。已销毁/献祭的物品不注入。"""
    stage = _disclosure_stage(repo, eid, chapter_seq)
    schedule = get_disclosure_schedule(repo, eid)
    if stage == 0:
        return ""
    if stage == 1:
        return schedule.foreshadow_hint
    ent = None
    for e in repo.list_entities():
        if e.entity_id == eid:
            ent = e
            break
    if ent:
        if ent.type == "object":
            check = getattr(repo, "item_exists", None)
            if check and not check(eid):
                return ""
        if ent.type == "character":
            public = _card_snippet(
                repo, eid, prose_safe=(prose_safe or chapter_seq is not None)
            )
        elif ent.type == "location":
            public = _location_snippet(
                repo, eid, prose_safe=(prose_safe or chapter_seq is not None)
            )
        elif ent.type == "object":
            public = _object_snippet(repo, eid)
        else:
            public = ""
        if public:
            return _assemble_with_disclosure(public, stage, schedule.secret_truth)
    # 可能是 faction_id（不在 entities 表）
    fac_fn = getattr(repo, "get_faction", None)
    if fac_fn:
        fac = fac_fn(eid)
        if fac:
            public = _faction_snippet(
                repo, eid, prose_safe=(prose_safe or chapter_seq is not None)
            )
            return _assemble_with_disclosure(public, stage, schedule.secret_truth)
    return ""


_REL_CN = {
    "member_of": "隶属于",
    "has_member": "成员包括",
    "controls": "控制",
    "controlled_by": "受控于",
    "allied": "结盟",
    "hostile": "敌对",
    "infiltrates": "渗透",
    "neutral": "中立",
    "tributary": "从属",
    "related_to": "相关",
    "located_in": "位于",
    "contains": "包含",
    "knows": "认识/知晓",
    "owns": "持有",
    "has_item": "有物品",
    "at_location": "位于",
    "transferred": "转移过",
}


def _edge_sentence(repo: Repository, edge) -> str:
    src = _display_name(repo, edge.src)
    dst = _display_name(repo, edge.dst)
    rel = _REL_CN.get(edge.rel, edge.rel)
    note = ""
    if isinstance(edge.meta, dict):
        note = str(edge.meta.get("note") or edge.meta.get("cause") or edge.meta.get("source_note") or "").strip()
    base = f"{src} --{rel}--> {dst}"
    if note:
        base += f"（{note[:120]}）"
    return base


def _relation_blocks(repo: Repository, seeds: set[str], expanded_ids: list[str],
                     limit: int = 18, chapter_seq: int | None = None,
                     allowed_entity_ids: set[str] | None = None) -> list[ContextBlock]:
    if not seeds:
        return []
    relevant = set(seeds) | set(expanded_ids)
    rows = []
    seen: set[tuple[str, str, str]] = set()
    direct_lines = _direct_relation_lines(repo, seeds, chapter_seq=chapter_seq)
    for sid in seeds:
        fn = getattr(repo, "attention_ranked_neighbors", None)
        if fn:
            rows.extend(fn(sid, limit=limit))
        for edge in getattr(repo, "list_edges", lambda **_: [])(src=sid):
            rows.append(edge)
        for edge in getattr(repo, "list_edges", lambda **_: [])(dst=sid):
            rows.append(edge)
    kept = []
    for edge in rows:
        key = (edge.src, edge.rel, edge.dst)
        if key in seen:
            continue
        seen.add(key)
        if edge.until_chapter is not None:
            continue
        if chapter_seq is not None and (
            _disclosure_stage(repo, edge.src, chapter_seq) < 2
            or _disclosure_stage(repo, edge.dst, chapter_seq) < 2
        ):
            continue
        if chapter_seq is not None and int(edge.since_chapter or 0) > chapter_seq:
            continue
        if allowed_entity_ids is not None and (
            edge.src not in allowed_entity_ids or edge.dst not in allowed_entity_ids
        ):
            continue
        if edge.src not in relevant and edge.dst not in relevant:
            continue
        kept.append(edge)
    kept.sort(key=lambda e: (-float(e.intensity or 0.0), e.rel, _display_name(repo, e.src)))
    lines = direct_lines + [_edge_sentence(repo, e) for e in kept[:limit]]
    lines = list(dict.fromkeys(line for line in lines if line.strip()))
    if not lines:
        return []
    return [ContextBlock("关系图谱", "\n".join(f"- {line}" for line in lines), priority=-1)]


def _direct_relation_lines(
    repo: Repository,
    seeds: set[str],
    chapter_seq: int | None = None,
) -> list[str]:
    lines: list[str] = []
    seed_set = set(seeds)
    ent_by_id = {e.entity_id: e for e in repo.list_entities()}

    for oid in seed_set:
        if chapter_seq is not None and _disclosure_stage(repo, oid, chapter_seq) < 2:
            continue
        ent = ent_by_id.get(oid)
        if not ent or ent.type != "object":
            continue
        item = getattr(repo, "get_inventory_item", lambda _id: None)(oid)
        if item and item.holder_agent_id and (
            chapter_seq is None
            or _disclosure_stage(repo, item.holder_agent_id, chapter_seq) >= 2
        ):
            lines.append(f"{_display_name(repo, item.holder_agent_id)} --持有--> {_display_name(repo, oid)}")
        loc_name = _object_location_holder(repo, oid)
        if loc_name:
            lines.append(f"{loc_name} --固有物--> {_display_name(repo, oid)}")

    for loc in getattr(repo, "list_locations", lambda: [])():
        if loc.loc_id not in seed_set:
            continue
        if chapter_seq is not None and _disclosure_stage(repo, loc.loc_id, chapter_seq) < 2:
            continue
        for oid in loc.notable_items or []:
            if oid in seed_set and (
                chapter_seq is None or _disclosure_stage(repo, oid, chapter_seq) >= 2
            ):
                lines.append(f"{loc.name} --固有物--> {_display_name(repo, oid)}")
        if loc.parent and (
            chapter_seq is None or _disclosure_stage(repo, loc.parent, chapter_seq) >= 2
        ):
            lines.append(f"{loc.name} --位于/从属--> {_display_name(repo, loc.parent)}")
        if loc.controlling_faction and (
            chapter_seq is None
            or _disclosure_stage(repo, loc.controlling_faction, chapter_seq) >= 2
        ):
            lines.append(f"{_display_name(repo, loc.controlling_faction)} --控制地点--> {loc.name}")

    for aid in seed_set:
        if chapter_seq is not None and _disclosure_stage(repo, aid, chapter_seq) < 2:
            continue
        ent = ent_by_id.get(aid)
        if not ent or ent.type != "character":
            continue
        fid = (ent.attributes or {}).get("faction_id")
        if fid and (chapter_seq is None or _disclosure_stage(repo, fid, chapter_seq) >= 2):
            lines.append(f"{_display_name(repo, aid)} --隶属于--> {_display_name(repo, fid)}")
        for item in getattr(repo, "items_held_by", lambda _id: [])(aid):
            if item.object_id in seed_set and (
                chapter_seq is None or _disclosure_stage(repo, item.object_id, chapter_seq) >= 2
            ):
                lines.append(f"{_display_name(repo, aid)} --持有--> {_display_name(repo, item.object_id)}")

    return list(dict.fromkeys(lines))


# ---------------------------------------------------------------------------
# 关键词触发：从文本中匹配实体名
# ---------------------------------------------------------------------------

def _keyword_scan(repo: Repository, text: str, exclude: set[str],
                  chapter_seq: int | None = None,
                  allowed_entity_ids: set[str] | None = None) -> list[str]:
    """扫描 text 中出现的实体/势力名，返回命中的 entity_id / faction_id。"""
    if not text:
        return []
    hits = []
    name_to_id: dict[str, str] = {}
    for e in repo.list_entities():
        if e.name and len(e.name) >= 2:
            name_to_id[e.name] = e.entity_id
    fac_fn = getattr(repo, "list_factions", None)
    if fac_fn:
        for f in fac_fn():
            if f.name and len(f.name) >= 2:
                name_to_id[f.name] = f.faction_id
    for name, eid in name_to_id.items():
        if allowed_entity_ids is not None and eid not in allowed_entity_ids:
            continue
        if _disclosure_stage(repo, eid, chapter_seq) < 2:
            continue
        if eid not in exclude and name in text:
            hits.append(eid)
    return hits


# ---------------------------------------------------------------------------
# 主入口：build_context
# ---------------------------------------------------------------------------

def build_context(repo: Repository,
                  seeds: set[str] | None = None,
                  budget: int = DEFAULT_BUDGET,
                  beat_text: str = "",
                  include_bible_summary: bool = True,
                  hops: int = 1,
                  chapter_seq: int | None = None,
                  allowed_fact_ids: set[str] | None = None,
                  exclude_future: bool = False,
                  allowed_entity_ids: set[str] | None = None,
                  prose_safe: bool | None = None,
                  include_faction_overview: bool | None = None) -> str:
    """W6 RAG 注入主函数。

    Args:
        repo: 仓储
        seeds: 本场直接相关的实体 id（cast + location + faction）
        budget: 最大注入字符数
        beat_text: 本拍/本章节拍文本（用于关键词触发）
        include_bible_summary: 是否注入世界观 summary 速览
        hops: 图谱扩展跳数（1 或 2）
        chapter_seq: 当前章序；给定后按 available_from_chapter 过滤未来实体
        allowed_fact_ids: 本章可揭示的 fact 白名单
        exclude_future: 排除 story_time 晚于本章的 fact
        allowed_entity_ids: **硬白名单**。给定时（写作严格态）关键词/图谱扩展只在白名单内；
            为 None 时（规划发现态）只按章节可见性过滤、保留图谱扩展。
        prose_safe: 脱敏开关。True 时剥离人物小传/弧线、势力目标/手段/detail 等"答案层"。
            默认：进入任何章节作用域（chapter_seq/exclude_future/allowed_entity_ids 任一非默认）即开。
        include_faction_overview: 是否注入"势力速览"全量总览。默认：仅在无硬白名单时注入
            （规划需要全局势力感知；写作严格态默认关闭，避免带出未来反派名）。

    Returns:
        拼好的上下文字符串

    设计：把"脱敏 / 实体白名单 / 势力总览"三件事**解耦**——旧实现用单个
    `permissioned` 同时决定三者，导致规划态一旦脱敏就被迫上硬白名单、清空图谱扩展。
    """
    seeds = set(seeds or set())
    scoped = chapter_seq is not None or exclude_future or allowed_entity_ids is not None
    if prose_safe is None:
        prose_safe = scoped
    if include_faction_overview is None:
        include_faction_overview = allowed_entity_ids is None
    seeds = {
        eid for eid in seeds
        if _available_in_chapter(repo, eid, chapter_seq)
        and (allowed_entity_ids is None or eid in allowed_entity_ids)
    }
    blocks: list[ContextBlock] = []

    # ── 常驻层 P0：世界观各节 summary ──
    if include_bible_summary:
        ov_fn = getattr(repo, "bible_summaries_text", None)
        if ov_fn:
            try:
                ov = ov_fn(["settingCore", "rules", "geography", "culture"])
            except Exception:
                ov = ""
            if ov:
                blocks.append(ContextBlock("世界观速览", _safe_world_text(ov), priority=0))

    # ── 常驻层 P0.5：核心世界圣经 detail（保底，pre-W1 项目也能注入） ──
    if include_bible_summary:
        sec_fn = getattr(repo, "bible_sections_text", None)
        if sec_fn:
            try:
                core_detail = sec_fn(["settingCore", "rules"], max_chars=800)
            except Exception:
                core_detail = ""
            if core_detail:
                blocks.append(ContextBlock("核心设定", _safe_world_text(core_detail), priority=0))

    # ── 常驻层 P1：势力 summary ──
    fac_fn = getattr(repo, "faction_summaries_text", None)
    if fac_fn and include_faction_overview:
        try:
            if chapter_seq is None:
                fac_ov = fac_fn()
            else:
                fac_ov = "\n".join(
                    snippet
                    for faction in getattr(repo, "list_factions", lambda: [])()
                    if (
                        snippet := _entity_snippet(
                            repo,
                            faction.faction_id,
                            prose_safe=True,
                            chapter_seq=chapter_seq,
                        )
                    )
                )
        except Exception:
            fac_ov = ""
        if fac_ov:
            blocks.append(ContextBlock("势力速览", fac_ov, priority=1))

    # ── 种子层 P2：直接种子实体 detail ──
    for sid in seeds:
        snippet = _entity_snippet(
            repo, sid, prose_safe=prose_safe, chapter_seq=chapter_seq
        )
        if snippet:
            blocks.append(ContextBlock("种子", snippet, priority=2, entity_id=sid))

    # ── 关键词层 P3：beat 文本命中的非种子实体 ──
    kw_hits = _keyword_scan(
        repo,
        beat_text,
        seeds,
        chapter_seq=chapter_seq,
        allowed_entity_ids=allowed_entity_ids,
    )
    for eid in kw_hits:
        snippet = _entity_snippet(
            repo, eid, prose_safe=prose_safe, chapter_seq=chapter_seq
        )
        if snippet:
            blocks.append(ContextBlock("关键词命中", snippet, priority=3, entity_id=eid))

    # ── 图谱层 P4：从种子扩展的邻居 ──
    already = seeds | set(kw_hits)
    expanded = [
        (eid, intensity)
        for eid, intensity in _expand_seeds(repo, seeds, hops=hops)
        if _available_in_chapter(repo, eid, chapter_seq)
        and (allowed_entity_ids is None or eid in allowed_entity_ids)
    ]
    expanded_ids = [eid for eid, _intensity in expanded]
    blocks.extend(_relation_blocks(
        repo,
        seeds,
        expanded_ids,
        chapter_seq=chapter_seq,
        allowed_entity_ids=allowed_entity_ids,
    ))
    for eid, intensity in expanded:
        if eid in already:
            continue
        snippet = _entity_snippet(
            repo, eid, prose_safe=prose_safe, chapter_seq=chapter_seq
        )
        if snippet:
            blocks.append(ContextBlock(
                f"图谱({intensity:.2f})", snippet,
                priority=4, entity_id=eid,
            ))

    # Canonical facts are opt-in.  Existing or old facts are still hidden when
    # the current chapter's reveal gate does not authorize them.
    if allowed_fact_ids:
        for fact in repo.list_facts():
            if fact.fact_id not in allowed_fact_ids:
                continue
            if exclude_future and chapter_seq is not None and int(fact.story_time or 0) > chapter_seq:
                continue
            blocks.append(ContextBlock(
                "本章可揭示事实",
                fact.canonical_content,
                priority=1,
                entity_id=f"fact:{fact.fact_id}",
            ))

    # ── 按优先级排序 + budget 截断 ──
    blocks.sort(key=lambda b: (b.priority, -len(b.text)))
    result_parts: list[str] = []
    used = 0
    seen_eids: set[str] = set()
    for b in blocks:
        if b.entity_id and b.entity_id in seen_eids:
            continue
        line = f"〔{b.label}〕\n{b.text}" if b.label else b.text
        cost = _char_len(line)
        if used + cost > budget:
            remaining = budget - used
            if remaining > 80:
                line = line[:remaining]
            else:
                continue
        result_parts.append(line)
        used += _char_len(line)
        if b.entity_id:
            seen_eids.add(b.entity_id)
        if used >= budget:
            break

    return "\n\n".join(result_parts)


# ---------------------------------------------------------------------------
# 便捷方法：为 SceneWriter 构造种子集
# ---------------------------------------------------------------------------

def scene_seeds(chapter, pov: str = "") -> set[str]:
    """从 ChapterPlan 提取本场种子 id 集合。"""
    s: set[str] = set()
    if pov:
        s.add(pov)
    for a in getattr(chapter, "cast", None) or []:
        s.add(a)
    for loc in getattr(chapter, "location_ids", None) or []:
        s.add(loc)
    for item in getattr(chapter, "items_present", None) or []:
        s.add(item)
    for item in getattr(chapter, "available_items", None) or []:
        s.add(item)
    for entity_id in getattr(chapter, "allowed_entity_ids", None) or []:
        s.add(entity_id)
    return s


def chapter_seeds(chapter) -> set[str]:
    """从 ChapterPlan 提取本章种子（含 cast + locations）。"""
    return scene_seeds(chapter)
