from __future__ import annotations

import math
import re
import uuid
from collections import Counter, defaultdict
from typing import Any

from ..models import StyleCluster, StyleSegment
from ..repository import Repository
from .segmentation import segment_chapter_style_units

_SENTENCE_SPLIT_RE = re.compile(r"[。！？!?]+")
_DIALOGUE_RE = re.compile(r"[“\"「『].+?[”\"」』]")
_FUNCTION_WORDS = ("的", "地", "得", "了", "着", "过", "却", "便", "竟", "倒", "于是", "原来", "呢", "吧", "啊", "嘛")


def build_style_corpus(repo: Repository, *, project_id: str = "") -> dict[str, Any]:
    repo.clear_style_corpus()
    segments: list[StyleSegment] = []
    chapters = repo.list_source_chapters()
    known_characters = {persona.agent_id: persona.name for persona in repo.list_personas() if persona.name}
    for chapter in chapters:
        segments.extend(
            segment_chapter_style_units(
                chapter,
                project_id=project_id,
                known_characters=known_characters,
            )
        )
    for segment in segments:
        repo.insert_style_segment(segment)
    clusters = _build_clusters(segments, project_id=project_id)
    for cluster in clusters:
        repo.insert_style_cluster(cluster)
    return {
        "segmentCount": len(segments),
        "clusterCount": len(clusters),
    }


def extract_style_features(text: str) -> dict[str, Any]:
    clean = (text or "").strip()
    sentence_lengths = _sentence_lengths(clean)
    clause_lengths = _clause_lengths(clean)
    paragraph_lengths = [len(p.strip()) for p in re.split(r"\n{1,2}", clean) if p.strip()]
    fn_counts = {token: clean.count(token) for token in _FUNCTION_WORDS}
    total_chars = max(1, len(clean))
    quote_chars = sum(len(match.group(0)) for match in _DIALOGUE_RE.finditer(clean))
    paragraphs = [p.strip() for p in re.split(r"\n{1,2}", clean) if p.strip()]
    paragraph_openers = Counter(_normalize_template(p[:10]) for p in paragraphs if p)
    paragraph_closers = Counter(_normalize_template(p[-10:]) for p in paragraphs if p)
    tokens = [clean[i:i + 2] for i in range(max(0, len(clean) - 1))]
    return {
        "sentence_length": _distribution(sentence_lengths),
        "clause_length": _distribution(clause_lengths),
        "paragraph_length": _distribution(paragraph_lengths),
        "punctuation": {
            "comma_per_100_chars": round(clean.count("，") * 100 / total_chars, 3),
            "dash_per_1000_chars": round(clean.count("—") * 1000 / total_chars, 3),
            "ellipsis_per_1000_chars": round(clean.count("…") * 1000 / total_chars, 3),
            "quote_ratio": round(quote_chars / total_chars, 3),
        },
        "dialogue": {
            "ratio": round(quote_chars / total_chars, 3),
            "turns": len(_DIALOGUE_RE.findall(clean)),
            "median_turn_chars": _median_quote_length(clean),
        },
        "function_words": fn_counts,
        "lexical": {
            "unique_char_ratio": round(len(set(clean)) / total_chars, 3),
            "avg_wordish_span": round(sum(sentence_lengths) / max(1, len(sentence_lengths)), 3),
            "four_char_density": round(len(re.findall(r"[\u4e00-\u9fff]{4}", clean)) / max(1, total_chars), 4),
        },
        "character_ngrams": Counter(tokens).most_common(20),
        "structure": {
            "paragraph_openers": paragraph_openers.most_common(8),
            "paragraph_closers": paragraph_closers.most_common(8),
        },
        "perception_verbs": _count_tokens(clean, ("看", "听", "闻", "感觉", "意识到")),
        "body_reaction": _count_tokens(clean, ("手", "肩", "呼吸", "心脏", "后背", "指尖")),
        "template_hashes": _template_hashes(clean),
    }


def _build_clusters(segments: list[StyleSegment], *, project_id: str) -> list[StyleCluster]:
    grouped: dict[tuple[str, str], list[StyleSegment]] = defaultdict(list)
    for seg in segments:
        grouped[(seg.discourse_type, seg.voice_type)].append(seg)
    clusters: list[StyleCluster] = []
    for (discourse_type, voice_type), items in grouped.items():
        items = sorted(items, key=lambda seg: seg.quality_score, reverse=True)
        feature_summary = _summarize_features([seg.feature_json for seg in items])
        reps = [seg.id for seg in items[:4]]
        clusters.append(StyleCluster(
            id=f"stycls_{uuid.uuid4().hex[:12]}",
            project_id=project_id,
            cluster_type=discourse_type,
            label=f"{discourse_type}:{voice_type}",
            centroid_key=reps[0] if reps else "",
            feature_summary_json=feature_summary,
            representative_segment_ids_json=reps,
        ))
    return clusters


def _summarize_features(features: list[dict[str, Any]]) -> dict[str, Any]:
    if not features:
        return {}
    sentence_p50 = [f.get("sentence_length", {}).get("p50", 0) for f in features]
    sentence_p90 = [f.get("sentence_length", {}).get("p90", 0) for f in features]
    dialogue_ratio = [f.get("dialogue", {}).get("ratio", 0.0) for f in features]
    comma_density = [f.get("punctuation", {}).get("comma_per_100_chars", 0.0) for f in features]
    med_turn = [f.get("dialogue", {}).get("median_turn_chars", 0.0) for f in features]
    return {
        "sentence_length": {
            "p50": round(sum(sentence_p50) / max(1, len(sentence_p50)), 3),
            "p90": round(sum(sentence_p90) / max(1, len(sentence_p90)), 3),
        },
        "dialogue_ratio": round(sum(dialogue_ratio) / max(1, len(dialogue_ratio)), 3),
        "comma_per_100_chars": round(sum(comma_density) / max(1, len(comma_density)), 3),
        "median_turn_chars": round(sum(med_turn) / max(1, len(med_turn)), 3),
    }


def _distribution(lengths: list[int]) -> dict[str, float]:
    if not lengths:
        return {"p10": 0.0, "p50": 0.0, "p90": 0.0, "mean": 0.0, "long_short_transition_rate": 0.0}
    ordered = sorted(lengths)
    long_short = 0
    for prev, cur in zip(lengths, lengths[1:]):
        if prev >= 28 and cur <= 12:
            long_short += 1
    return {
        "p10": _quantile(ordered, 0.10),
        "p50": _quantile(ordered, 0.50),
        "p90": _quantile(ordered, 0.90),
        "mean": round(sum(lengths) / len(lengths), 3),
        "long_short_transition_rate": round(long_short / max(1, len(lengths) - 1), 3),
    }


def _quantile(values: list[int], q: float) -> float:
    if not values:
        return 0.0
    idx = min(len(values) - 1, max(0, int(math.floor((len(values) - 1) * q))))
    return float(values[idx])


def _sentence_lengths(text: str) -> list[int]:
    return [len(part.strip()) for part in _SENTENCE_SPLIT_RE.split(text) if part.strip()]


def _clause_lengths(text: str) -> list[int]:
    return [len(part.strip()) for part in re.split(r"[，、；：]", text) if part.strip()]


def _template_hashes(text: str) -> list[str]:
    hashes: list[str] = []
    for sentence in [s.strip() for s in _SENTENCE_SPLIT_RE.split(text) if s.strip()]:
        normalized = _normalize_template(sentence)[:14]
        if normalized:
            hashes.append(normalized)
    return hashes[:8]


def _normalize_template(text: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]", "", text)


def _median_quote_length(text: str) -> float:
    lengths = [len(match.group(0).strip("“”\"「」『』")) for match in _DIALOGUE_RE.finditer(text)]
    if not lengths:
        return 0.0
    ordered = sorted(lengths)
    return float(ordered[len(ordered) // 2])


def _count_tokens(text: str, tokens: tuple[str, ...]) -> dict[str, int]:
    return {token: text.count(token) for token in tokens if token in text}
