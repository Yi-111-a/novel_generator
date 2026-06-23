"""段落节奏：把"一大坨"描写段确定性切成均匀短段；对白/已短的段不动。"""
from __future__ import annotations

from novel_engine.story_bible.chapter_writer import (
    _split_long_paragraph,
    ChapterWriter,
)


def test_short_paragraph_untouched():
    p = "他推开门。屋里没人。"
    assert _split_long_paragraph(p) == [p]


def test_long_descriptive_block_is_split():
    block = (
        "他贴着围墙走了半圈。墙顶插着碎玻璃。东北角有个缺口。"
        "他侧身钻进去。脚下踩到一团湿纸。鞋底黏糊糊的。"
        "院子里停着一辆面包车。车身蒙着灰。后门没锁紧。"
    )
    out = _split_long_paragraph(block, max_sent=3)
    assert len(out) >= 3                     # 被切成多段
    assert "".join(out) == block             # 不丢字、不改字
    assert all(s.count("。") <= 3 for s in out)


def test_dialogue_starts_new_paragraph():
    block = '他停在门口。屋里很暗。“有人吗？”他低声问。没有回答。'
    out = _split_long_paragraph(block, max_sent=2)
    # 引号开头的那句应自成一段（对白分行）
    assert any(s.startswith("“") for s in out)


def test_clean_generated_prose_breaks_wall():
    wall = "。".join([f"第{i}个动作他做完了" for i in range(1, 9)]) + "。"
    cleaned = ChapterWriter._clean_generated_prose(wall)
    paras = [p for p in cleaned.split("\n\n") if p.strip()]
    assert len(paras) >= 2                    # 墙被打散成多段
    assert "".join(paras) == wall.replace("\n", "")
