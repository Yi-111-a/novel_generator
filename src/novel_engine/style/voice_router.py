from __future__ import annotations

from typing import Any


def route_style_voice(*, speaker: str = "", pov: str = "", discourse_type: str = "narration",
                      listener: str = "", relationship: str = "", emotion: str = "",
                      scene_function: str = "") -> dict[str, Any]:
    weights = {
        "author_prior_weight": 0.30,
        "character_voice_weight": 0.05,
        "scene_register_weight": 0.20,
        "narrator_weight": 0.45,
        "current_arc_weight": 0.10,
    }
    if discourse_type == "dialogue":
        weights.update({
            "author_prior_weight": 0.20,
            "character_voice_weight": 0.55,
            "scene_register_weight": 0.20,
            "narrator_weight": 0.05,
        })
    elif discourse_type == "free_indirect":
        weights.update({
            "author_prior_weight": 0.25,
            "character_voice_weight": 0.30,
            "scene_register_weight": 0.15,
            "narrator_weight": 0.30,
        })
    elif discourse_type == "reflection":
        weights.update({
            "author_prior_weight": 0.20,
            "character_voice_weight": 0.45,
            "scene_register_weight": 0.20,
            "narrator_weight": 0.15,
        })
    elif discourse_type == "description":
        weights.update({
            "author_prior_weight": 0.30,
            "character_voice_weight": 0.05,
            "scene_register_weight": 0.20,
            "narrator_weight": 0.45,
        })
    primary = f"character_voice_{speaker}" if discourse_type in {"dialogue", "reflection", "interior"} and speaker else "narrator_default"
    secondary = f"{discourse_type}_{emotion or scene_function or 'neutral'}"
    return {
        "primary_profile": primary,
        "secondary_profile": secondary,
        "relationship": relationship,
        "listener": listener,
        "pov": pov,
        "scene_function": scene_function,
        "speaker": speaker,
        "discourse_type": discourse_type,
        **weights,
    }
