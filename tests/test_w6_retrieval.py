"""W6 RAG 注入：子图检索 + token 预算 + 关键词触发 + 分级排序。"""
from __future__ import annotations

from novel_engine import db
from novel_engine.models import (
    CharacterCard, Entity, Faction, GraphEdge, Location,
)
from novel_engine.repository import Repository
from novel_engine.narration.retrieval import (
    build_context, scene_seeds, _expand_seeds, _keyword_scan,
    _card_snippet, _location_snippet, _faction_snippet,
)


def _seeded_repo() -> Repository:
    """W3+W4+W5 完整数据：人物、势力、地点、图谱边。"""
    r = Repository(db.connect(":memory:"))
    # 地点
    r.insert_entity(Entity("loc_a", "location", "百乐门", {"canon": True}))
    r.insert_entity(Entity("loc_b", "location", "霞飞路", {"canon": True}))
    r.upsert_location(Location(loc_id="loc_a", name="百乐门",
                                summary="上海最著名的舞厅", detail="位于静安寺路，百乐门舞厅灯火通明" * 5,
                                culture_local="夜夜笙歌的名利场"))
    r.upsert_location(Location(loc_id="loc_b", name="霞飞路",
                                summary="法租界繁华大道", detail="霞飞路两旁梧桐成荫" * 5))
    # 势力
    r.upsert_faction(Faction(faction_id="fac_a", name="孤光社",
                              summary="地下抗日情报组织", ideology="抗日救亡",
                              goals="收集日伪情报", methods="渗透伪政权",
                              detail="孤光社是一个秘密组织" * 5,
                              territory=["loc_b"],
                              relations=[{"target_faction_id": "fac_b", "kind": "hostile",
                                          "intensity": 5, "note": "死敌"}],
                              source="w3"))
    r.upsert_faction(Faction(faction_id="fac_b", name="七十六号",
                              summary="汪伪特务机构",
                              detail="七十六号是日伪的情报机关" * 3,
                              territory=["loc_a"], relations=[], source="w3"))
    # 人物
    r.insert_entity(Entity("char_1", "character", "沈砚", {"faction_id": "fac_a"}))
    r.insert_entity(Entity("char_2", "character", "郑沉舟", {"faction_id": "fac_b"}))
    r.insert_entity(Entity("char_3", "character", "苏静", {}))
    r.add_card(CharacterCard(card_id="card_1", agent_id="char_1", tier="lead",
                              name="沈砚", one_liner="伪装成舞女的情报员",
                              appearance="身材高挑，面容冷峻", social_role="舞女/情报员",
                              psychology="外冷内热，背负家仇", backstory="出身书香门第" * 10,
                              arc="从被动接受任务到主动承担责任"))
    r.add_card(CharacterCard(card_id="card_2", agent_id="char_2", tier="supporting",
                              name="郑沉舟", one_liner="七十六号的冷血特务",
                              defining_trait="冷酷无情", backstory="曾是革命青年"))
    r.add_card(CharacterCard(card_id="card_3", agent_id="char_3", tier="supporting",
                              name="苏静", one_liner="报社女记者",
                              defining_trait="正义感强", backstory="留洋归来"))
    # W1 世界圣经
    r.add_bible_section("settingCore", "故事背景", "1939年上海孤岛时期" * 10,
                        source="w1", summary="1939年上海，租界被日占区包围，是为孤岛")
    r.add_bible_section("geography", "地理", "上海法租界" * 10,
                        source="w1", summary="法租界是故事主要发生地")
    # W5 图谱边
    r.upsert_edge(GraphEdge(src="char_1", rel="member_of", dst="fac_a", intensity=0.7))
    r.upsert_edge(GraphEdge(src="fac_a", rel="has_member", dst="char_1", intensity=0.7))
    r.upsert_edge(GraphEdge(src="char_2", rel="member_of", dst="fac_b", intensity=0.7))
    r.upsert_edge(GraphEdge(src="fac_a", rel="hostile", dst="fac_b", intensity=1.0))
    r.upsert_edge(GraphEdge(src="fac_a", rel="controls", dst="loc_b", intensity=0.7))
    r.upsert_edge(GraphEdge(src="char_1", rel="related_to", dst="char_2",
                             intensity=0.9, last_active_chapter=3))
    r.upsert_edge(GraphEdge(src="char_1", rel="related_to", dst="char_3",
                             intensity=0.3, last_active_chapter=1))
    return r


# ---- 种子扩展 ----

def test_expand_seeds_finds_neighbors():
    r = _seeded_repo()
    expanded = _expand_seeds(r, {"char_1"}, hops=1)
    ids = [eid for eid, _ in expanded]
    assert "char_2" in ids  # related_to
    assert "fac_a" in ids   # member_of


def test_expand_seeds_empty_on_no_graph():
    r = Repository(db.connect(":memory:"))
    assert _expand_seeds(r, {"x"}) == []


def test_expand_seeds_respects_intensity_order():
    r = _seeded_repo()
    expanded = _expand_seeds(r, {"char_1"}, hops=1)
    intensities = [i for _, i in expanded]
    assert intensities == sorted(intensities, reverse=True)


# ---- 关键词触发 ----

def test_keyword_scan_matches_entity_names():
    r = _seeded_repo()
    hits = _keyword_scan(r, "沈砚和郑沉舟在百乐门碰面", exclude=set())
    assert "char_1" in hits
    assert "char_2" in hits
    assert "loc_a" in hits


def test_keyword_scan_respects_exclude():
    r = _seeded_repo()
    hits = _keyword_scan(r, "沈砚在百乐门", exclude={"char_1"})
    assert "char_1" not in hits
    assert "loc_a" in hits


# ---- snippet 提取 ----

def test_card_snippet_lead_detailed():
    r = _seeded_repo()
    s = _card_snippet(r, "char_1")
    assert "沈砚" in s
    assert "外貌" in s or "外冷内热" in s  # lead has 三维度
    assert "弧线" in s


def test_card_snippet_supporting_condensed():
    r = _seeded_repo()
    s = _card_snippet(r, "char_2")
    assert "郑沉舟" in s
    assert "特质" in s


def test_location_snippet():
    r = _seeded_repo()
    s = _location_snippet(r, "loc_a")
    assert "百乐门" in s
    assert "灯火通明" in s or "舞厅" in s


def test_faction_snippet():
    r = _seeded_repo()
    s = _faction_snippet(r, "fac_a")
    assert "孤光社" in s
    assert "抗日" in s


# ---- build_context 主入口 ----

def test_build_context_includes_seed_entities():
    r = _seeded_repo()
    ctx = build_context(r, {"char_1", "loc_a"}, budget=5000)
    assert "沈砚" in ctx
    assert "百乐门" in ctx


def test_build_context_includes_bible_summary():
    r = _seeded_repo()
    ctx = build_context(r, set(), budget=5000)
    assert "孤岛" in ctx or "1939" in ctx


def test_build_context_includes_faction_summary():
    r = _seeded_repo()
    ctx = build_context(r, set(), budget=5000)
    assert "孤光社" in ctx or "七十六号" in ctx


def test_build_context_budget_truncation():
    r = _seeded_repo()
    ctx_small = build_context(r, {"char_1", "char_2", "loc_a", "fac_a"}, budget=200)
    ctx_large = build_context(r, {"char_1", "char_2", "loc_a", "fac_a"}, budget=10000)
    assert len(ctx_small) <= 250  # some slack for formatting
    assert len(ctx_large) > len(ctx_small)


def test_build_context_keyword_trigger():
    r = _seeded_repo()
    ctx = build_context(r, set(), budget=5000, beat_text="苏静在霞飞路采访")
    assert "苏静" in ctx
    assert "霞飞路" in ctx


def test_build_context_graph_expansion():
    r = _seeded_repo()
    ctx = build_context(r, {"char_1"}, budget=5000, hops=1)
    # char_2 is neighbor via related_to with high intensity
    assert "郑沉舟" in ctx


def test_build_context_no_duplicates():
    r = _seeded_repo()
    # char_1 is both seed and keyword match — should appear only once
    ctx = build_context(r, {"char_1"}, budget=5000, beat_text="沈砚出场")
    count = ctx.count("【沈砚】")
    assert count == 1


def test_build_context_empty_repo():
    r = Repository(db.connect(":memory:"))
    ctx = build_context(r, set(), budget=1000)
    assert ctx == "" or len(ctx) < 10


def test_build_context_no_bible_summary():
    r = _seeded_repo()
    ctx = build_context(r, {"char_1"}, budget=5000, include_bible_summary=False)
    # Should not have the bible overview block
    assert "世界观速览" not in ctx


# ---- scene_seeds ----

def test_scene_seeds_from_chapter():
    from novel_engine.models import ChapterPlan
    ch = ChapterPlan(chapter_id="ch1", arc_id="arc1", sequence_order=1,
                     cast=["char_1", "char_2"], location_ids=["loc_a"])
    s = scene_seeds(ch, pov="char_1")
    assert "char_1" in s
    assert "char_2" in s
    assert "loc_a" in s


# ---- 优先级排序 ----

def test_priority_order_bible_before_graph():
    """常驻层（P0）应排在图谱层（P4）之前。"""
    r = _seeded_repo()
    ctx = build_context(r, {"char_1"}, budget=5000)
    # Bible summary should appear before graph expansion
    bible_pos = ctx.find("世界观速览") if "世界观速览" in ctx else ctx.find("1939")
    if bible_pos >= 0:
        graph_pos = ctx.find("图谱(")
        if graph_pos >= 0:
            assert bible_pos < graph_pos
