"""B2 隔离/揭示/库存迁移：FactExtractor 公平谜题揭示（prereq 门控）+ transfer_item + 隔离。"""
from __future__ import annotations

from novel_engine import db
from novel_engine.models import (
    ChapterPlan, Entity, Fact, KnowledgeItem, Persona, RevealNode,
)
from novel_engine.narration.fact_extractor import FactExtractor, SceneDelta
from novel_engine.repository import Repository


def _repo() -> Repository:
    r = Repository(db.connect(":memory:"))
    r.insert_entity(Entity("hero", "character", "沈砚", {}))
    r.insert_entity(Entity("ally", "character", "赵九", {}))
    r.insert_entity(Entity("bystander", "character", "苏静", {}))
    r.insert_entity(Entity("obj_pen", "object", "钢笔", {}))
    for aid, nm in [("hero", "沈砚"), ("ally", "赵九"), ("bystander", "苏静")]:
        r.insert_persona(Persona(agent_id=aid, name=nm))
    return r


def _ch(**kw) -> ChapterPlan:
    base = dict(chapter_id="ch1", arc_id="a1", sequence_order=2, title="", status="active",
                cast=["hero", "ally"], items_present=[], min_scenes=1, target_scenes=3)
    base.update(kw)
    return ChapterPlan(**base)


# ---- 隔离：只有在场者拿到新事实 ----
def test_new_fact_isolated_to_present():
    r = _repo()
    FactExtractor(r).commit(
        SceneDelta(new_facts=[{"content": "沈砚与赵九密谈", "involved": ["沈砚", "赵九"]}]),
        pov="hero", present=["hero", "ally"], tick=1, chapter=_ch())
    assert any("密谈" in k.version_content for k in r.get_agent_ledger("ally"))
    assert not any("密谈" in k.version_content for k in r.get_agent_ledger("bystander"))


# ---- Fix C 焦点化：在场但未参与的旁观者不自动获知 ----
def test_focalization_bystander_in_cast_does_not_learn():
    r = _repo()
    ch = _ch(cast=["hero", "ally", "bystander"])   # 苏静在场但不参与这条私密事实
    FactExtractor(r).commit(
        SceneDelta(new_facts=[{"content": "沈砚悄悄塞给赵九一张纸条", "involved": ["沈砚", "赵九"]}]),
        pov="hero", present=["hero", "ally", "bystander"], tick=1, chapter=ch)
    assert any("纸条" in k.version_content for k in r.get_agent_ledger("hero"))
    assert any("纸条" in k.version_content for k in r.get_agent_ledger("ally"))
    # 苏静在同一场，但不是这条事实的参与者 → 不自动获知
    assert not any("纸条" in k.version_content for k in r.get_agent_ledger("bystander"))


# ---- 道具：transferred → held by 新主 + 进 items_present；lost → status lost ----
def test_item_transfer_held_and_whitelisted():
    r = _repo()
    ch = _ch()
    FactExtractor(r).commit(
        SceneDelta(item_transfers=[{"obj": "钢笔", "to": "沈砚", "status": "transferred"}]),
        pov="hero", present=["hero"], tick=1, chapter=ch)
    inv = r.get_inventory_item("obj_pen")
    assert inv.holder_agent_id == "hero" and inv.status == "held"
    assert "obj_pen" in ch.items_present                      # 并入叙述白名单


def test_item_lost():
    r = _repo()
    FactExtractor(r).commit(
        SceneDelta(item_transfers=[{"obj": "钢笔", "to": "", "status": "lost"}]),
        pov="hero", present=["hero"], tick=1, chapter=_ch())
    assert r.get_inventory_item("obj_pen").status == "lost"


def test_narrative_item_registered():
    # B6.1：正文里易手的叙事道具（抽取器标记）→ 入册成 object 实体 + 落库存（来自正文、非凭空）
    r = _repo()
    FactExtractor(r).commit(
        SceneDelta(item_transfers=[{"obj": "染血纸条", "to": "沈砚", "status": "transferred"}]),
        pov="hero", present=["hero"], tick=1, chapter=_ch())
    ents = [e for e in r.list_entities() if e.type == "object" and e.name == "染血纸条"]
    assert len(ents) == 1 and ents[0].attributes.get("source") == "narrated"
    assert r.get_inventory_item(ents[0].entity_id).holder_agent_id == "hero"


def test_non_object_garbage_not_registered():
    # 英文残留/泛指不入册（过滤）
    r = _repo()
    FactExtractor(r).commit(
        SceneDelta(item_transfers=[{"obj": "backup chip", "to": "沈砚", "status": "transferred"}]),
        pov="hero", present=["hero"], tick=1, chapter=_ch())
    assert all(e.name != "backup chip" for e in r.list_entities())


# ---- 揭示：prereq 门控（公平谜题，不越级）----
def _seed_reveal_chain(r: Repository):
    # 线索节点 rv_clue（未发现）→ 真相节点 rv_truth（prereq=rv_clue）
    r.append_fact(Fact(fact_id="f_truth", fact_type="secret", canonical_content="赵九是卧底",
                       story_time=0, involved_entities=["ally"]))
    r.upsert_reveal_node(RevealNode(node_id="rv_clue", fact_id="f_clue", kind="clue", sequence_order=1))
    r.upsert_reveal_node(RevealNode(node_id="rv_truth", fact_id="f_truth", kind="truth",
                                    sequence_order=2, prereq_node_ids=["rv_clue"]))
    # POV 此刻还不知道真相
    r.upsert_knowledge(KnowledgeItem("hero", "f_truth", "赵九是卧底", 1.0, 0))


def test_reveal_blocked_when_prereq_unmet():
    r = _repo()
    _seed_reveal_chain(r)
    FactExtractor(r).commit(SceneDelta(reveals=["f_truth"]), pov="hero", present=["hero"],
                            tick=1, chapter=_ch(reveal_gate=["f_truth"]))
    assert not any(n.discovered for n in r.list_reveal_nodes() if n.node_id == "rv_truth")
    assert not r.reader_knows("f_truth")                      # 越级揭示被挡


def test_reveal_succeeds_after_prereq_discovered():
    r = _repo()
    _seed_reveal_chain(r)
    r.mark_node_discovered("rv_clue", 1)                      # 线索已出
    FactExtractor(r).commit(SceneDelta(reveals=["f_truth"]), pov="hero", present=["hero"],
                            tick=2, chapter=_ch(reveal_gate=["f_truth"]))
    assert any(n.discovered for n in r.list_reveal_nodes() if n.node_id == "rv_truth")
    assert r.reader_knows("f_truth")                          # 读者账本拿到
    # 读者看到的是 POV 持有的版本
    rk = next(x for x in r.list_reader_knowledge() if x.fact_id == "f_truth")
    assert rk.via_pov == "hero" and "卧底" in rk.revealed_version
