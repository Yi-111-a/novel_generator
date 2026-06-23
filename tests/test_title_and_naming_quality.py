from __future__ import annotations

from novel_engine.chapter_titles import (
    is_near_duplicate_title,
    repair_chapter_title,
    validate_chapter_title,
)
from novel_engine.naming import validate_character_name


def test_title_validation_flags_duplicate_and_template():
    result = validate_chapter_title("第1章", ["第1章", "雾中灯"])
    assert result.duplicate is True
    assert result.template is True
    assert result.ok is False


def test_title_validation_flags_near_duplicate():
    assert is_near_duplicate_title("三锁铁匣", ["三锁铁箱"]) is True


def test_repair_title_falls_back_to_unique_chapter_number():
    repaired = repair_chapter_title("", 3, ["第3章"])
    assert repaired == "第3章-2"


def test_character_name_validation_flags_bad_and_similar_names():
    result = validate_character_name("薇拉·锈指", ["薇拉·锈风"])
    assert result.similar is True
    assert result.bad is True
    assert result.ok is False
