"""W5 知识图谱：静态边 + 注意力 bump + 衰减 + 邻居检索。"""
from __future__ import annotations

from novel_engine import db
from novel_engine.models import Entity, Faction, GraphEdge, Location
from novel_engine.repository import Repository
from novel_engine.worldbible import build_static_graph


def _repo_w3w4_seeded() -> Repository:
    """模拟 W3+W4 落库后的状态：势力、territory、relations、人物 entity 带 faction_id。"""
    r = Repository(db.connect(":memory:"))
    # 两个 canon 地点 + 父子层级
    r.insert_entity(Entity("loc_a", "location", "百乐门", {"canon": True}))
    r.insert_entity(Entity("loc_b", "location", "霞飞路", {"canon": True}))
    r.upsert_location(Location(loc_id="loc_a", name="百乐门", parent="loc_b"))
    r.upsert_location(Location(loc_id="loc_b", name="霞飞路"))
    # 两个势力 + 敌对关系
    r.upsert_faction(Faction(faction_id="fac_a", name="孤光社",
                             territory=["loc_b"],
                             relations=[{"target_faction_id": "fac_b", "kind": "hostile",
                                         "intensity": 5, "note": "死敌"}],
                             source="w3"))
    r.upsert_faction(Faction(faction_id="fac_b", name="七十六号",
                             territory=["loc_a"], relations=[], source="w3"))
    # 人物 → 势力（W3 落卡时写 faction_id）
    r.insert_entity(Entity("char_1", "character", "沈砚", {"faction_id": "fac_a"}))
    r.insert_entity(Entity("char_2", "character", "郑沉舟", {"faction_id": "fac_b"}))
    r.insert_entity(Entity("char_3", "character", "苏静", {}))   # 无势力
    return r


# ---------------- 静态边生成 ----------------
def test_build_static_graph_creates_all_edges():
    r = _repo_w3w4_seeded()
    res = build_static_graph(r)
    assert res["edges"] >= 7
    edges = r.list_edges()
    rels = {(e.src, e.rel, e.dst) for e in edges}
    # 人 → 势力
    assert ("char_1", "member_of", "fac_a") in rels
    assert ("char_2", "member_of", "fac_b") in rels
    # 反向 has_member
    assert ("fac_a", "has_member", "char_1") in rels
    # 势力 → 地点
    assert ("fac_a", "controls", "loc_b") in rels
    assert ("fac_b", "controls", "loc_a") in rels
    # 势力 → 势力（hostile）
    assert ("fac_a", "hostile", "fac_b") in rels
    # 地点层级
    assert ("loc_a", "located_in", "loc_b") in rels
    # 无势力的人物不出 member_of 边
    assert not any(e.src == "char_3" and e.rel == "member_of" for e in edges)


def test_static_graph_is_idempotent():
    """二次调用替换不堆积（UNIQUE ON CONFLICT REPLACE）。"""
    r = _repo_w3w4_seeded()
    build_static_graph(r)
    n1 = len(r.list_edges())
    build_static_graph(r)
    n2 = len(r.list_edges())
    assert n1 == n2


def test_faction_relations_intensity_scaling():
    """W3 relations 的 intensity 1-5 → 边 intensity 0.2-1.0。"""
    r = _repo_w3w4_seeded()
    build_static_graph(r)
    e = r.get_edge("fac_a", "hostile", "fac_b")
    assert e is not None
    assert abs(e.intensity - 1.0) < 1e-6   # 5/5 = 1.0


# ---------------- 注意力 bump + 衰减 ----------------
def test_bump_creates_then_increments():
    r = _repo_w3w4_seeded()
    r.bump_edge_attention("char_1", "related_to", "char_2", chapter=1)
    e1 = r.get_edge("char_1", "related_to", "char_2")
    assert e1 and abs(e1.intensity - 0.65) < 1e-6   # 0.5+0.15
    assert e1.last_active_chapter == 1
    # 再 bump 一次
    r.bump_edge_attention("char_1", "related_to", "char_2", chapter=3, delta=0.20)
    e2 = r.get_edge("char_1", "related_to", "char_2")
    assert abs(e2.intensity - 0.85) < 1e-6   # 0.65+0.20
    assert e2.last_active_chapter == 3


def test_bump_clamps_to_one():
    r = _repo_w3w4_seeded()
    for ch in range(1, 8):
        r.bump_edge_attention("char_1", "related_to", "char_2", chapter=ch, delta=0.3)
    e = r.get_edge("char_1", "related_to", "char_2")
    assert e.intensity == 1.0


def test_decay_only_affects_dynamic_edges():
    r = _repo_w3w4_seeded()
    build_static_graph(r)
    r.bump_edge_attention("char_1", "related_to", "char_2", chapter=1)
    static_before = r.get_edge("fac_a", "hostile", "fac_b").intensity
    n = r.decay_edges(current_chapter=10, half_life=3)
    assert n >= 1
    # 静态边不变
    assert r.get_edge("fac_a", "hostile", "fac_b").intensity == static_before
    # 动态边显著衰减（9 章距，half_life=3 → 0.125 倍因子）
    dyn = r.get_edge("char_1", "related_to", "char_2")
    assert dyn.intensity < 0.2


def test_decay_skips_recently_active():
    r = _repo_w3w4_seeded()
    r.bump_edge_attention("char_1", "related_to", "char_2", chapter=5)
    before = r.get_edge("char_1", "related_to", "char_2").intensity
    r.decay_edges(current_chapter=5, half_life=3)  # gap=0
    assert r.get_edge("char_1", "related_to", "char_2").intensity == before


# ---------------- 邻居检索（W6 基础）----------------
def test_attention_ranked_neighbors():
    r = _repo_w3w4_seeded()
    build_static_graph(r)
    r.bump_edge_attention("char_1", "related_to", "char_2", chapter=5, delta=0.4)
    r.bump_edge_attention("char_1", "related_to", "char_3", chapter=5, delta=0.05)
    nbrs = r.attention_ranked_neighbors("char_1", limit=5)
    assert nbrs[0].intensity >= nbrs[-1].intensity   # 降序
    # 含双向
    assert any(e.dst == "fac_a" or e.src == "fac_a" for e in nbrs)


def test_neighbors_filter_by_rel():
    r = _repo_w3w4_seeded()
    build_static_graph(r)
    out = r.attention_ranked_neighbors("char_1", rels=("member_of",))
    assert all(e.rel == "member_of" for e in out)
    assert any(e.src == "char_1" and e.dst == "fac_a" for e in out)


def test_edge_roundtrip():
    r = Repository(db.connect(":memory:"))
    r.upsert_edge(GraphEdge(src="a", rel="related_to", dst="b",
                            meta={"k": "v"}, intensity=0.42,
                            since_chapter=2, last_active_chapter=4))
    got = r.get_edge("a", "related_to", "b")
    assert got.intensity == 0.42 and got.meta == {"k": "v"}
    assert got.since_chapter == 2 and got.last_active_chapter == 4
