"""序列化：把引擎 dataclass 映射为前端 HttpAdapter 期望的 camelCase JSON。

引擎内部用 snake_case；前端契约用 camelCase。这里是唯一的翻译边界。
"""
from __future__ import annotations

import json
from typing import Any

from novel_engine.models import (
    Arc,
    AuthorWritingSheet,
    Beat,
    ChapterPlan,
    Ending,
    Event,
    Fact,
    Foreshadow,
    InventoryItem,
    KnowledgeItem,
    Part,
    Persona,
    ReaderKnowledge,
    RevealNode,
    Scene,
    SceneAnchor,
    StyleClaim,
    Thread,
)


def fact_out(f: Fact) -> dict[str, Any]:
    return {
        "factId": f.fact_id,
        "factType": f.fact_type,
        "canonicalContent": f.canonical_content,
        "storyTime": f.story_time,
        "locationId": f.location_id,
        "involvedEntities": f.involved_entities,
    }


def event_out(e: Event, drama: float | None = None) -> dict[str, Any]:
    payload = e.payload if isinstance(e.payload, str) else json.dumps(e.payload, ensure_ascii=False)
    return {
        "eventId": e.event_id,
        "storyTime": e.story_time,
        "actors": e.actors,
        "actionType": e.action_type,
        "payload": payload,
        "locationId": e.location_id,
        "perceivers": e.perceivers,
        "dramaScore": drama,
        "beatId": e.beat_id,
    }


def knowledge_out(k: KnowledgeItem) -> dict[str, Any]:
    return {
        "agentId": k.agent_id,
        "factId": k.fact_id,
        "versionContent": k.version_content,
        "confidence": k.confidence,
        "learnedTick": k.learned_tick,
    }


def reader_out(r: ReaderKnowledge) -> dict[str, Any]:
    return {
        "factId": r.fact_id,
        "revealedVersion": r.revealed_version,
        "revealedDiscoursePos": r.revealed_discourse_pos,
        "viaPov": r.via_pov,
    }


def beat_out(b: Beat) -> dict[str, Any]:
    return {
        "beatId": b.beat_id,
        "sequenceOrder": b.sequence_order,
        "type": b.type,
        "goal": b.goal,
        "threads": b.threads,
        "targetTension": b.target_tension,
        "targetEndingLink": b.target_ending_link or "",
        "status": b.status,
    }


def thread_out(t: Thread) -> dict[str, Any]:
    return {
        "threadId": t.thread_id,
        "centralQuestion": t.central_question,
        "involvedAgents": t.involved_agents,
        "priorityWeight": t.priority_weight,
        "currentTension": t.current_tension,
        "lastAdvancedTick": t.last_advanced_tick,
        "status": t.status,
    }


def foreshadow_out(f: Foreshadow) -> dict[str, Any]:
    return {
        "foreshadowId": f.foreshadow_id,
        "plantedDiscoursePos": f.planted_discourse_pos,
        "question": f.question,
        "linkedFactId": f.linked_fact_id,
        "mustResolve": f.must_resolve,
        "targetPayoffBeat": f.target_payoff_beat or "",
        "status": f.status,
    }


def scene_out(s: Scene) -> dict[str, Any]:
    return {
        "sceneId": s.scene_id,
        "discourseOrder": s.discourse_order,
        "sourceEvents": s.source_events,
        "pov": s.pov,
        "targetTension": s.target_tension,
        "proseText": s.prose_text,
        "newlyRevealed": s.newly_revealed,
    }


def scene_anchor_out(a: SceneAnchor) -> dict[str, Any]:
    return {
        "sceneId": a.scene_id,
        "name": a.name,
        "kind": a.kind,
        "locationId": a.location_id,
        "canonicalFacts": a.canonical_facts,
        "aliases": a.aliases,
        "establishedChapter": a.established_chapter,
        "createdAt": a.created_at,
    }


def ending_out(e: Ending) -> dict[str, Any]:
    return {
        "id": e.ending_id,
        "summary": e.summary,
        "themeExpression": e.theme_expression,
        "requiredConditions": e.required_conditions,
        "activeWeight": e.active_weight,
    }


def persona_out(p: Persona, display_name: str | None = None) -> dict[str, Any]:
    # 引擎 cost_threshold/arc_state 是 dict；前端契约是 string → 友好地拍平展示。
    return {
        "id": p.agent_id,
        "name": p.name,
        "displayName": display_name or p.name,
        "want": p.want,
        "values": p.values,
        "fatalFlaw": p.fatal_flaw,
        "obstacles": p.obstacles,
        "costThreshold": _flatten(p.cost_threshold),
        "voice": p.voice,
        "mannerisms": p.mannerisms,
        "motifObjects": p.motif_objects,
        "arcState": _flatten(p.arc_state),
        "costLedger": p.cost_ledger,
    }


# ---------- 规划层（大纲驱动） ----------
def part_out(p: Part) -> dict[str, Any]:
    return {
        "partId": p.part_id,
        "sequenceOrder": p.sequence_order,
        "title": p.title,
        "goal": p.goal,
        "region": p.region,
        "revealNodeIds": p.reveal_node_ids,
        "status": p.status,
    }


def arc_out(a: Arc) -> dict[str, Any]:
    return {
        "arcId": a.arc_id,
        "partId": a.part_id,
        "sequenceOrder": a.sequence_order,
        "title": a.title,
        "summary": a.summary,
        "targetChapters": a.target_chapters,
        "focusAgents": [
            {"agentId": f.get("agent_id", f.get("agentId", "")), "weight": f.get("weight", 0)}
            for f in (a.focus_agents or [])
        ],
        "status": a.status,
    }


def chapter_plan_out(c: ChapterPlan, names: dict[str, str] | None = None) -> dict[str, Any]:
    names = names or {}
    return {
        "chapterId": c.chapter_id,
        "arcId": c.arc_id,
        "sequenceOrder": c.sequence_order,
        "title": c.title,
        "cast": c.cast,
        "castNames": [names.get(a, a) for a in c.cast],
        "locationIds": c.location_ids,
        "availableItems": c.available_items,
        "beatGoals": c.beat_goals,
        "revealGate": c.reveal_gate,
        "threadDecisions": c.thread_decisions_json,
        "targetScenes": c.target_scenes,
        "role": c.role,
        "targetTension": c.target_tension,
        "dramaticQuestion": c.dramatic_question,
        "itemsPresent": c.items_present,
        "itemsIntroduced": c.items_introduced,
        "itemsConsumed": c.items_consumed,
        "targetWords": c.target_words,
        "endingHook": c.ending_hook,
        "hookType": c.hook_type,
        "status": c.status,
    }


def character_card_out(c, display_name: str | None = None) -> dict[str, Any]:
    """§1 选角层身份卡 + W4 三维度 序列化。"""
    return {
        "cardId": c.card_id,
        "agentId": c.agent_id,
        "tier": c.tier,
        "slotKey": c.slot_key,
        "name": c.name,
        "displayName": display_name or c.name,
        "oneLiner": c.one_liner,
        "voiceRegister": c.voice_register,
        "definingTrait": c.defining_trait,
        "coreDesire": c.core_desire,
        "verbalHabits": c.verbal_habits,
        "keyRelation": c.key_relation,
        "backstory": c.backstory,
        "fatalFlaw": c.fatal_flaw,
        "arc": c.arc,
        # W4 三维度
        "appearance": getattr(c, "appearance", ""),
        "socialRole": getattr(c, "social_role", ""),
        "psychology": getattr(c, "psychology", ""),
        "foreshadowFrom": getattr(c, "foreshadow_from", 0),
        "revealChapter": getattr(c, "reveal_chapter", 0),
        "secretRevealChapter": getattr(c, "secret_reveal_chapter", 0),
        "foreshadowHint": getattr(c, "foreshadow_hint", ""),
        "secretTruth": getattr(c, "secret_truth", ""),
    }


def faction_out(f) -> dict[str, Any]:
    """W3 势力一等实体序列化。"""
    return {
        "factionId": f.faction_id,
        "name": f.name,
        "ideology": f.ideology,
        "goals": f.goals,
        "methods": f.methods,
        "territory": f.territory,           # canon loc_id 列表
        "structure": f.structure,
        "keyMembers": f.key_members,        # [{name,role,agent_id?,note}]
        "history": f.history,
        "relations": f.relations,           # [{target_faction_id,kind,intensity,note}]
        "secret": f.secret,
        "summary": f.summary,
        "detail": f.detail,
        "source": f.source,
        "foreshadowFrom": getattr(f, "foreshadow_from", 0),
        "revealChapter": getattr(f, "reveal_chapter", 0),
        "secretRevealChapter": getattr(f, "secret_reveal_chapter", 0),
        "foreshadowHint": getattr(f, "foreshadow_hint", ""),
        "secretTruth": getattr(f, "secret_truth", ""),
    }


def location_out(l) -> dict[str, Any]:
    """§12.3 地点一等实体序列化（W2 加 level/parent/culture_local/summary/detail 两级）。"""
    return {
        "locId": l.loc_id,
        "partId": l.part_id,
        "name": l.name,
        "geoFull": l.geo_full,
        "connectsTo": l.connects_to,
        "controllingFaction": l.controlling_faction,
        "notableItems": l.notable_items,
        "level": getattr(l, "level", ""),
        "parent": getattr(l, "parent", ""),
        "cultureLocal": getattr(l, "culture_local", ""),
        "summary": getattr(l, "summary", ""),
        "detail": getattr(l, "detail", ""),
        "foreshadowFrom": getattr(l, "foreshadow_from", 0),
        "revealChapter": getattr(l, "reveal_chapter", 0),
        "secretRevealChapter": getattr(l, "secret_reveal_chapter", 0),
        "foreshadowHint": getattr(l, "foreshadow_hint", ""),
        "secretTruth": getattr(l, "secret_truth", ""),
    }


def tone_profile_out(p) -> dict[str, Any]:
    """§16 文风契约序列化（供前端「大纲」展示 + 确认）。"""
    return {
        "genre": p.genre,
        "primaryEffect": p.primary_effect,
        "register": p.register,
        "sentenceRhythm": p.sentence_rhythm,
        "dictionDo": p.diction_do,
        "dictionDont": p.diction_dont,
        "deviceKit": p.device_kit,
        "pacing": p.pacing,
        "tensionCurveBias": p.tension_curve_bias,
        "revealCadence": p.reveal_cadence,
        "complexity": p.complexity,
        "toneReference": p.tone_reference,
        "confirmed": p.confirmed,
        "eraLogic": p.era_logic or {},
    }


def style_skill_out(p) -> dict[str, Any]:
    """B0 文风模拟序列化（供前端上传后确认 + 展示 6 维指纹）。"""
    return {
        "name": p.name,
        "source": p.source,
        "register": p.register,
        "rhythm": p.rhythm,
        "devices": p.devices,
        "dictionDo": p.diction_do,
        "dictionDont": p.diction_dont,
        "motifs": p.motifs,
        "samples": p.samples,
        "metrics": p.metrics,
        "enabled": p.enabled,
    }


def reveal_node_out(n: RevealNode) -> dict[str, Any]:
    return {
        "nodeId": n.node_id,
        "factId": n.fact_id,
        "kind": n.kind,
        "sequenceOrder": n.sequence_order,
        "prereqNodeIds": n.prereq_node_ids,
        "partId": n.part_id,
        "description": n.description,
        "discovered": n.discovered,
        "discoveredChapter": n.discovered_chapter,
    }


def inventory_out(i: InventoryItem, name: str = "") -> dict[str, Any]:
    return {
        "objectId": i.object_id,
        "name": name or i.object_id,
        "holderAgentId": i.holder_agent_id,
        "status": i.status,
        "acquiredChapter": i.acquired_chapter,
        "note": i.note,
    }


def _claim_out(c: StyleClaim) -> dict[str, str]:
    return {"claim": c.claim, "evidence": c.evidence, "sourceChapter": c.source_chapter}


def author_sheet_out(s: AuthorWritingSheet) -> dict[str, Any]:
    """S1 Author Writing Sheet 序列化（四维 Claim-Evidence）。"""
    return {
        "name": s.name,
        "sourceGenre": s.source_genre,
        "plot": [_claim_out(c) for c in s.plot],
        "creativity": [_claim_out(c) for c in s.creativity],
        "development": [_claim_out(c) for c in s.development],
        "language": [_claim_out(c) for c in s.language],
        "personaMd": s.persona_md,
        "nSegments": s.n_segments,
    }


def _flatten(v: Any) -> str:
    if isinstance(v, str):
        return v
    if isinstance(v, dict):
        if not v:
            return ""
        return "；".join(f"{k}={val}" for k, val in v.items())
    return str(v)
