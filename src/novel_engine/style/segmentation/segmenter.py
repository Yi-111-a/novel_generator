from __future__ import annotations

import uuid

from ...models import SourceChapter, StyleSegment
from .dialogue_parser import split_dialogue_turns, split_paragraph_units
from .discourse_classifier import (
    classify_discourse_type,
    classify_emotion,
    classify_register,
    classify_scene_type,
    classify_voice_type,
    estimate_annotation_confidence,
    guess_character_id,
)


def segment_chapter_style_units(
    chapter: SourceChapter,
    *,
    project_id: str,
    known_characters: dict[str, str] | None = None,
    default_pov_character_id: str = "",
) -> list[StyleSegment]:
    paragraphs = split_paragraph_units(chapter.text or "")
    if not paragraphs and (chapter.text or "").strip():
        paragraphs = [(chapter.text or "").strip()]
    known_characters = known_characters or {}
    out: list[StyleSegment] = []
    cursor = 0
    buffer = ""
    buffer_start = 0
    for paragraph in paragraphs:
        para_start = (chapter.text or "").find(paragraph, cursor)
        if para_start < 0:
            para_start = cursor
        cursor = para_start + len(paragraph)
        turns = split_dialogue_turns(paragraph)
        if turns and len(paragraph) <= 500:
            out.append(
                _make_segment(
                    chapter=chapter,
                    project_id=project_id,
                    known_characters=known_characters,
                    default_pov_character_id=default_pov_character_id,
                    start=para_start,
                    end=para_start + len(paragraph),
                    text=paragraph,
                )
            )
            continue
        candidate = paragraph if not buffer else f"{buffer}\n{paragraph}"
        if not buffer:
            buffer_start = para_start
        if len(candidate) < 100 and cursor < len(chapter.text or ""):
            buffer = candidate
            continue
        text = candidate[:500]
        out.append(
            _make_segment(
                chapter=chapter,
                project_id=project_id,
                known_characters=known_characters,
                default_pov_character_id=default_pov_character_id,
                start=buffer_start,
                end=buffer_start + len(text),
                text=text,
            )
        )
        buffer = ""
    if buffer:
        out.append(
            _make_segment(
                chapter=chapter,
                project_id=project_id,
                known_characters=known_characters,
                default_pov_character_id=default_pov_character_id,
                start=buffer_start,
                end=buffer_start + len(buffer),
                text=buffer[:500],
            )
        )
    return out


def _make_segment(
    *,
    chapter: SourceChapter,
    project_id: str,
    known_characters: dict[str, str],
    default_pov_character_id: str,
    start: int,
    end: int,
    text: str,
) -> StyleSegment:
    from ..corpus import extract_style_features

    discourse_type = classify_discourse_type(text)
    voice_type = classify_voice_type(text, discourse_type)
    character_id = guess_character_id(text, known_characters)
    feature_json = extract_style_features(text)
    return StyleSegment(
        id=f"styseg_{uuid.uuid4().hex[:12]}",
        project_id=project_id or chapter.project_id,
        source_chapter_id=chapter.id,
        start_offset=start,
        end_offset=end,
        text=text,
        voice_type=voice_type,
        character_id=character_id,
        pov_character_id=default_pov_character_id or character_id,
        discourse_type=discourse_type,
        scene_type=classify_scene_type(text, discourse_type),
        emotion_json=classify_emotion(text),
        register_type=classify_register(text, discourse_type),
        feature_json=feature_json,
        quality_score=min(1.0, max(0.2, len(text) / 280.0)),
        annotation_confidence=estimate_annotation_confidence(text, discourse_type, voice_type),
        enabled=True,
    )
