"""§5 final cut：杀青后 in medias res——最高潮场作为序章钩子前置。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from novel_engine import db  # noqa: E402
from novel_engine.models import Scene  # noqa: E402
from novel_engine.repository import Repository  # noqa: E402
from server.projects import Project  # noqa: E402


def _project_with_scenes() -> Project:
    p = Project("定稿测试")
    p.repo = Repository(db.connect(":memory:"))
    p.repo.insert_scene(Scene("s1", 1, ["e1"], pov="hero", target_tension=0.3, prose_text="平淡开场"))
    p.repo.insert_scene(Scene("s2", 2, ["e2"], pov="hero", target_tension=0.9, prose_text="高潮对峙"))
    p.repo.insert_scene(Scene("s3", 3, ["e3"], pov="hero", target_tension=0.5, prose_text="余波"))
    p.status = "writing"
    return p


def test_finalize_sets_completed_and_prologue():
    p = _project_with_scenes()
    res = p.finalize()
    assert res["ok"] and p.status == "completed"
    chs = p.chapters()
    # 第一项是序章钩子，指向最高潮场 s2（in medias res）
    assert chs[0].get("isPrologue") and chs[0]["sceneIds"] == ["s2"]
    p.dispose()


def test_finalize_refuses_without_scenes():
    p = Project("空")
    p.repo = Repository(db.connect(":memory:"))
    p.status = "writing"
    res = p.finalize()
    assert not res["ok"]
    assert p.status == "writing"  # 未成稿不改状态
    p.dispose()
