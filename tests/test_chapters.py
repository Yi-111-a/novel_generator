"""章节分组（高潮处断章）单元测试。直接测纯函数 group_chapters。"""
import sys
from pathlib import Path

# 让 server 包可导入（server 依赖 src 下的 novel_engine）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from server.projects import group_chapters, _num_cn  # noqa: E402


def test_break_at_peak():
    # 张力序列：低 低 高 → 第一章收在高潮(第3场)
    rows = [("s1", 0.3), ("s2", 0.4), ("s3", 0.9)]
    chs = group_chapters(rows)
    assert len(chs) == 1
    assert chs[0]["status"] == "done"
    assert chs[0]["sceneIds"] == ["s1", "s2", "s3"]
    assert chs[0]["climaxSceneId"] == "s3"


def test_min_length_prevents_single_scene_chapter():
    # 第一场就是高潮，但章长 < 2 → 不断章
    rows = [("s1", 0.95), ("s2", 0.3)]
    chs = group_chapters(rows)
    # 不应在 s1 处断章；整体成一个进行中的章
    assert len(chs) == 1
    assert chs[0]["status"] == "ongoing"


def test_multiple_chapters_and_ongoing_tail():
    rows = [("a", 0.3), ("b", 0.9), ("c", 0.4), ("d", 0.8), ("e", 0.5)]
    chs = group_chapters(rows)
    assert [c["status"] for c in chs] == ["done", "done", "ongoing"]
    assert chs[0]["sceneIds"] == ["a", "b"]
    assert chs[1]["sceneIds"] == ["c", "d"]
    assert chs[2]["sceneIds"] == ["e"]


def test_titles_are_chinese_numerals():
    assert _num_cn(1) == "一" and _num_cn(10) == "十" and _num_cn(11) == "十一" and _num_cn(23) == "二十三"


def test_empty():
    assert group_chapters([]) == []
