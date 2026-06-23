from __future__ import annotations

from collections import Counter
from typing import Any

from ..models import ChapterPlan, StylePacket, StyleSegment
from ..repository import Repository
from .retrieval import retrieve_style_exemplars
from .voice_router import route_style_voice


def build_style_packet(repo: Repository, *, chapter_plan: ChapterPlan | None,
                       guidance: str = "", previous_tail: str = "", beat_text: str = "",
                       beat_index: int = 0, pov_character_id: str = "", speaker: str = "") -> StylePacket:
    segments = repo.list_style_segments()
    continuation = repo.get_continuation_meta()
    life_model = repo.latest_author_life_model() if continuation.experience_layer_enabled else None
    discourse_type = _preferred_discourse_type(chapter_plan, guidance, beat_text)
    scene_type = _preferred_scene_type(chapter_plan, guidance, beat_text)
    target_voice_type = "character" if discourse_type == "dialogue" else ("mixed" if discourse_type in {"interior", "free_indirect"} else "narrator")
    emotion = _preferred_emotion(guidance, beat_text, previous_tail)
    relationship = _preferred_relationship(guidance, beat_text)
    exemplars = retrieve_style_exemplars(
        segments,
        discourse_type=discourse_type,
        scene_type=scene_type,
        emotion=emotion,
        relationship=relationship,
        semantic_seed=" ".join(filter(None, [guidance, beat_text, previous_tail[-120:]])),
        target_voice_type=target_voice_type,
        limit=6,
    )
    negatives = repo.list_style_negative_samples(limit=6)
    router = route_style_voice(
        speaker=speaker or pov_character_id or (chapter_plan.pov_agent if chapter_plan else ""),
        pov=pov_character_id or (chapter_plan.pov_agent if chapter_plan else ""),
        discourse_type=discourse_type,
        emotion=emotion or _dominant_emotion(exemplars),
        scene_function=scene_type,
        relationship=relationship,
    )
    target_statistics = _aggregate_target_statistics(exemplars)
    diagnostics = {
        "requestedDiscourseType": discourse_type,
        "requestedSceneType": scene_type,
        "requestedEmotion": emotion,
        "requestedRelationship": relationship,
        "retrievedCount": len(exemplars),
        "availableCorpusSize": len(segments),
        "experienceLayerEnabled": continuation.experience_layer_enabled,
        "experienceLayerMode": continuation.experience_layer_mode,
        "experienceStyleLevel": continuation.experience_style_level,
        "lifeModelId": life_model.model_id if life_model else "",
    }
    return StylePacket(
        packet_id=f"stypkt_{beat_index}_{(pov_character_id or speaker or 'narrator')[:24]}",
        beat_index=beat_index,
        beat_label=(beat_text or guidance or "")[:80],
        global_prior=repo.get_style_skill().metrics if repo.get_style_skill().is_set() else {},
        voice_profile={
            "primary": router["primary_profile"],
            "secondary": router["secondary_profile"],
            "weights": router,
        },
        scene_profile={
            "discourseType": discourse_type,
            "sceneType": scene_type,
            "guidance": guidance,
            "beatText": beat_text,
            "povCharacterId": pov_character_id or (chapter_plan.pov_agent if chapter_plan else ""),
        },
        target_statistics=target_statistics,
        positive_exemplars=[_segment_card(seg) for seg in exemplars],
        negative_patterns=[
            {
                "id": sample.id,
                "failureTypes": sample.failure_types_json,
                "text": sample.text[:120],
            }
            for sample in negatives
        ],
        previous_paragraph_tail=(previous_tail or "")[-180:],
        next_beat_constraint=(beat_text or guidance or "")[:160],
        router=router,
        experience_prior=_experience_prior(life_model),
        diagnostics=diagnostics,
    )


def _experience_prior(life_model) -> dict[str, Any]:
    if life_model is None:
        return {}
    return {
        "summary": life_model.summary,
        "coreWound": life_model.core_wound_json,
        "defensePatterns": life_model.defense_patterns_json[:4],
        "desireVectors": life_model.desire_vectors_json[:4],
        "relationshipModel": life_model.relationship_model_json,
        "narrativeEngines": life_model.narrative_engines_json[:4],
        "proseRules": life_model.prose_rules_json,
        "worldview": life_model.worldview_json,
        "evidence": life_model.evidence_json[:4],
        "personaPrompt": life_model.persona_prompt,
    }


def _aggregate_target_statistics(exemplars: list[StyleSegment]) -> dict[str, Any]:
    if not exemplars:
        return {}
    sentence_p50 = [seg.feature_json.get("sentence_length", {}).get("p50", 0.0) for seg in exemplars]
    sentence_p90 = [seg.feature_json.get("sentence_length", {}).get("p90", 0.0) for seg in exemplars]
    comma_density = [seg.feature_json.get("punctuation", {}).get("comma_per_100_chars", 0.0) for seg in exemplars]
    dialogue_ratio = [seg.feature_json.get("dialogue", {}).get("ratio", 0.0) for seg in exemplars]
    median_turn = [seg.feature_json.get("dialogue", {}).get("median_turn_chars", 0.0) for seg in exemplars]
    clause_p50 = [seg.feature_json.get("clause_length", {}).get("p50", 0.0) for seg in exemplars]
    return {
        "sentence_length": {
            "p50": round(sum(sentence_p50) / len(sentence_p50), 3),
            "p90": round(sum(sentence_p90) / len(sentence_p90), 3),
        },
        "clause_length": {
            "p50": round(sum(clause_p50) / len(clause_p50), 3),
        },
        "punctuation": {
            "comma_per_100_chars": round(sum(comma_density) / len(comma_density), 3),
        },
        "dialogue": {
            "ratio": round(sum(dialogue_ratio) / len(dialogue_ratio), 3),
            "median_turn_chars": round(sum(median_turn) / len(median_turn), 3),
        },
    }


def _segment_card(seg: StyleSegment) -> dict[str, Any]:
    return {
        "id": seg.id,
        "sourceChapterId": seg.source_chapter_id,
        "voiceType": seg.voice_type,
        "discourseType": seg.discourse_type,
        "sceneType": seg.scene_type,
        "emotion": seg.emotion_json,
        "registerType": seg.register_type,
        "featureJson": seg.feature_json,
        "text": seg.text,
    }


def _preferred_discourse_type(chapter_plan: ChapterPlan | None, guidance: str, beat_text: str) -> str:
    text = " ".join(filter(None, [
        guidance,
        beat_text,
        chapter_plan.conflict_type if chapter_plan else "",
        " ".join(chapter_plan.beat_goals) if chapter_plan and chapter_plan.beat_goals else "",
    ]))
    if any(token in text for token in ("对话", "问", "回答", "试探", "交谈")):
        return "dialogue"
    if any(token in text for token in ("发现", "追", "逃", "冲突", "战")):
        return "action"
    if any(token in text for token in ("意识到", "回忆", "想起", "命运", "心里")):
        return "reflection"
    if any(token in text for token in ("心里", "脑海", "不由得")):
        return "interior"
    return "narration"


def _preferred_scene_type(chapter_plan: ChapterPlan | None, guidance: str, beat_text: str) -> str:
    text = " ".join(filter(None, [
        guidance,
        beat_text,
        chapter_plan.conflict_type if chapter_plan else "",
        chapter_plan.dramatic_question if chapter_plan else "",
    ]))
    if any(token in text for token in ("隐瞒", "试探", "信息")):
        return "investigation"
    if any(token in text for token in ("旧友", "朋友", "熟人", "对话")):
        return "conversation"
    if any(token in text for token in ("冲突", "追逐", "战", "袭击")):
        return "confrontation"
    return "general"


def _preferred_emotion(guidance: str, beat_text: str, previous_tail: str) -> str:
    text = " ".join(filter(None, [guidance, beat_text, previous_tail[-60:]]))
    if any(token in text for token in ("隐瞒", "防备", "掩饰", "嘴硬")):
        return "defensive_humor"
    if any(token in text for token in ("害怕", "恐惧", "发冷", "发抖")):
        return "fear"
    if any(token in text for token in ("愤怒", "咬牙", "压着火")):
        return "suppressed_anger"
    return ""


def _preferred_relationship(guidance: str, beat_text: str) -> str:
    text = " ".join(filter(None, [guidance, beat_text]))
    if any(token in text for token in ("熟人", "旧友", "朋友")):
        return "friend_with_distance"
    if any(token in text for token in ("老师", "上级", "长辈")):
        return "hierarchical"
    return ""


def _dominant_emotion(exemplars: list[StyleSegment]) -> str:
    emotions = Counter(emotion for seg in exemplars for emotion in seg.emotion_json)
    return emotions.most_common(1)[0][0] if emotions else "neutral"
