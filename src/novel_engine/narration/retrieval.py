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

def _card_snippet(repo: Repository, agent_id: str) -> str:
    """人物卡摘要：名字 + one_liner + 三维度精简。"""
    fn = getattr(repo, "get_card_for_agent", None)
    card = fn(agent_id) if fn else None
    if not card:
        return ""
    parts = [f"【{card.name}】{card.one_liner or ''}"]
    if card.tier == "lead":
        if card.appearance:
            parts.append(f"外貌：{card.appearance[:120]}")
        if card.social_role:
            parts.append(f"社会：{card.social_role[:120]}")
        if card.psychology:
            parts.append(f"心理：{card.psychology[:120]}")
        if card.backstory:
            parts.append(f"小传：{card.backstory[:200]}")
        if card.arc:
            parts.append(f"弧线：{card.arc[:100]}")
    else:
        if card.defining_trait:
            parts.append(f"特质：{card.defining_trait}")
        if card.backstory:
            parts.append(f"小传：{card.backstory[:100]}")
    if card.voice_register:
        parts.append(f"语域：{card.voice_register}")
    if card.verbal_habits:
        parts.append(f"口头禅：{card.verbal_habits}")
    return "\n".join(parts)


def _location_snippet(repo: Repository, loc_id: str) -> str:
    fn = getattr(repo, "get_location", None)
    loc = fn(loc_id) if fn else None
    if not loc:
        return ""
    parts = [f"【{loc.name}】"]
    if loc.detail:
        parts.append(loc.detail[:300])
    elif loc.geo_full:
        parts.append(loc.geo_full[:300])
    if loc.culture_local:
        parts.append(f"风土：{loc.culture_local[:150]}")
    return "\n".join(parts)


def _faction_snippet(repo: Repository, fac_id: str) -> str:
    fn = getattr(repo, "get_faction", None)
    fac = fn(fac_id) if fn else None
    if not fac:
        return ""
    parts = [f"【{fac.name}】{fac.summary or ''}"]
    if fac.ideology:
        parts.append(f"信条：{fac.ideology[:100]}")
    if fac.goals:
        parts.append(f"目标：{fac.goals[:100]}")
    if fac.methods:
        parts.append(f"手段：{fac.methods[:100]}")
    if fac.detail:
        parts.append(fac.detail[:200])
    return "\n".join(parts)


def _entity_snippet(repo: Repository, eid: str) -> str:
    """根据实体类型返回对应的 detail 片段。已销毁/献祭的物品不注入。"""
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
            return _card_snippet(repo, eid)
        elif ent.type == "location":
            return _location_snippet(repo, eid)
    # 可能是 faction_id（不在 entities 表）
    fac_fn = getattr(repo, "get_faction", None)
    if fac_fn:
        fac = fac_fn(eid)
        if fac:
            return _faction_snippet(repo, eid)
    return ""


# ---------------------------------------------------------------------------
# 关键词触发：从文本中匹配实体名
# ---------------------------------------------------------------------------

def _keyword_scan(repo: Repository, text: str, exclude: set[str]) -> list[str]:
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
                  hops: int = 1) -> str:
    """W6 RAG 注入主函数。

    Args:
        repo: 仓储
        seeds: 本场直接相关的实体 id（cast + location + faction）
        budget: 最大注入字符数
        beat_text: 本拍/本章节拍文本（用于关键词触发）
        include_bible_summary: 是否注入世界观 summary 速览
        hops: 图谱扩展跳数（1 或 2）

    Returns:
        拼好的上下文字符串
    """
    seeds = seeds or set()
    blocks: list[ContextBlock] = []

    # ── 常驻层 P0：世界观各节 summary ──
    if include_bible_summary:
        ov_fn = getattr(repo, "bible_summaries_text", None)
        if ov_fn:
            try:
                ov = ov_fn()
            except Exception:
                ov = ""
            if ov:
                blocks.append(ContextBlock("世界观速览", ov, priority=0))

    # ── 常驻层 P0.5：核心世界圣经 detail（保底，pre-W1 项目也能注入） ──
    if include_bible_summary:
        sec_fn = getattr(repo, "bible_sections_text", None)
        if sec_fn:
            try:
                core_detail = sec_fn(["settingCore", "rules"], max_chars=800)
            except Exception:
                core_detail = ""
            if core_detail:
                blocks.append(ContextBlock("核心设定", core_detail, priority=0))

    # ── 常驻层 P1：势力 summary ──
    fac_fn = getattr(repo, "faction_summaries_text", None)
    if fac_fn:
        try:
            fac_ov = fac_fn()
        except Exception:
            fac_ov = ""
        if fac_ov:
            blocks.append(ContextBlock("势力速览", fac_ov, priority=1))

    # ── 种子层 P2：直接种子实体 detail ──
    for sid in seeds:
        snippet = _entity_snippet(repo, sid)
        if snippet:
            blocks.append(ContextBlock("种子", snippet, priority=2, entity_id=sid))

    # ── 关键词层 P3：beat 文本命中的非种子实体 ──
    kw_hits = _keyword_scan(repo, beat_text, seeds)
    for eid in kw_hits:
        snippet = _entity_snippet(repo, eid)
        if snippet:
            blocks.append(ContextBlock("关键词命中", snippet, priority=3, entity_id=eid))

    # ── 图谱层 P4：从种子扩展的邻居 ──
    already = seeds | set(kw_hits)
    expanded = _expand_seeds(repo, seeds, hops=hops)
    for eid, intensity in expanded:
        if eid in already:
            continue
        snippet = _entity_snippet(repo, eid)
        if snippet:
            blocks.append(ContextBlock(
                f"图谱({intensity:.2f})", snippet,
                priority=4, entity_id=eid,
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
    return s


def chapter_seeds(chapter) -> set[str]:
    """从 ChapterPlan 提取本章种子（含 cast + locations）。"""
    return scene_seeds(chapter)
