"""回归：MCP 可读章节必须与已采纳章节 1:1 对齐。

历史 bug：_readable_chapters 曾按 target_tension 峰值把场景流重新分章，
在“一章=一场景”的生成模型下把 N 章错并成少数几章（实测把 20 章并成 3 章）。
本测试用“多数章节张力低于旧阈值 0.66”的场景复现该条件，断言现在每个已采纳
章节各自成一章、且 index 等于 chapter_no。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import novel_engine.db as db
from novel_engine.models import AcceptedChapterRecord, Scene
from novel_engine.repository import Repository

# mcp_server 不在 src 下，单独加入路径
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mcp_server"))
from novelworld_mcp import _readable_chapters  # noqa: E402


def _seed_chapters(repo: Repository, tensions: list[float]) -> None:
    """每章写入 1 行 accepted_chapters + 1 个同序场景，模拟 ChapterIndexer.accept。"""
    for i, tension in enumerate(tensions, 1):
        repo.insert_accepted_chapter(
            AcceptedChapterRecord(
                project_id="p",
                draft_id=f"d{i}",
                chapter_no=i,
                title=f"第{i}章",
                prose=f"正文{i}",
                summary="",
                created_at="2026-01-01T00:00:00+00:00",
            )
        )
        repo.insert_scene(
            Scene(
                scene_id=f"sc{i}",
                discourse_order=i,
                source_events=[],
                pov="",
                target_tension=tension,
                prose_text=f"正文{i}",
                newly_revealed=[],
            )
        )


def test_readable_chapters_one_per_accepted_even_with_low_tension():
    repo = Repository(db.connect(":memory:"))
    # 还原触发旧 bug 的张力曲线：多数 <0.66，仅个别峰值。
    tensions = [0.30, 0.42, 0.43, 0.48, 0.49, 0.53, 0.57, 0.78, 0.61, 0.92]
    _seed_chapters(repo, tensions)

    chapters = _readable_chapters(repo)

    assert len(chapters) == 10, "可读章数必须等于已采纳章数，而非按张力合并"
    assert [c["index"] for c in chapters] == list(range(1, 11))
    assert [c["status"] for c in chapters] == ["done"] * 10
    # 正文取自 accepted_chapters，而非拼接的场景流
    assert chapters[0]["prose"] == "正文1"
    assert chapters[-1]["prose"] == "正文10"


def test_readable_chapters_fallback_to_scene_stream_when_no_accepted():
    repo = Repository(db.connect(":memory:"))
    for i in (1, 2, 3):
        repo.insert_scene(
            Scene(
                scene_id=f"sc{i}",
                discourse_order=i,
                source_events=[],
                pov="",
                target_tension=0.4,
                prose_text=f"草稿{i}",
                newly_revealed=[],
            )
        )

    chapters = _readable_chapters(repo)

    # 无已采纳章节时，回退为“每个场景自成一章”，仍 1:1。
    assert len(chapters) == 3
    assert [c["index"] for c in chapters] == [1, 2, 3]
    assert chapters[1]["prose"] == "草稿2"
