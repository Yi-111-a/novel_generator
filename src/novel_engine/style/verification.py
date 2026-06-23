from __future__ import annotations

import math
import re
from typing import Any

from .corpus import extract_style_features
from .evaluation import compute_style_drift


def score_style_candidate(text: str, *, style_packet: dict[str, Any] | None = None,
                          previous_tail: str = "", source_texts: list[str] | None = None) -> dict[str, Any]:
    style_packet = style_packet or {}
    source_texts = source_texts or []
    features = extract_style_features(text)
    target = style_packet.get("target_statistics", {}) or {}
    stylometric_similarity = _stylometric_similarity(features, target)
    repetition_penalty = _repetition_penalty(text)
    overlap_penalty = _source_overlap_penalty(text, source_texts)
    continuity_score = _continuity_score(text, previous_tail)
    diversity_score = min(1.0, features.get("lexical", {}).get("unique_char_ratio", 0.0) * 1.6)
    voice_similarity = _voice_similarity(text, style_packet, features)
    voice_collapse_penalty = _voice_collapse_penalty(text, style_packet)
    scene_completion = 1.0 if _contains_actionable_progress(text) else 0.65
    content_consistency = 0.9 if previous_tail and _shares_anchor(previous_tail, text) else 0.75
    paragraph_similarity = _paragraph_similarity(text, target)
    caricature_penalty = _caricature_penalty(text)
    drift = compute_style_drift(text, target_statistics=target)
    drift_penalty = _drift_penalty(drift)
    final_score = (
        0.25 * content_consistency
        + 0.18 * voice_similarity
        + 0.15 * stylometric_similarity
        + 0.12 * paragraph_similarity
        + 0.10 * scene_completion
        + 0.10 * diversity_score
        + 0.10 * continuity_score
        - 0.12 * caricature_penalty
        - 0.15 * overlap_penalty
        - 0.08 * repetition_penalty
        - 0.10 * voice_collapse_penalty
        - 0.06 * drift_penalty
    )
    return {
        "finalScore": round(final_score, 4),
        "contentConsistency": round(content_consistency, 4),
        "voiceSimilarity": round(voice_similarity, 4),
        "stylometricSimilarity": round(stylometric_similarity, 4),
        "paragraphStructureSimilarity": round(paragraph_similarity, 4),
        "sceneFunctionCompletion": round(scene_completion, 4),
        "diversityScore": round(diversity_score, 4),
        "continuityScore": round(continuity_score, 4),
        "caricaturePenalty": round(caricature_penalty, 4),
        "sourceOverlapPenalty": round(overlap_penalty, 4),
        "repetitionPenalty": round(repetition_penalty, 4),
        "voiceCollapsePenalty": round(voice_collapse_penalty, 4),
        "driftPenalty": round(drift_penalty, 4),
        "drift": drift,
        "diagnostics": {
            "templateHashes": features.get("template_hashes", []),
            "sentenceLength": features.get("sentence_length", {}),
            "overusedTokens": _overused_tokens(text),
            "longestCopiedSpan": _longest_overlap_span(text, source_texts),
        },
    }


def _stylometric_similarity(features: dict[str, Any], target: dict[str, Any]) -> float:
    if not target:
        return 0.7
    target_p50 = float(target.get("sentence_length", {}).get("p50", 0.0) or 0.0)
    actual_p50 = float(features.get("sentence_length", {}).get("p50", 0.0) or 0.0)
    target_p90 = float(target.get("sentence_length", {}).get("p90", 0.0) or 0.0)
    actual_p90 = float(features.get("sentence_length", {}).get("p90", 0.0) or 0.0)
    target_comma = float(target.get("punctuation", {}).get("comma_per_100_chars", 0.0) or 0.0)
    actual_comma = float(features.get("punctuation", {}).get("comma_per_100_chars", 0.0) or 0.0)
    target_turn = float(target.get("dialogue", {}).get("median_turn_chars", 0.0) or 0.0)
    actual_turn = float(features.get("dialogue", {}).get("median_turn_chars", 0.0) or 0.0)
    dist = abs(target_p50 - actual_p50) + abs(target_p90 - actual_p90) * 0.6 + abs(target_comma - actual_comma) * 2.5 + abs(target_turn - actual_turn) * 0.2
    return max(0.0, min(1.0, 1.0 - dist / 40.0))


def _repetition_penalty(text: str) -> float:
    sentences = [part.strip() for part in re.split(r"[。！？!?]", text) if part.strip()]
    if len(sentences) < 2:
        return 0.0
    prefixes = [re.sub(r"[^\w\u4e00-\u9fff]", "", sentence)[:10] for sentence in sentences]
    duplicates = len(prefixes) - len(set(prefixes))
    return min(1.0, duplicates / max(1, len(sentences) - 1))


def _source_overlap_penalty(text: str, source_texts: list[str]) -> float:
    if not source_texts:
        return 0.0
    target_grams = _ngrams(text, 8)
    if not target_grams:
        return 0.0
    best = 0.0
    for source in source_texts:
        source_grams = _ngrams(source, 8)
        if not source_grams:
            continue
        overlap = len(target_grams & source_grams) / max(1, len(target_grams))
        best = max(best, overlap)
    return min(1.0, best * 2.0)


def _continuity_score(text: str, previous_tail: str) -> float:
    if not previous_tail:
        return 0.8
    return 1.0 if _shares_anchor(previous_tail, text) else 0.7


def _shares_anchor(previous_tail: str, text: str) -> bool:
    anchors = [token for token in re.findall(r"[\u4e00-\u9fff]{2,4}", previous_tail[-80:]) if len(token) >= 2]
    return any(anchor in text for anchor in anchors[:6])


def _paragraph_similarity(text: str, target: dict[str, Any]) -> float:
    paragraphs = [p.strip() for p in re.split(r"\n{1,2}", text) if p.strip()]
    if not target:
        return 0.75
    target_dialogue = float(target.get("dialogue", {}).get("ratio", 0.0) or 0.0)
    actual_dialogue = text.count("“") / max(1, len(text))
    para_count_score = 1.0 if 1 <= len(paragraphs) <= 6 else 0.7
    dialogue_score = max(0.0, min(1.0, 1.0 - abs(target_dialogue - actual_dialogue) * 3))
    return round((para_count_score + dialogue_score) / 2, 4)


def _caricature_penalty(text: str) -> float:
    penalties = []
    for token in ("命运", "仿佛", "忽然", "笑了笑", "像是"):
        count = text.count(token)
        if count >= 3:
            penalties.append(min(1.0, count / 6))
    short_sentences = [part.strip() for part in re.split(r"[。！？!?]", text) if 0 < len(part.strip()) <= 6]
    if len(short_sentences) >= 4:
        penalties.append(min(1.0, len(short_sentences) / 10))
    return round(max(penalties) if penalties else 0.0, 4)


def _contains_actionable_progress(text: str) -> bool:
    return any(token in text for token in ("问", "说", "走", "看", "发现", "知道", "推开", "沉默"))


def _ngrams(text: str, n: int) -> set[str]:
    clean = re.sub(r"\s+", "", text)
    if len(clean) < n:
        return set()
    return {clean[i:i + n] for i in range(len(clean) - n + 1)}


def _voice_similarity(text: str, style_packet: dict[str, Any], features: dict[str, Any]) -> float:
    discourse_type = style_packet.get("scene_profile", {}).get("discourseType")
    if discourse_type == "dialogue":
        base = 1.0 if "“" in text else 0.65
    elif discourse_type in {"interior", "free_indirect"}:
        base = 0.95 if any(token in text for token in ("觉得", "心里", "想")) else 0.72
    else:
        base = 0.86
    target_ratio = float(style_packet.get("target_statistics", {}).get("dialogue", {}).get("ratio", 0.0) or 0.0)
    actual_ratio = float(features.get("dialogue", {}).get("ratio", 0.0) or 0.0)
    return max(0.0, min(1.0, base - abs(target_ratio - actual_ratio) * 0.8))


def _voice_collapse_penalty(text: str, style_packet: dict[str, Any]) -> float:
    discourse_type = style_packet.get("scene_profile", {}).get("discourseType")
    if discourse_type != "dialogue":
        return 0.0
    if "“" not in text:
        return 0.35
    tags = style_packet.get("diagnostics", {}) or {}
    relationship = str(tags.get("requestedRelationship", "") or "")
    if relationship and relationship not in text and text.count("说") >= 3:
        return 0.18
    return 0.08 if text.count("说") >= 4 else 0.0


def _drift_penalty(drift: dict[str, float]) -> float:
    return min(
        1.0,
        abs(drift.get("sentence_p50_delta", 0.0)) / 18
        + abs(drift.get("dialogue_ratio_delta", 0.0)) * 1.5
        + abs(drift.get("comma_density_delta", 0.0)) / 6,
    )


def _overused_tokens(text: str) -> list[str]:
    return [token for token in ("命运", "仿佛", "忽然", "像是", "笑了笑") if text.count(token) >= 3]


def _longest_overlap_span(text: str, source_texts: list[str]) -> int:
    clean = re.sub(r"\s+", "", text)
    best = 0
    for source in source_texts:
        other = re.sub(r"\s+", "", source)
        for width in range(min(len(clean), 24), 7, -1):
            if best >= width:
                break
            for start in range(0, max(0, len(clean) - width + 1)):
                span = clean[start:start + width]
                if span and span in other:
                    best = width
                    break
    return best
