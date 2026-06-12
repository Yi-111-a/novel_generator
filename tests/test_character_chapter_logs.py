from __future__ import annotations

import sys
from pathlib import Path

from novel_engine import db
from novel_engine.models import CharacterChapterLog, Entity, Persona
from novel_engine.repository import Repository

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from server.dossier import build_markdown  # noqa: E402


def test_character_chapter_logs_merge_and_truncate():
    repo = Repository(db.connect(":memory:"))
    repo.insert_entity(Entity("hero", "character", "陆沉", {}))
    repo.insert_persona(Persona(agent_id="hero", name="陆沉"))

    repo.insert_character_log(CharacterChapterLog(
        agent_id="hero", chapter_seq=1, actions="献祭怀表", psychology="动摇", intention="寻找入口",
        items_changed=["怀表"],
    ))
    repo.insert_character_log(CharacterChapterLog(
        agent_id="hero", chapter_seq=1, actions="穿过通道", psychology="重新稳住", intention="继续下探",
        items_changed=["通道钥匙"],
    ))
    repo.insert_character_log(CharacterChapterLog(
        agent_id="hero", chapter_seq=2, actions="试探向导", psychology="开始怀疑", intention="暗中跟踪",
    ))

    logs = repo.get_character_logs("hero", last_n=1)
    assert [log.chapter_seq for log in logs] == [2]

    logs = repo.get_character_logs("hero", last_n=5)
    assert [log.chapter_seq for log in logs] == [1, 2]
    assert logs[0].actions == "献祭怀表；穿过通道"
    assert logs[0].psychology == "动摇；重新稳住"
    assert logs[0].intention == "继续下探"
    assert logs[0].items_changed == ["怀表", "通道钥匙"]


def test_dossier_includes_recent_character_logs():
    repo = Repository(db.connect(":memory:"))
    repo.insert_entity(Entity("hero", "character", "陆沉", {}))
    repo.insert_persona(Persona(agent_id="hero", name="陆沉", want="活下去"))
    repo.insert_character_log(CharacterChapterLog(
        agent_id="hero", chapter_seq=3, actions="献祭怀表", psychology="动摇但坚定", intention="寻找第五回廊入口",
        items_changed=["怀表"],
    ))

    md = build_markdown(repo, "hero", chapter=3)
    assert "近期轨迹" in md
    assert "第 3 章" in md
    assert "动摇但坚定" in md
