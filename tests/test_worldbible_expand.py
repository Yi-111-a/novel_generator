"""Fix D：世界圣经扩写（非破坏式追加「详述」，治设定太薄）。"""
from __future__ import annotations

from novel_engine import db
from novel_engine.llm.base import LLMClient
from novel_engine.repository import Repository
from novel_engine.worldbible import expand_world_bible


class _ExpandLLM(LLMClient):
    def complete(self, s, u):
        return "霞飞路两侧梧桐成荫，" * 30          # 长扩写文本


def _repo() -> Repository:
    r = Repository(db.connect(":memory:"))
    r.add_bible_section("settingCore", "设定内核", "1939 上海孤岛。")        # 薄
    r.add_bible_section("geography", "地理", "法租界、七十六号、百乐门。")    # 薄
    r.add_bible_section("culture", "文化", "各扫门前雪。" * 40)              # 已丰富
    return r


def test_expand_thin_sections_appends_detail():
    r = _repo()
    n = expand_world_bible(r, _ExpandLLM(), theme="假面与忠诚")
    assert n >= 2                                       # settingCore + geography 被扩写
    secs = r.list_bible_sections()
    assert any(s["source"] == "llm_expanded" and s["section"] == "geography" for s in secs)
    # 原文仍在（永不覆写）
    assert any(s["body_full"] == "1939 上海孤岛。" for s in secs)


def test_rich_section_not_expanded():
    r = _repo()
    expand_world_bible(r, _ExpandLLM(), theme="")
    culture = [s for s in r.list_bible_sections() if s["section"] == "culture"]
    assert all(s["source"] != "llm_expanded" for s in culture)   # 已丰富的不动


def test_idempotent_and_offline():
    r = _repo()
    expand_world_bible(r, _ExpandLLM())
    before = len(r.list_bible_sections())
    expand_world_bible(r, _ExpandLLM())                 # 再调不重复加
    assert len(r.list_bible_sections()) == before
    assert expand_world_bible(r, llm=None) == 0         # 无 LLM 不做
