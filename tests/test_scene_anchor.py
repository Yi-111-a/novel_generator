"""关键场景档案（捕获/快照）。跨章一致性审计已统一由 fact_delta 负责，本模块只捕获。"""
from __future__ import annotations

from novel_engine import db
from novel_engine.llm.base import LLMClient
from novel_engine.models import ChapterPlan, Entity
from novel_engine.narration.scene_anchor import capture_scene_anchors
from novel_engine.repository import Repository


def _repo() -> Repository:
    repo = Repository(db.connect(":memory:"))
    repo.insert_entity(Entity("hero", "character", "陈野"))
    return repo


class _CaptureLLM(LLMClient):
    def __init__(self, scenes_json: str = "") -> None:
        self.scenes_json = scenes_json

    def complete(self, system: str, user: str) -> str:
        if "连续性档案员" in system:
            return self.scenes_json or '{"scenes":[]}'
        return "{}"


_SCENE = '{"scenes":[{"name":"锦澜湾17号别墅地下室","kind":"crime_scene","facts":["位于锦澜湾，紧邻18号别墅"]}]}'


def test_capture_first_locks_then_only_appends():
    repo = _repo()
    ch4 = ChapterPlan("c4", "a", 4, cast=["hero"])
    capture_scene_anchors(repo, ch4, "陈野走进锦澜湾17号别墅地下室。", _CaptureLLM(scenes_json=_SCENE))

    anchors = repo.list_scene_anchors()
    assert len(anchors) == 1
    assert anchors[0].established_chapter == 4
    assert anchors[0].canonical_facts == ["位于锦澜湾，紧邻18号别墅"]

    # 第5章再次捕获：追加新事实，已锁定的不被覆盖，established_chapter 不变。
    llm2 = _CaptureLLM(
        scenes_json='{"scenes":[{"name":"锦澜湾17号别墅地下室","kind":"crime_scene",'
        '"facts":["位于锦澜湾，紧邻18号别墅","西北角有浇筑隔墙藏尸"]}]}'
    )
    ch5 = ChapterPlan("c5", "a", 5, cast=["hero"])
    capture_scene_anchors(repo, ch5, "地下室和记忆里一样。", llm2)

    anchors = repo.list_scene_anchors()
    assert len(anchors) == 1
    assert anchors[0].established_chapter == 4  # 仍是首次确立的章
    assert anchors[0].canonical_facts == ["位于锦澜湾，紧邻18号别墅", "西北角有浇筑隔墙藏尸"]


def test_capture_noop_without_llm():
    repo = _repo()
    ch = ChapterPlan("c5", "a", 5, cast=["hero"])
    assert capture_scene_anchors(repo, ch, "随便写点。", None) == []
    assert repo.list_scene_anchors() == []
