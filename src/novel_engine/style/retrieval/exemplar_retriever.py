from __future__ import annotations

import re
from typing import Any

from ...models import StyleSegment


def retrieve_style_exemplars(
    segments: list[StyleSegment],
    *,
    discourse_type: str,
    scene_type: str,
    emotion: str = "",
    relationship: str = "",
    semantic_seed: str = "",
    target_voice_type: str = "",
    limit: int = 6,
) -> list[StyleSegment]:
    scored: list[tuple[float, StyleSegment]] = []
    for seg in segments:
        if not seg.enabled:
            continue
        voice_match = 1.0 if not target_voice_type else float(seg.voice_type == target_voice_type)
        discourse_match = float(seg.discourse_type == discourse_type)
        scene_match = float(seg.scene_type == scene_type)
        emotion_match = 1.0 if (emotion and emotion in (seg.emotion_json or [])) else 0.0
        relationship_match = 1.0 if (relationship and relationship in (seg.feature_json.get("relationship_tags", []) or [])) else 0.0
        semantic_similarity = _semantic_similarity(seg.text, semantic_seed)
        quality = min(1.0, max(seg.quality_score, seg.annotation_confidence))
        retrieval_score = (
            0.30 * voice_match
            + 0.20 * discourse_match
            + 0.15 * scene_match
            + 0.15 * emotion_match
            + 0.10 * relationship_match
            + 0.10 * semantic_similarity
            + 0.05 * quality
        )
        scored.append((retrieval_score, seg))
    scored.sort(key=lambda item: item[0], reverse=True)

    selected: list[StyleSegment] = []
    used_ids: set[str] = set()
    for _, seg in scored:
        if seg.id in used_ids:
            continue
        selected.append(seg)
        used_ids.add(seg.id)
        if len(selected) >= limit:
            break
    return selected


def _semantic_similarity(text: str, seed: str) -> float:
    if not seed.strip():
        return 0.0
    left = _keywords(text)
    right = _keywords(seed)
    if not left or not right:
        return 0.0
    return len(left & right) / max(1, len(left | right))


def _keywords(text: str) -> set[str]:
    normalized = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", " ", text or "")
    return {token for token in normalized.split() if len(token) >= 2}
