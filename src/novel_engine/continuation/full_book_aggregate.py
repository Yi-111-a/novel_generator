# -*- coding: utf-8 -*-
"""C2 · 全书聚合（完全蒸馏的第二层）。

吃 C1 落库的逐章产物（events / snapshots / codex / foreshadow，都是结构化低 token 数据），
聚合成：
  C2.x 材料化写作实体  -> entities / personas / character_cards / locations / factions / threads
  C2.a 全书剧情主线    -> story_arcs
  C2.b 人物完整弧线    -> character_cards.arc / personas
  C2.c 书末状态        -> story_bible_v2.last_state_json
  C2.d 时间线          -> story_bible_v2.timeline_json
  C2.e 知识图谱精炼    -> graph_edges
  C2.f 伏笔配对        -> foreshadow_setups.status

prompt 全部通用：只说"原作/角色/事件"，不含作品/作者专名。
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from typing import Any

from ..entity_matching import best_existing_name, semantically_equivalent_names
from ..llm.base import LLMClient
from ..models import CharacterCard, Entity, Faction, GraphEdge, InventoryItem, Location, Persona, Thread
from ..repository import Repository

AGG_TEMPERATURE = 0.3


def _parse_json(raw: str) -> Any:
    if not raw:
        return None
    s = raw.strip().strip("`")
    if s.lower().startswith("json"):
        s = s[4:].strip()
    for open_c, close_c in (("{", "}"), ("[", "]")):
        i, j = s.find(open_c), s.rfind(close_c)
        if 0 <= i < j:
            try:
                return json.loads(s[i:j + 1])
            except Exception:
                continue
    try:
        return json.loads(s)
    except Exception:
        return None


def _sid(prefix: str, *parts: Any) -> str:
    h = hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:8]
    return f"{prefix}_{h}"


def _upsert_entity(repo: Repository, entity: Entity) -> None:
    """Idempotent entity write for C2 reruns."""
    if repo.entity_exists(entity.entity_id):
        repo.update_entity_attributes(entity.entity_id, entity.attributes)
        return
    repo.insert_entity(entity)


def _compact_text(value: Any, limit: int = 160) -> str:
    text = str(value or "").strip()
    return text[:limit]


def _snapshot_line(s: dict[str, Any]) -> str:
    snap = s.get("snapshot") or {}
    return (
        f"[第{s['chapter_no']}章] 地点={snap.get('location','')} "
        f"情绪={snap.get('emotional_state','')} 目标={snap.get('goal_now','')} "
        f"新知={'/'.join((snap.get('knows_new') or [])[:3])} "
        f"得={'/'.join((snap.get('gained') or [])[:2])} 失={'/'.join((snap.get('lost') or [])[:2])}"
    )


_FACTION_HINT_RE = re.compile(
    r"(学院|学校|家族|组织|协会|会社|社团|委员会|议会|政府|机关|部门|公司|集团|联盟|"
    r"军|警|队|局|会|社|党|派|门|帮|族|国|宫|廷|院|所|基金会)"
)


def _looks_like_faction(name: str) -> bool:
    n = (name or "").strip()
    if not n or n.isascii() or len(n) > 18:
        return False
    return bool(_FACTION_HINT_RE.search(n))


def _relationship_text(card: CharacterCard) -> str:
    rel = "；".join(f"{k}:{v}" for k, v in (card.relationship_map or {}).items())
    return "；".join(x for x in (
        card.one_liner, card.key_relation, card.backstory, card.social_role, card.psychology, rel
    ) if x)


# ============ C2.x 材料化角色 + C2.b 人物弧线 ============
_CHAR_ARC_SYS = (
    "你在为长篇小说续写系统蒸馏一个角色的完整弧线。下面给你这个角色在原作每一章末尾的状态序列"
    "（按章号），以及它参与的关键事件。请综合推导该角色的完整画像。绝不杜撰序列里没有的信息。\n"
    "只输出 JSON：{"
    "\"role_tier\":\"lead|supporting|minor\",\"want\":\"贯穿全书的核心欲望\","
    "\"fatal_flaw\":\"致命弱点\",\"voice\":\"说话风格\",\"one_liner\":\"一句话人设\","
    "\"backstory_revealed\":\"原作中揭示出的前史\",\"arc_start\":\"初始状态\",\"arc_end\":\"原作末尾状态\","
    "\"growth_axis\":\"变化最大的轴\",\"unresolved\":\"截至原作末尾仍悬而未决的方面\","
    "\"key_relationships\":[{\"with\":\"对象名\",\"kind\":\"关系\",\"evolution\":\"如何演变\"}]}"
)


def aggregate_characters(repo: Repository, llm: LLMClient, *, min_chapters: int = 1) -> dict[str, Any]:
    snaps = repo.list_character_snapshots()
    if not snaps:
        return {"characters": 0}
    by_char: dict[str, list[dict]] = defaultdict(list)
    for s in snaps:
        by_char[s["character_name"]].append(s)
    # 出场章数排序，取主要角色
    events = repo.list_source_events()
    name_to_events: dict[str, list[str]] = defaultdict(list)
    for e in events:
        for p in e["participants"]:
            name_to_events[p].append(f"第{e['chapter_no']}章: {e['summary']}")

    created = 0
    skipped_llm = 0
    name_to_aid: dict[str, str] = {}
    ranked = sorted(by_char.items(), key=lambda kv: -len(kv[1]))
    for name, items in ranked:
        chapters_present = len({s["chapter_no"] for s in items})
        ordered_items = sorted(items, key=lambda x: x["chapter_no"])
        seq_blob = "\n".join(_snapshot_line(s) for s in ordered_items)
        ev_blob = "\n".join(name_to_events.get(name, [])[:20])
        data = {}
        if chapters_present < max(2, min_chapters):
            skipped_llm += 1
            first = ordered_items[0].get("snapshot") or {}
            last = ordered_items[-1].get("snapshot") or {}
            rels: dict[str, str] = {}
            for snap_row in ordered_items:
                for rel in (snap_row.get("snapshot") or {}).get("relationship_changes") or []:
                    if isinstance(rel, dict) and str(rel.get("with", "")).strip():
                        rels[str(rel["with"]).strip()] = str(rel.get("delta", "")).strip()
            data = {
                "role_tier": "minor",
                "want": _compact_text(last.get("goal_now") or first.get("goal_now"), 80),
                "fatal_flaw": "",
                "voice": "",
                "one_liner": f"{name}：{_compact_text(last.get('physical_state') or last.get('emotional_state') or '短暂出场角色', 80)}",
                "backstory_revealed": "",
                "arc_start": _compact_text(first.get("physical_state") or first.get("emotional_state"), 100),
                "arc_end": _compact_text(last.get("physical_state") or last.get("emotional_state"), 100),
                "growth_axis": "",
                "unresolved": _compact_text(last.get("goal_now"), 100),
                "key_relationships": [{"with": k, "kind": "related", "evolution": v} for k, v in rels.items()],
            }
        else:
            try:
                data = _parse_json(llm.complete_at(
                    _CHAR_ARC_SYS,
                    f"角色名：{name}\n出场章数：{chapters_present}\n\n[每章末状态序列]\n{seq_blob}\n\n"
                    f"[参与的关键事件]\n{ev_blob}\n\n只输出 JSON。",
                    AGG_TEMPERATURE,
                )) or {}
            except Exception:
                data = {}
        tier = str(data.get("role_tier", "")).strip()
        if tier not in ("lead", "supporting", "minor"):
            tier = "lead" if chapters_present >= max(3, len(ranked) and 3) and created == 0 else (
                "supporting" if chapters_present >= 2 else "minor")
        aid = _sid("char", name)
        name_to_aid[name] = aid
        _upsert_entity(repo, Entity(aid, "character", name, {"source": "full_distill", "tier": tier}))
        repo.insert_persona(Persona(
            agent_id=aid, name=name,
            want=str(data.get("want", "")).strip(),
            fatal_flaw=str(data.get("fatal_flaw", "")).strip(),
            voice=str(data.get("voice", "")).strip(),
            arc_state={"arc_start": str(data.get("arc_start", "")).strip(),
                       "arc_end": str(data.get("arc_end", "")).strip(),
                       "unresolved": str(data.get("unresolved", "")).strip()},
        ))
        rel_map: dict[str, Any] = {}
        for r in (data.get("key_relationships") or []):
            if isinstance(r, dict) and str(r.get("with", "")).strip():
                rel_map[str(r["with"]).strip()] = f"{r.get('kind','')}：{r.get('evolution','')}".strip("：")
        repo.add_card(CharacterCard(
            card_id=_sid("card", name), agent_id=aid, tier=tier,
            slot_key=f"distill:{name}", name=name,
            one_liner=str(data.get("one_liner", "")).strip(),
            defining_trait=str(data.get("growth_axis", "")).strip(),
            core_desire=str(data.get("want", "")).strip(),
            fatal_flaw=str(data.get("fatal_flaw", "")).strip(),
            voice_register=str(data.get("voice", "")).strip(),
            key_relation="；".join(list(rel_map.keys())[:3]),
            relationship_map=rel_map,
            backstory=str(data.get("backstory_revealed", "")).strip(),
            arc=f"{data.get('arc_start','')} → {data.get('arc_end','')}".strip(" →"),
        ))
        created += 1
    return {"characters": created, "name_to_aid": name_to_aid, "skipped_minor_llm": skipped_llm}


# ============ C2.x 材料化地点 ============
_LOC_SYS = (
    "你在为长篇小说续写系统蒸馏地点。下面给你原作出现过的地点名清单，以及各地发生过的事件样本。"
    "为每个地点给一句定位 summary 与一段 detail（环境/功能/声光气味）。绝不杜撰冲突设定。\n"
    "只输出 JSON：{\"locations\":[{\"name\":\"\",\"summary\":\"\",\"detail\":\"\"}]}"
)


def aggregate_locations(repo: Repository, llm: LLMClient) -> dict[str, Any]:
    events = repo.list_source_events()
    codex = repo.list_codex()
    loc_counter: Counter = Counter()
    for e in events:
        if e["location"].strip():
            loc_counter[e["location"].strip()] += 1
    for c in codex:
        if c["kind"] == "location_detail" and c["name"].strip():
            loc_counter[c["name"].strip()] += 1
    names = [n for n, _ in loc_counter.most_common(14) if not n.isascii()]
    if not names:
        return {"locations": 0}
    loc_events: dict[str, list[str]] = defaultdict(list)
    for e in events:
        if e["location"].strip() in names:
            loc_events[e["location"].strip()].append(e["summary"])
    blob = "\n".join(f"- {n}（出现{loc_counter[n]}次）：{'; '.join(loc_events.get(n, [])[:3])}" for n in names)
    data = {}
    try:
        data = _parse_json(llm.complete_at(
            _LOC_SYS, f"[地点清单与事件样本]\n{blob}\n\n只输出 JSON。", AGG_TEMPERATURE)) or {}
    except Exception:
        data = {}
    summaries = {str(d.get("name", "")).strip(): d for d in (data.get("locations") or []) if isinstance(d, dict)}
    name_to_locid: dict[str, str] = {}
    created = 0
    for name in names:
        lid = _sid("loc", name)
        name_to_locid[name] = lid
        d = summaries.get(name, {})
        _upsert_entity(repo, Entity(lid, "location", name, {"canon": True, "source": "full_distill"}))
        repo.upsert_location(Location(
            loc_id=lid, part_id="", name=name,
            geo_full=str(d.get("detail", "")).strip(),
            summary=str(d.get("summary", "")).strip(),
            detail=str(d.get("detail", "")).strip(),
        ))
        created += 1
    return {"locations": created, "name_to_locid": name_to_locid}


# ============ C2.x 材料化道具/能力物件 ============
def materialize_items(repo: Repository) -> dict[str, Any]:
    codex = repo.list_codex()
    item_rows = [
        c for c in codex
        if c["kind"] in ("item", "magic_system") and str(c.get("name", "")).strip()
    ]
    name_to_oid: dict[str, str] = {}
    existing_rows = [
        (entity.entity_id, entity.name)
        for entity in repo.list_entities()
        if entity.type == "object" and not (entity.attributes or {}).get("merged_into")
    ]
    created = 0
    # Specific names win over generic variants from the same source chapter.
    canonical_rows: list[dict[str, Any]] = []
    for row in sorted(item_rows, key=lambda item: -len(str(item.get("name", "")).strip())):
        name = str(row.get("name", "")).strip()
        if any(
            semantically_equivalent_names(name, str(existing.get("name", "")).strip())
            for existing in canonical_rows
        ):
            continue
        canonical_rows.append(row)
    for c in canonical_rows:
        name = str(c["name"]).strip()
        oid = best_existing_name(name, existing_rows) or _sid("obj", name)
        name_to_oid[name] = oid
        attrs = {
            "source": "settings_codex",
            "codex_id": c.get("codex_id", ""),
            "kind": c.get("kind", "item"),
            "type": c.get("type", ""),
            "summary": c.get("summary", ""),
            "evidence_chapter": c.get("evidence_chapter", 0),
            "evidence_excerpt": c.get("evidence_excerpt", ""),
        }
        _upsert_entity(repo, Entity(oid, "object", name, attrs))
        previous_name = next((existing for entity_id, existing in existing_rows if entity_id == oid), "")
        if previous_name and len(name) > len(previous_name):
            repo.update_entity_name(oid, name)
            existing_rows = [
                (entity_id, name if entity_id == oid else existing)
                for entity_id, existing in existing_rows
            ]
        if not any(row[0] == oid for row in existing_rows):
            existing_rows.append((oid, name))
        if repo.get_inventory_item(oid) is None:
            repo.set_inventory(InventoryItem(
                object_id=oid, holder_agent_id=None, status="held",
                acquired_chapter=int(c.get("first_appeared", 0) or 0),
                note=str(c.get("summary", "")).strip()[:160],
            ))
        created += 1
    return {"items": created, "name_to_oid": name_to_oid}


# ============ C2.x 材料化势力 ============
_FAC_SYS = (
    "你在为长篇小说续写系统蒸馏势力/组织。下面给你原作出现过的组织相关设定、事件参与者候选、人物关系卡线索。"
    "归纳出主要势力，每个给 summary/ideology/goals，并列出两两关系。绝不杜撰冲突设定。\n"
    "只输出 JSON：{\"factions\":[{\"name\":\"\",\"summary\":\"\",\"ideology\":\"\",\"goals\":\"\"}],"
    "\"relations\":[{\"src\":\"势力名\",\"dst\":\"势力名\",\"kind\":\"allied|hostile|infiltrates|neutral|tributary\",\"intensity\":3}]}"
)


def aggregate_factions(repo: Repository, llm: LLMClient) -> dict[str, Any]:
    codex = repo.list_codex()
    org_items = [c for c in codex if c["kind"] == "organization" or "机构" in c["type"] or "势力" in c["type"]]
    persona_names = {p.name for p in repo.list_personas()}
    card_lines = []
    event_candidates: Counter = Counter()
    for e in repo.list_source_events():
        for p in e.get("participants", []) or []:
            name = str(p).strip()
            if name and name not in persona_names and _looks_like_faction(name):
                event_candidates[name] += 1
    for card in repo.list_cards():
        text = _relationship_text(card)
        if text and re.search(r"(隶属|效忠|成员|来自|学院|家族|组织|势力|机构|阵营)", text):
            card_lines.append(f"- {card.name}：{text[:220]}")
    if not org_items and not event_candidates and not card_lines:
        return {"factions": 0}
    codex_blob = "\n".join(f"- {c['name']}：{c['summary']}" for c in org_items[:30])
    event_blob = "\n".join(f"- {n}：事件参与者出现{c}次" for n, c in event_candidates.most_common(20))
    cards_blob = "\n".join(card_lines[:30])
    blob = (
        f"[组织相关设定]\n{codex_blob or '（无）'}\n\n"
        f"[事件参与者中的组织候选]\n{event_blob or '（无）'}\n\n"
        f"[人物卡关系/隶属线索]\n{cards_blob or '（无）'}"
    )
    data = {}
    try:
        data = _parse_json(llm.complete_at(
            _FAC_SYS, f"{blob}\n\n只输出 JSON。", AGG_TEMPERATURE)) or {}
    except Exception:
        data = {}
    name_to_fid: dict[str, str] = {}
    created = 0
    for f in (data.get("factions") or []):
        if not isinstance(f, dict) or not str(f.get("name", "")).strip():
            continue
        name = str(f["name"]).strip()
        fid = _sid("fac", name)
        name_to_fid[name] = fid
    for f in (data.get("factions") or []):
        if not isinstance(f, dict) or not str(f.get("name", "")).strip():
            continue
        name = str(f["name"]).strip()
        fid = name_to_fid[name]
        relations = []
        for r in (data.get("relations") or []):
            if isinstance(r, dict) and str(r.get("src", "")).strip() == name:
                tgt = name_to_fid.get(str(r.get("dst", "")).strip())
                if tgt and tgt != fid:
                    relations.append({"target_faction_id": tgt, "kind": str(r.get("kind", "neutral")).strip(),
                                      "intensity": max(1, min(5, int(r.get("intensity", 3) or 3))), "note": ""})
        repo.upsert_faction(Faction(
            faction_id=fid, name=name, ideology=str(f.get("ideology", "")).strip(),
            goals=str(f.get("goals", "")).strip(), methods="", territory=[], structure="",
            key_members=[], history="", relations=relations, secret="",
            summary=str(f.get("summary", "")).strip(), detail="", source="full_distill",
        ))
        created += 1
    # 回填角色隶属，供 C3 冲突/势力压力和图谱使用。
    for card in repo.list_cards():
        text = _relationship_text(card)
        if not text:
            continue
        for fname, fid in name_to_fid.items():
            if fname and fname in text and card.agent_id:
                repo.update_entity_attributes(card.agent_id, {"faction_id": fid})
                repo.upsert_edge(GraphEdge(
                    src=card.agent_id, rel="member_of", dst=fid,
                    meta={"source": "faction_card_signal", "note": f"{card.name}关系卡提及{fname}"},
                    intensity=0.7,
                ))
                break
    return {"factions": created, "name_to_fid": name_to_fid}


# ============ C2.a 剧情主线 ============
_ARC_SYS = (
    "你在为长篇小说续写系统聚合剧情主线。下面是全书事件清单或压缩后的里程碑清单（按章号）。"
    "把它们归纳成 2-5 条主线，每条给名称、主题、关键事件（按时序的原始事件序号）、转折点、主角历程、解决状态。\n"
    "只输出 JSON：{\"arcs\":[{\"name\":\"\",\"theme\":\"\",\"key_event_seq\":[全局事件序号],"
    "\"turning_point_seq\":[序号],\"journey\":\"主角在这条线的历程\","
    "\"resolution\":\"resolved|partial|open\"}]}"
)

_MILESTONE_SYS = (
    "你在为长篇小说续写系统压缩事件清单。下面是一段连续章节的客观事件列表，每条带全书原始事件序号。"
    "请筛出真正改变人物处境、世界规则、关系、秘密揭示、损失/获得、门槛跨越的里程碑事件；"
    "合并重复小事件，但必须保留原始事件序号 original_seq。绝不杜撰。\n"
    "只输出 JSON：{\"milestones\":[{\"original_seq\":1,\"summary\":\"压缩后事件\",\"kind\":\"reveal|loss|gain|decision|conflict|threshold|other\"}]}"
)


def _fallback_milestones(indexed: list[tuple[int, dict[str, Any]]], target: int = 80) -> list[tuple[int, dict[str, Any]]]:
    important = {"reveal", "loss", "gain", "decision", "conflict", "threshold"}
    selected: dict[int, dict[str, Any]] = {}
    for i, e in indexed:
        if e.get("kind") in important:
            selected[i] = e
    if len(selected) < target:
        step = max(1, len(indexed) // max(1, target))
        for i, e in indexed[::step]:
            selected.setdefault(i, e)
            if len(selected) >= target:
                break
    return sorted(selected.items())[:target]


def _compress_events_to_milestones(
    indexed: list[tuple[int, dict[str, Any]]],
    llm: LLMClient,
    *,
    chunk_size: int = 110,
    target: int = 80,
) -> list[tuple[int, dict[str, Any]]]:
    full_blob = "\n".join(f"{i}. [第{e['chapter_no']}章/{e['kind']}] {e['summary']}（{e.get('effects','')}）"
                          for i, e in indexed)
    if len(full_blob) <= 14000:
        return indexed
    milestones: dict[int, dict[str, Any]] = {}
    for start in range(0, len(indexed), chunk_size):
        chunk = indexed[start:start + chunk_size]
        blob = "\n".join(
            f"{i}. [第{e['chapter_no']}章/{e['kind']}] {e['summary']}（{e.get('effects','')}）"
            for i, e in chunk
        )
        try:
            data = _parse_json(llm.complete_at(_MILESTONE_SYS, f"[事件清单]\n{blob}\n\n只输出 JSON。", AGG_TEMPERATURE)) or {}
        except Exception:
            data = {}
        for m in (data.get("milestones") or []):
            if not isinstance(m, dict):
                continue
            try:
                seq = int(m.get("original_seq"))
            except Exception:
                continue
            original = dict(indexed[seq - 1][1]) if 1 <= seq <= len(indexed) else None
            if not original:
                continue
            summary = str(m.get("summary", "")).strip()
            if summary:
                original["summary"] = summary
            if str(m.get("kind", "")).strip():
                original["kind"] = str(m.get("kind", "")).strip()
            milestones[seq] = original
    if not milestones:
        return _fallback_milestones(indexed, target=target)
    if len(milestones) > target:
        keep = _fallback_milestones(sorted(milestones.items()), target=target)
        return keep
    return sorted(milestones.items())


def aggregate_story_arcs(repo: Repository, llm: LLMClient) -> dict[str, Any]:
    events = repo.list_source_events()
    if not events:
        return {"arcs": 0}
    indexed = list(enumerate(events, 1))
    milestones = _compress_events_to_milestones(indexed, llm)
    blob = "\n".join(
        f"{i}. [第{e['chapter_no']}章/{e['kind']}] {e['summary']}"
        for i, e in milestones
    )
    data = {}
    try:
        data = _parse_json(llm.complete_at(
            _ARC_SYS, f"[全书事件清单]\n{blob}\n\n只输出 JSON。", AGG_TEMPERATURE)) or {}
    except Exception:
        data = {}
    idx_to_eid = {i: e["event_id"] for i, e in indexed}
    created = 0
    for a in (data.get("arcs") or []):
        if not isinstance(a, dict) or not str(a.get("name", "")).strip():
            continue
        key_events = [idx_to_eid[i] for i in (a.get("key_event_seq") or []) if i in idx_to_eid]
        turning = [idx_to_eid[i] for i in (a.get("turning_point_seq") or []) if i in idx_to_eid]
        repo.upsert_story_arc(
            arc_id=_sid("sarc", a["name"]), name=str(a["name"]).strip(), theme=str(a.get("theme", "")).strip(),
            key_events=key_events, turning_points=turning,
            journey_summary=str(a.get("journey", "")).strip(),
            resolution_status=str(a.get("resolution", "open")).strip() or "open",
        )
        created += 1
    return {"arcs": created}


# ============ C2.c 书末状态 ============
_LAST_STATE_SYS = (
    "你在为长篇小说续写系统蒸馏'书末状态'。下面给你原作最后五章的事件与人物状态，"
    "以及全书中后段未再出现的人物 absent 索引。给出原作结束时的世界与人物状态，这是续写的起点。绝不杜撰。\n"
    "只输出 JSON：{\"protagonist\":{\"name\":\"\",\"location\":\"\",\"emotional_state\":\"\",\"active_goal\":\"\"},"
    "\"cast_alive\":[{\"name\":\"\",\"location\":\"\",\"status\":\"\"}],"
    "\"cast_dead_or_lost\":[{\"name\":\"\",\"fate\":\"\"}],"
    "\"world_changes\":[\"相比开篇世界发生的变化\"],"
    "\"active_conflicts\":[{\"between\":\"\",\"stakes\":\"\"}],"
    "\"hooks_for_next_book\":[\"可作为续写起点的钩子\"],\"ending_state\":\"一句话概括书末局面\"}"
)


def aggregate_last_state(repo: Repository, llm: LLMClient) -> dict[str, Any]:
    chapters = repo.list_source_chapters()
    if not chapters:
        return {}
    last_ch = chapters[-1].chapter_no
    tail_start = max(1, last_ch - 4)
    tail_events = [e for e in repo.list_source_events() if e["chapter_no"] >= tail_start]
    tail_snaps = [s for s in repo.list_character_snapshots() if s["chapter_no"] >= tail_start]
    all_snaps = repo.list_character_snapshots()
    latest_by_char: dict[str, dict[str, Any]] = {}
    for s in all_snaps:
        if s["chapter_no"] >= latest_by_char.get(s["character_name"], {}).get("chapter_no", -1):
            latest_by_char[s["character_name"]] = s
    absent = [
        {
            "name": name,
            "last_seen_chapter": row["chapter_no"],
            "last_state": row.get("snapshot") or {},
        }
        for name, row in latest_by_char.items()
        if row["chapter_no"] < tail_start
    ]
    absent.sort(key=lambda x: x["last_seen_chapter"], reverse=True)
    ev_blob = "\n".join(f"[第{e['chapter_no']}章] {e['summary']}（{e['effects']}）" for e in tail_events[:80])
    sn_blob = "\n".join(
        f"[第{s['chapter_no']}章] {s['character_name']}: 在{s['snapshot'].get('location','')}, "
        f"{s['snapshot'].get('emotional_state','')}, 目标={s['snapshot'].get('goal_now','')}"
        for s in tail_snaps[:60]
    )
    absent_blob = "\n".join(
        f"- {a['name']}：最后出现第{a['last_seen_chapter']}章，"
        f"状态={a['last_state'].get('physical_state','') or a['last_state'].get('emotional_state','')}, "
        f"目标={a['last_state'].get('goal_now','')}"
        for a in absent[:40]
    )
    data = {}
    try:
        data = _parse_json(llm.complete_at(
            _LAST_STATE_SYS,
            f"[末尾五章事件]\n{ev_blob}\n\n[末尾五章人物状态]\n{sn_blob}\n\n"
            f"[absent 索引：全书出现过但末尾五章未再出现]\n{absent_blob or '（无）'}\n\n只输出 JSON。",
            AGG_TEMPERATURE)) or {}
    except Exception:
        data = {}
    if isinstance(data, dict):
        data.setdefault("absent_index", absent[:40])
        return data
    return {"absent_index": absent[:40]}


# ============ C2.d 时间线 ============
_TIMELINE_SYS = (
    "你在为长篇小说续写系统蒸馏时间线。下面给你每章的时间线索词。把全书排成相对时间线，"
    "处理'几天后/翌日/那年冬天'这类相对标记。\n"
    "只输出 JSON：{\"timeline\":[{\"chapter_no\":0,\"relative_day\":\"从第1天起的相对日期\",\"season\":\"\",\"summary\":\"\"}]}"
)


def aggregate_timeline(repo: Repository, llm: LLMClient) -> list[dict]:
    events = repo.list_source_events()
    if not events:
        return []
    by_ch: dict[int, list[str]] = defaultdict(list)
    for e in events:
        if e["time_marker"].strip():
            by_ch[e["chapter_no"]].append(e["time_marker"].strip())
    if not by_ch:
        return [{"chapter_no": c.chapter_no, "title": c.title, "summary": c.summary[:80]}
                for c in repo.list_source_chapters()]
    blob = "\n".join(f"第{ch}章: {'/'.join(set(marks))}" for ch, marks in sorted(by_ch.items()))
    data = {}
    try:
        data = _parse_json(llm.complete_at(
            _TIMELINE_SYS, f"[各章时间线索]\n{blob}\n\n只输出 JSON。", AGG_TEMPERATURE)) or {}
    except Exception:
        data = {}
    tl = data.get("timeline") if isinstance(data, dict) else None
    if isinstance(tl, list) and tl:
        return [t for t in tl if isinstance(t, dict)]
    return [{"chapter_no": ch, "markers": list(set(marks))} for ch, marks in sorted(by_ch.items())]


# ============ C2.e 知识图谱精炼 ============
_GRAPH_SYS = (
    "你在为长篇小说续写系统精炼知识图谱。下面给你角色清单、地点清单、势力清单、物件/能力物清单，"
    "以及角色共现和道具共现统计。输出原作末态的关系边：人物-人物、人物-势力、人物-地点、势力-势力、"
    "人物-物件。人物-物件关系优先使用 possesses / seeks / controls。绝不杜撰。\n"
    "只输出 JSON：{\"edges\":[{\"src\":\"名\",\"src_type\":\"character|location|faction|object\","
    "\"rel\":\"简短英文下划线关系\",\"dst\":\"名\",\"dst_type\":\"character|location|faction|object\","
    "\"intensity\":0.0到1.0,\"note\":\"\"}]}"
)


def refine_graph(repo: Repository, llm: LLMClient, *, name_maps: dict[str, dict]) -> dict[str, Any]:
    char_names = [e.name for e in repo.list_entities() if e.type == "character"]
    object_names = [e.name for e in repo.list_entities() if e.type == "object"]
    loc_names = [l.name for l in repo.list_locations()]
    fac_names = [f.name for f in repo.list_factions()]
    if not char_names:
        return {"edges": 0}
    events = repo.list_source_events()
    cooc: Counter = Counter()
    for e in events:
        ps = [p for p in e["participants"] if p in char_names]
        for i in range(len(ps)):
            for j in range(i + 1, len(ps)):
                cooc[tuple(sorted((ps[i], ps[j])))] += 1
    item_cooc: Counter = Counter()
    for e in events:
        text = f"{e.get('summary','')} {e.get('effects','')}"
        present_items = [n for n in object_names if n and n in text]
        present_chars = [p for p in e["participants"] if p in char_names]
        for c in present_chars:
            for item in present_items:
                item_cooc[(c, item)] += 1
    cooc_blob = "\n".join(f"{a} ↔ {b}: 共现{n}次" for (a, b), n in cooc.most_common(30))
    item_blob = "\n".join(f"{a} → {b}: 事件文本共现{n}次" for (a, b), n in item_cooc.most_common(60))
    blob = (f"[角色]{', '.join(char_names[:20])}\n[地点]{', '.join(loc_names[:15])}\n"
            f"[势力]{', '.join(fac_names[:10])}\n[物件/能力物]{', '.join(object_names[:40])}\n\n"
            f"[角色共现统计]\n{cooc_blob}\n\n[道具共现统计]\n{item_blob}")
    data = {}
    try:
        data = _parse_json(llm.complete_at(_GRAPH_SYS, f"{blob}\n\n只输出 JSON。", AGG_TEMPERATURE)) or {}
    except Exception:
        data = {}
    name_to_id: dict[tuple, str] = {}
    for e in repo.list_entities():
        name_to_id[(e.type, e.name)] = e.entity_id
    for f in repo.list_factions():
        name_to_id[("faction", f.name)] = f.faction_id
    added = 0
    for ed in (data.get("edges") or []):
        if not isinstance(ed, dict):
            continue
        st = str(ed.get("src_type", "character")).strip()
        dt = str(ed.get("dst_type", "character")).strip()
        sid = name_to_id.get((st, str(ed.get("src", "")).strip()))
        did = name_to_id.get((dt, str(ed.get("dst", "")).strip()))
        if not sid or not did or sid == did:
            continue
        rel = re.sub(r"[^a-z_]", "", str(ed.get("rel", "")).strip().lower().replace(" ", "_")) or "related_to"
        try:
            inten = max(0.1, min(1.0, float(ed.get("intensity", 0.6))))
        except Exception:
            inten = 0.6
        repo.upsert_edge(GraphEdge(src=sid, rel=rel, dst=did,
                                   meta={"source": "full_distill", "note": str(ed.get("note", "")).strip()},
                                   intensity=inten))
        added += 1
    # 叠加确定性共现边（兜底，保证图谱不空）
    for (a, b), n in cooc.most_common(40):
        sid = name_to_id.get(("character", a))
        did = name_to_id.get(("character", b))
        if sid and did and sid != did:
            repo.upsert_edge(GraphEdge(src=sid, rel="co_appears", dst=did,
                                        meta={"source": "cooccurrence"}, intensity=min(1.0, 0.3 + n * 0.1)))
            added += 1
    for (char_name, item_name), n in item_cooc.most_common(180):
        sid = name_to_id.get(("character", char_name))
        did = name_to_id.get(("object", item_name))
        if not sid or not did or sid == did:
            continue
        rel = "possesses"
        note = ""
        for e in events:
            if char_name not in (e.get("participants") or []):
                continue
            text = f"{e.get('summary','')} {e.get('effects','')}"
            if item_name not in text:
                continue
            note = text[:120]
            if re.search(r"(寻找|追|索要|觊觎|争夺|夺|抢|偷|要回)", text):
                rel = "seeks"
            if re.search(r"(控制|掌控|藏|封存|保管|守住)", text):
                rel = "controls"
            if re.search(r"(给|交|递|送|还|拿|带|持|握|拾|获得|得到|收下)", text):
                rel = "possesses"
            break
        repo.upsert_edge(GraphEdge(
            src=sid, rel=rel, dst=did,
            meta={"source": "item_event_cooccurrence", "note": note},
            intensity=min(1.0, 0.35 + n * 0.08),
        ))
        added += 1
    return {"edges": len(repo.list_edges()), "added": added}


# ============ C2.f 伏笔配对 ============
_PAIR_SYS = (
    "你在为长篇小说续写系统做伏笔配对。下面给你全书伏笔候选清单与全书事件清单。"
    "判断每个 setup：在后续哪个事件被回报(paid)、埋了但全书没回报(open)、其实不是伏笔(discarded)。\n"
    "只输出 JSON：{\"pairings\":[{\"setup_id\":\"\",\"status\":\"paid|open|discarded\","
    "\"payoff_event_seq\":全局事件序号或null,\"confidence\":0.0到1.0,\"reason\":\"一句理由\"}]}"
)


def pair_foreshadows(repo: Repository, llm: LLMClient, *, batch_size: int = 40,
                     max_workers: int = 6, only_pending: bool = False) -> dict[str, Any]:
    """伏笔配对：把全部 setups 分批喂给 LLM，并发跑。

    only_pending=True 时只配对仍 status='pending' 的 setup（用于补全上次未完成的配对）。
    """
    from concurrent.futures import ThreadPoolExecutor
    import threading

    setups = repo.list_foreshadows(status="pending") if only_pending else repo.list_foreshadows()
    events = repo.list_source_events()
    if not setups:
        return {"paired": 0, "open": 0, "batches": 0}
    indexed = list(enumerate(events, 1))
    idx_to_eid = {i: e["event_id"] for i, e in indexed}
    idx_to_ch = {i: e["chapter_no"] for i, e in indexed}
    # 关键事件清单（截断到 12k 字以内供每个 batch 复用）
    ev_blob = "\n".join(f"{i}. [第{e['chapter_no']}章] {e['summary']}"
                        for i, e in indexed if e["kind"] in ("reveal", "gain", "loss", "threshold", "decision"))
    if len(ev_blob) > 12000:
        ev_blob = ev_blob[:12000]

    # 分批
    batches: list[list[dict]] = []
    for i in range(0, len(setups), batch_size):
        batches.append(setups[i:i + batch_size])

    valid_ids = {s["setup_id"] for s in setups}
    results_lock = threading.Lock()
    counters = {"paired": 0, "open": 0, "paid": 0, "discarded": 0}

    def _run_batch(batch: list[dict]) -> None:
        su_blob = "\n".join(
            f"{s['setup_id']} [第{s['chapter_no']}章]: {s['what_planted']}（{s['excerpt']}）"
            for s in batch
        )
        data = {}
        try:
            raw = llm.complete_at(
                _PAIR_SYS,
                f"[伏笔候选（本批 {len(batch)} 条）]\n{su_blob}\n\n[关键事件]\n{ev_blob}\n\n只输出 JSON。",
                AGG_TEMPERATURE,
            )
            data = _parse_json(raw) or {}
        except Exception:
            data = {}
        for p in (data.get("pairings") or []):
            if not isinstance(p, dict) or p.get("setup_id") not in valid_ids:
                continue
            status = str(p.get("status", "open")).strip()
            if status not in ("paid", "open", "discarded"):
                status = "open"
            seq = p.get("payoff_event_seq")
            eid = idx_to_eid.get(seq) if isinstance(seq, int) else ""
            pch = idx_to_ch.get(seq, 0) if isinstance(seq, int) else 0
            try:
                conf = max(0.0, min(1.0, float(p.get("confidence", 0.5))))
            except Exception:
                conf = 0.5
            with results_lock:
                repo.update_foreshadow_pairing(
                    setup_id=p["setup_id"], status=status, payoff_event_id=eid or "",
                    payoff_chapter=pch, confidence=conf, reason=str(p.get("reason", "")).strip(),
                )
                counters["paired"] += 1
                counters[status] = counters.get(status, 0) + 1

    if max_workers <= 1 or len(batches) <= 1:
        for b in batches:
            _run_batch(b)
    else:
        with ThreadPoolExecutor(max_workers=min(max_workers, len(batches))) as ex:
            list(ex.map(_run_batch, batches))

    return {"paired": counters["paired"], "open": counters.get("open", 0),
            "paid": counters.get("paid", 0), "discarded": counters.get("discarded", 0),
            "batches": len(batches), "total_setups": len(setups)}


# ============ 材料化 threads（供 chapter_planner / 大纲使用） ============
def materialize_threads(repo: Repository, last_state: dict[str, Any]) -> int:
    """从 open 伏笔 + open 剧情线 + last_state 的未解冲突 生成续写用的 Thread。"""
    name_to_aid = {e.name: e.entity_id for e in repo.list_entities() if e.type == "character"}
    created = 0
    seen: set[str] = set()

    def _add(question: str, involved_names: list[str], weight: float, tension: float):
        nonlocal created
        q = question.strip()
        if not q or q in seen:
            return
        seen.add(q)
        involved = [name_to_aid[n] for n in involved_names if n in name_to_aid]
        repo.insert_thread(Thread(
            thread_id=_sid("thr", q), central_question=q, involved_agents=involved,
            priority_weight=weight, current_tension=tension, status="open",
        ))
        created += 1

    for arc in repo.list_story_arcs():
        if arc["resolution_status"] in ("open", "partial") and arc["journey_summary"]:
            _add(f"{arc['name']}：{arc['journey_summary']}", [], 0.85, 0.7)
    for fs in repo.list_foreshadows(status="open"):
        _add(fs["what_planted"], [], 0.6 + 0.3 * float(fs.get("salience", 0.5)), 0.65)
    for conf in (last_state.get("active_conflicts") or []):
        if isinstance(conf, dict) and str(conf.get("stakes", "")).strip():
            _add(f"{conf.get('between','')}：{conf.get('stakes','')}".strip("："), [], 0.8, 0.72)
    for hook in (last_state.get("hooks_for_next_book") or []):
        _add(str(hook), [], 0.75, 0.7)
    return created


# ============ 全书聚合主入口 ============
def aggregate_full_book(repo: Repository, llm: LLMClient,
                        on_progress=None) -> dict[str, Any]:
    """C2 主入口：依序跑材料化 + 聚合。返回各步统计。"""
    def _p(step: str, i: int, n: int = 10):
        if on_progress:
            on_progress(step, i, n)

    _p("characters", 1)
    chars = aggregate_characters(repo, llm)
    _p("locations", 2)
    locs = aggregate_locations(repo, llm)
    _p("items", 3)
    items = materialize_items(repo)
    _p("factions", 4)
    facs = aggregate_factions(repo, llm)
    _p("arcs", 5)
    arcs = aggregate_story_arcs(repo, llm)
    _p("last_state", 6)
    last_state = aggregate_last_state(repo, llm)
    _p("timeline", 7)
    timeline = aggregate_timeline(repo, llm)
    _p("graph", 8)
    graph = refine_graph(repo, llm, name_maps={"loc": locs, "fac": facs, "item": items})
    _p("foreshadow", 9)
    fs = pair_foreshadows(repo, llm)
    _p("threads", 10)
    threads = materialize_threads(repo, last_state)

    return {
        "characters": chars.get("characters", 0),
        "minor_llm_skipped": chars.get("skipped_minor_llm", 0),
        "locations": locs.get("locations", 0),
        "items": items.get("items", 0),
        "factions": facs.get("factions", 0),
        "arcs": arcs.get("arcs", 0),
        "graph_edges": graph.get("edges", 0),
        "foreshadow_paired": fs.get("paired", 0),
        "foreshadow_open": fs.get("open", 0),
        "threads": threads,
        "last_state": last_state,
        "timeline": timeline,
    }
