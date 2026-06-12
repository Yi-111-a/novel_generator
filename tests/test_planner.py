"""P2 规划器：总纲生成 + 滚动 Arc/章计划。离线（无 LLM）走确定性回退路径。

重点验证约束硬性：章计划的 cast/locations/items/reveal_gate 不得超出 DB 现状。
"""
from __future__ import annotations

from novel_engine import db
from novel_engine.models import (
    Entity, Fact, Foreshadow, KnowledgeItem, Persona,
)
from novel_engine.planner import Planner, _role_curve
from novel_engine.repository import Repository


def _seed_repo() -> Repository:
    """3 个角色：hero/ally/villain；hero 持有 motif 物品；villain 独握一条秘密真相。"""
    r = Repository(db.connect(":memory:"))
    r.insert_entity(Entity("loc_main", "location", "主场景", {}))
    for aid, name, motif in [("hero", "云鹤子", ["obj_compass"]), ("ally", "季拾遗", []), ("villain", "墨渊", [])]:
        r.insert_entity(Entity(aid, "character", name, {}))
        for o in motif:
            r.insert_entity(Entity(o, "object", o, {}))
        r.insert_persona(Persona(agent_id=aid, name=name, want="求道", motif_objects=motif))
    # hero 知道一条前提；villain 独握秘密
    r.append_fact(Fact("f_premise", "event", "云鹤子重回风暴中心。", involved_entities=["hero"]))
    r.insert_knowledge(KnowledgeItem("hero", "f_premise", "云鹤子重回风暴中心。", 1.0, 0))
    r.append_fact(Fact("f_secret", "state", "旧案关键握在墨渊手里。", involved_entities=["villain"]))
    r.insert_knowledge(KnowledgeItem("villain", "f_secret", "旧案关键握在墨渊手里。", 1.0, 0))
    r.upsert_foreshadow(Foreshadow("fs_secret", "墨渊瞒着什么？", "f_secret", 1, True))
    return r


def test_build_master_creates_parts_chain_and_inventory():
    r = _seed_repo()
    info = Planner(r, llm=None, theme="轮回证道").build_master(part_count=4)
    parts = r.list_parts()
    assert 3 <= len(parts) <= 5 and len(parts) == 4
    assert all(p.region for p in parts)  # 每部分有地域
    # ⑥ 每个 Part 落多个地点实体（按剧情分配），且每部至少 1 个
    locs = [e for e in r.list_entities() if e.type == "location" and e.attributes.get("part")]
    assert len(locs) >= len(parts)
    assert all(any(e.attributes.get("part") == p.part_id for e in locs) for p in parts)
    # 揭示链：至少含 hero 不知的 f_secret 作为 truth 节点
    nodes = r.list_reveal_nodes()
    truths = [n for n in nodes if n.kind == "truth"]
    assert any(n.fact_id == "f_secret" for n in truths)
    # 每个 truth 前面有线索作为前置（探索驱动）
    assert all(t.prereq_node_ids for t in truths)
    # 物品落入 hero 库存
    assert r.agent_holds("hero", "obj_compass")
    # 揭示节点已分摊到 part 且回填 part_id
    assert all(n.part_id for n in nodes)
    assert parts[0].status == "active"


def test_build_master_idempotent():
    r = _seed_repo()
    p = Planner(r, llm=None)
    p.build_master(part_count=3)
    n1 = len(r.list_parts())
    res = p.build_master(part_count=3)
    assert res.get("skipped") and len(r.list_parts()) == n1


def test_plan_next_arc_respects_constraints():
    r = _seed_repo()
    p = Planner(r, llm=None, theme="轮回证道")
    p.build_master(part_count=3, arcs_per_part=2)
    seed_chars = {"hero", "ally", "villain"}

    arc = p.plan_next_arc()
    assert arc is not None and 5 <= arc.target_chapters <= 10
    assert arc.focus_agents and arc.focus_agents[0]["agent_id"] in seed_chars
    # ⑤ 软节拍：plan_next_arc 只建骨架，章节尚未铺满
    assert r.list_chapter_plans(arc.arc_id) == []

    # 逐章懒生成，直至铺满本 Arc
    while len(r.list_chapter_plans(arc.arc_id)) < arc.target_chapters:
        p.next_chapter()
    chapters = r.list_chapter_plans(arc.arc_id)
    assert len(chapters) == arc.target_chapters
    # 合法角色 = 当前世界里真实存在的角色（含 ④ 孵化的新角色）——只要不凭空捏造即可
    valid_chars = {e.entity_id for e in r.list_entities() if e.type == "character"}
    part0 = r.list_parts()[0]
    part_locs = {e.entity_id for e in r.list_entities()
                 if e.type == "location" and (e.attributes.get("part") == part0.part_id or e.entity_id == "loc_main")}
    all_truth_fids = {n.fact_id for n in r.list_reveal_nodes() if n.fact_id}
    for c in chapters:
        assert c.cast and set(c.cast) <= valid_chars            # 人物不凭空出现
        assert set(c.location_ids) <= part_locs                 # 地点属于本 Part 地域
        for obj in c.available_items:                           # 物品来自 cast 库存，非凭空
            assert any(r.agent_holds(a, obj) for a in c.cast)
        assert set(c.reveal_gate) <= all_truth_fids             # 只揭示揭示链里的真相
        assert 2 <= c.target_scenes <= 4


def test_rolling_generation_advances_and_terminates():
    r = _seed_repo()
    p = Planner(r, llm=None)
    p.build_master(part_count=3, arcs_per_part=1)  # 3 part × 1 arc
    # 持续懒生成章节，直到全书铺满（next_chapter 会跨 Arc 自动续建）
    for _ in range(200):
        if p.next_chapter() is None:
            break
    assert len(r.list_arcs()) == 3              # 恰好 3 个 arc
    assert all(pt.status == "done" for pt in r.list_parts())
    # 全书章号全局递增、连续
    seqs = [c.sequence_order for c in r.list_chapter_plans()]
    assert seqs == list(range(1, len(seqs) + 1))


def test_role_curve_has_arc_shape():
    for n in (5, 6, 8, 10):
        roles = _role_curve(n)
        names = [r for r, _ in roles]
        assert len(roles) == n
        assert names[0] == "setup"                 # 起
        assert "twist" in names and "climax" in names  # 必含转、合
        # twist 在 climax 之前；张力都在 [0,1]
        assert names.index("twist") < (len(names) - 1 - names[::-1].index("climax")) + 1
        assert all(0 <= t <= 1 for _, t in roles)


def test_arc_chapters_have_roles_and_distinct_goals():
    r = _seed_repo()
    p = Planner(r, llm=None, theme="轮回证道")
    p.build_master(part_count=3, arcs_per_part=1)
    arc = p.plan_next_arc()
    while len(r.list_chapter_plans(arc.arc_id)) < arc.target_chapters:
        p.next_chapter()
    chs = r.list_chapter_plans(arc.arc_id)
    roles = [c.role for c in chs]
    assert roles[0] == "setup" and "twist" in roles and "climax" in roles
    # 每章目标彼此不同（离线回退也按 role 区分，不再雷同）
    goals = [c.beat_goals[0] for c in chs]
    assert len(set(goals)) >= max(3, len(goals) - 1)
    # 张力随 role 变化（climax 章张力最高）
    climax = next(c for c in chs if c.role == "climax")
    assert climax.target_tension >= max(c.target_tension for c in chs if c.role == "setup")


def test_title_blacklist_catches_overused_word():
    r = _seed_repo()
    p = Planner(r, llm=None)
    # "轮回"在多个近期标题里反复出现 → 应被列入禁用词
    bl = p._title_blacklist(["轮回之始", "轮回再临", "永夜"])
    assert "轮回" in bl


def test_incubator_grows_roster_at_new_arc():
    from novel_engine.worldsmith import WorldSmith
    r = _seed_repo()
    ws = WorldSmith(r, llm=None, theme="x")
    p = Planner(r, llm=None, theme="x", worldsmith=ws)
    p.build_master(part_count=3, arcs_per_part=1)
    before = len([e for e in r.list_entities() if e.type == "character"])
    p.plan_next_arc()   # 新 Arc 开场 → 孵化一个新角色
    after = len([e for e in r.list_entities() if e.type == "character"])
    assert after == before + 1
    # 不超过群像上限
    for _ in range(10):
        p.plan_next_arc()
    assert len([e for e in r.list_entities() if e.type == "character"]) <= p.roster_cap


def test_chapter_location_within_part():
    r = _seed_repo()
    p = Planner(r, llm=None, theme="x")
    p.build_master(part_count=3, arcs_per_part=1)
    arc = p.plan_next_arc()
    part0 = r.list_parts()[0]
    part_locs = {e.entity_id for e in r.list_entities()
                 if e.type == "location" and (e.attributes.get("part") == part0.part_id or e.entity_id == "loc_main")}
    while len(r.list_chapter_plans(arc.arc_id)) < arc.target_chapters:
        p.next_chapter()
    for c in r.list_chapter_plans(arc.arc_id):
        assert set(c.location_ids) <= part_locs  # 地点恒属本 Part


def test_name_chapter_offline_fallback():
    r = _seed_repo()
    p = Planner(r, llm=None)
    p.build_master(part_count=3, arcs_per_part=1)
    p.plan_next_arc()
    ch = p.next_chapter()
    title = p.name_chapter(ch.chapter_id)
    assert title and r.get_chapter_plan(ch.chapter_id).title == title
