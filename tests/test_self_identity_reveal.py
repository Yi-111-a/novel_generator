"""问题5 补全：主角**自身**伪装身份纳入受控揭示。

主角持有的隐藏身份是"本人知、他人/读者不知"，与核心真相（主角去发现别人的秘密）
方向相反，会被 _build_reveal_chain 主循环排除。这里验证它经 _build_self_reveal_chain
单独建出一条 线索→线索→暴露(truth) 揭示线，落到末部、可作 reveal_gate，并能经
director 触发 reveal_to_reader → 解锁 narrator 称谓闸门。
"""
from __future__ import annotations

from novel_engine import db
from novel_engine.models import Entity, Fact, KnowledgeItem, Persona
from novel_engine.narration.narrator import Narrator
from novel_engine.planner import Planner
from novel_engine.repository import Repository


def _hero_identity_repo() -> Repository:
    """谍战场景：LLM 给主角沈砚锁了'地下党联络员'伪装身份（本人知，他人不知）。"""
    r = Repository(db.connect(":memory:"))
    r.insert_entity(Entity("loc_main", "location", "主场景", {}))
    r.insert_entity(Entity(
        "hero", "character", "沈砚",
        {"identity": {"public": "书局的账房", "true": "地下党联络员", "fact_id": "f_self"}}))
    r.insert_persona(Persona(agent_id="hero", name="沈砚", want="潜伏完成任务"))
    r.insert_entity(Entity("foe", "character", "周明远", {}))
    r.insert_persona(Persona(agent_id="foe", name="周明远", want="揪出内鬼"))
    # 主角自身身份 fact：只有本人知道
    r.append_fact(Fact("f_self", "state", "沈砚是地下党联络员", involved_entities=["hero"]))
    r.insert_knowledge(KnowledgeItem("hero", "f_self", "沈砚是地下党联络员", 1.0, 0))
    return r


def _narrator(r: Repository) -> Narrator:
    n = Narrator.__new__(Narrator)
    n.repo = r
    return n


# ---- 主角自身伪装身份进入揭示链（核心修复点）----
def test_hero_self_identity_enters_reveal_chain():
    r = _hero_identity_repo()
    Planner(r, llm=None, theme="谍战").build_master(part_count=3)
    nodes = r.list_reveal_nodes()
    truths = [n for n in nodes if n.kind == "truth" and n.fact_id == "f_self"]
    assert len(truths) == 1, "主角自身身份应建出且仅建出一个 truth 揭示节点"
    assert truths[0].part_id, "该 truth 节点必须被分配到某个 Part（进入揭示链）"
    # 配套的'暴露'线索节点（fact_id=None，不作 gate，只供中间章铺陈）
    clues = [n for n in nodes if n.fact_id is None and "暴露线索" in n.description]
    assert len(clues) == 2
    assert truths[0].prereq_node_ids, "暴露 truth 应有线索前置（探索/暴露驱动）"


# ---- 暴露 truth 落在末部，对齐高潮 ----
def test_hero_self_identity_lands_in_last_part():
    r = _hero_identity_repo()
    Planner(r, llm=None, theme="谍战").build_master(part_count=3)
    parts = r.list_parts()
    truth = next(n for n in r.list_reveal_nodes() if n.fact_id == "f_self")
    assert truth.node_id in parts[-1].reveal_node_ids


# ---- 唯一带 fact_id 的可 gate 节点仍只是 truth（不破坏既有不变量）----
def test_self_reveal_gateable_node_is_truth():
    r = _hero_identity_repo()
    Planner(r, llm=None, theme="谍战").build_master(part_count=3)
    gateable = [n for n in r.list_reveal_nodes() if n.fact_id]
    assert gateable and all(n.kind == "truth" for n in gateable)


# ---- 主角无伪装身份 → 不建暴露线（不臆造）----
def test_no_self_reveal_when_hero_has_no_identity():
    r = _hero_identity_repo()
    # 抹掉主角 identity 属性
    r.update_entity_attributes("hero", {"identity": None})
    Planner(r, llm=None, theme="谍战").build_master(part_count=3)
    nodes = r.list_reveal_nodes()
    assert not any(n.fact_id == "f_self" for n in nodes)
    assert not any("暴露线索" in n.description for n in nodes)


# ---- 末部里程碑章把暴露 truth 选作 reveal_gate（受控揭示节点驱动）----
def test_milestone_chapter_picks_self_reveal_gate():
    r = _hero_identity_repo()
    p = Planner(r, llm=None, theme="谍战")
    p.build_master(part_count=3)
    p.build_full_outline()
    # 全章纲里应有一章把 f_self 选作 reveal_gate
    gated = [c for c in r.list_chapter_plans() if "f_self" in c.reveal_gate]
    assert gated, "末部的 twist/climax 章应把主角身份暴露选作 reveal_gate"
    assert all(c.role in ("twist", "climax") for c in gated)


# ---- 揭示前（读者/POV 都不知）：别的 POV 章只能用中性称呼，禁真实头衔 ----
def test_hero_appellation_locked_before_self_reveal():
    r = _hero_identity_repo()
    n = _narrator(r)
    lines = n._identity_lines(["hero", "foe"], pov="foe",
                              pov_fids=set(), reader_fids=set())
    assert any("书局的账房" in l and "尚未揭示" in l and "绝不可" in l for l in lines)


# ---- 揭示后（读者已知该 fact）：称谓闸门解锁，可用真实头衔 ----
def test_hero_appellation_unlocked_after_reader_knows():
    r = _hero_identity_repo()
    n = _narrator(r)
    lines = n._identity_lines(["hero", "foe"], pov="foe",
                              pov_fids=set(), reader_fids={"f_self"})
    assert any("已揭示" in l and "地下党联络员" in l for l in lines)
    assert not any("绝不可" in l for l in lines)
