from __future__ import annotations

from typing import Any

from .distill import continuation_graph_summary
from ..repository import Repository
from ..style.corpus import extract_style_features


def _tail(text: str, limit: int = 1800) -> str:
    return (text or "")[-limit:]


def build_continuation_snapshot(repo: Repository) -> dict[str, Any]:
    meta = repo.get_continuation_meta()
    accepted = repo.list_accepted_chapters()
    source_chapters = repo.list_source_chapters()
    story_bible = repo.get_story_bible_record()
    latest_source = source_chapters[-1] if source_chapters else None
    latest_accepted = accepted[-1] if accepted else None
    recent_text = latest_accepted.prose if latest_accepted else (latest_source.text if latest_source else "")
    recent_features = extract_style_features(recent_text) if recent_text else {}
    style_segments = repo.list_style_segments()
    recent_segment_ids = [seg.id for seg in style_segments[-4:]]
    style_state = {
        "active_narrator_profile_id": "narrator_default",
        "active_pov_character_id": "",
        "scene_register": "neutral",
        "recent_style_segment_ids": recent_segment_ids,
        "recent_sentence_length_histogram": recent_features.get("sentence_length", {}),
        "recent_template_hashes": recent_features.get("template_hashes", []),
        "recent_metaphor_domains": [],
        "chapter_style_drift": {
            "dialogue_ratio": recent_features.get("dialogue", {}).get("ratio", 0.0),
            "comma_per_100_chars": recent_features.get("punctuation", {}).get("comma_per_100_chars", 0.0),
        },
    }
    life_model = repo.latest_author_life_model()
    world_config = story_bible.world_config_json if story_bible else {}
    graph_summary = continuation_graph_summary(repo)
    author_life_prior = {
        "id": life_model.model_id,
        "summary": life_model.summary,
        "core_wound": life_model.core_wound_json,
        "defense_patterns": life_model.defense_patterns_json[:4],
        "relationship_model": life_model.relationship_model_json,
    } if life_model else None
    if meta.write_mode == "new_series_book":
        return {
            "write_mode": meta.write_mode,
            "chapter_start_no": 1,
            "series_bible": (story_bible.world_config_json if story_bible else {}),
            "prequel_history_summary": (story_bible.timeline_json[-8:] if story_bible else []),
            "new_book_seed": meta.continuation_hint,
            "protagonist_strategy": meta.protagonist_strategy,
            "time_position": meta.time_position,
            "available_character_pool": (story_bible.characters_json if story_bible else []),
            "inherited_threads": (story_bible.open_threads_json if story_bible else []),
            "prev_tail": "",
            "source_tail_summary": latest_source.summary if latest_source else "",
            "world_config": world_config,
            "graph_summary": graph_summary,
            "style_state": style_state,
            "author_life_model_prior": author_life_prior,
        }
    return {
        "write_mode": meta.write_mode or "continue_current_book",
        "chapter_start_no": max(1, meta.latest_source_chapter_no + 1),
        "prev_tail": _tail(latest_accepted.prose if latest_accepted else (latest_source.text if latest_source else "")),
        "recent_source_summary": [c.summary for c in source_chapters[-3:] if c.summary],
        "world_config": world_config,
        "ending_state": (story_bible.last_state_json if story_bible else {}),
        "open_threads": (story_bible.open_threads_json if story_bible else []),
        "graph_summary": graph_summary,
        "continuation_hint": meta.continuation_hint,
        "style_state": style_state,
        "author_life_model_prior": author_life_prior,
    }
