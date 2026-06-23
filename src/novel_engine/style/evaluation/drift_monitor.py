from __future__ import annotations

from typing import Any

from ..corpus import extract_style_features


def compute_style_drift(text: str, *, target_statistics: dict[str, Any] | None = None) -> dict[str, float]:
    target_statistics = target_statistics or {}
    features = extract_style_features(text)
    target_sentence = target_statistics.get("sentence_length", {}) or {}
    target_dialogue = target_statistics.get("dialogue", {}) or {}
    target_punct = target_statistics.get("punctuation", {}) or {}
    return {
        "sentence_p50_delta": round(
            float(features.get("sentence_length", {}).get("p50", 0.0)) - float(target_sentence.get("p50", 0.0)),
            4,
        ),
        "sentence_p90_delta": round(
            float(features.get("sentence_length", {}).get("p90", 0.0)) - float(target_sentence.get("p90", 0.0)),
            4,
        ),
        "dialogue_ratio_delta": round(
            float(features.get("dialogue", {}).get("ratio", 0.0)) - float(target_dialogue.get("ratio", 0.0)),
            4,
        ),
        "comma_density_delta": round(
            float(features.get("punctuation", {}).get("comma_per_100_chars", 0.0)) - float(target_punct.get("comma_per_100_chars", 0.0)),
            4,
        ),
    }
