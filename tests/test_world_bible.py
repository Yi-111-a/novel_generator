"""§12 世界圣经全量存档：逐节原文入库、按节检索、全文喂入（不摘要）。"""
from __future__ import annotations

from novel_engine import db
from novel_engine.repository import Repository


def _repo() -> Repository:
    return Repository(db.connect(":memory:"))


def test_add_list_sections_full_text():
    r = _repo()
    r.add_bible_section("geography", "北境", "千里冰原，朔风裂石，唯有黑曜关一线通往南域。")
    r.add_bible_section("factions", "三宗", "玄阴、太虚、流火三宗鼎立，互不stat统属。")
    secs = r.list_bible_sections()
    assert len(secs) == 2
    geo = r.list_bible_sections("geography")
    assert len(geo) == 1 and "冰原" in geo[0]["body_full"]


def test_empty_body_not_stored():
    r = _repo()
    r.add_bible_section("history", "", "   ")
    assert r.list_bible_sections() == []


def test_sections_text_filters_and_caps():
    r = _repo()
    r.add_bible_section("geography", "北境", "冰原" * 100)
    r.add_bible_section("factions", "三宗", "宗门" * 100)
    only_geo = r.bible_sections_text(["geography"])
    assert "冰原" in only_geo and "宗门" not in only_geo
    capped = r.bible_sections_text(None, max_chars=50)
    assert len(capped) <= 60  # 含分节标题略有余量，但被裁到 max_chars 附近


def test_seedbuilder_stores_full_sections():
    from server import seedbuilder

    draft = {
        "worldBible": {
            "settingCore": "一个魂力枯竭的末法时代。",
            "geography": "北境冰原与南域火湖隔黑曜关相望。",
            "culture": "三宗鼎立，凡人附庸。",
            "physicsRules": ["魂力不可凭空增减", "誓约即律法"],
            "theme": "在枯竭中寻一线生机",
            "genre": "玄幻",
        },
        "personas": [
            {"id": "hero", "name": "云鹤子", "want": "求道", "motifObjects": []},
            {"id": "ally", "name": "季拾遗", "want": "护道", "motifObjects": []},
        ],
    }
    repo = seedbuilder.build_repo_from_draft(draft, db_path=":memory:")
    secs = repo.list_bible_sections()
    sections = {s["section"] for s in secs}
    assert {"settingCore", "geography", "culture", "rules"} <= sections
    text = repo.bible_sections_text(["geography"])
    assert "冰原" in text
    # 规则节把列表逐行落全
    rules_text = repo.bible_sections_text(["rules"])
    assert "誓约即律法" in rules_text
