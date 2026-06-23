"""B1-a 大纲驱动重构脚手架：双层解码 + SceneWriter + FactExtractor（独立组件，未接入当前主链）。"""
from __future__ import annotations

import json

from novel_engine import db
from novel_engine.llm.base import LLMClient
from novel_engine.models import ChapterPlan, Entity, KnowledgeItem, Persona, RevealNode
from novel_engine.narration.fact_extractor import FactExtractor, SceneDelta
from novel_engine.narration.scene_writer import SceneSpec, SceneWriter
from novel_engine.repository import Repository


def _repo() -> Repository:
    r = Repository(db.connect(":memory:"))
    r.insert_entity(Entity("hero", "character", "沈砚", {}))
    r.insert_entity(Entity("ally", "character", "赵九", {}))
    r.insert_entity(Entity("bystander", "character", "苏静", {}))
    r.insert_entity(Entity("obj_pen", "object", "钢笔", {}))
    r.insert_persona(Persona(agent_id="hero", name="沈砚"))
    r.insert_persona(Persona(agent_id="ally", name="赵九"))
    r.insert_persona(Persona(agent_id="bystander", name="苏静"))
    return r


def _chapter() -> ChapterPlan:
    return ChapterPlan(chapter_id="ch1", arc_id="a1", sequence_order=1, title="",
                       cast=["hero", "ally"], items_present=["obj_pen"],
                       target_words=1200, target_scenes=3, target_tension=0.5,
                       beat_goals=["沈砚与赵九对峙"], dramatic_question="谁会先动手？")


# ---- ① 双层解码 ----
def test_complete_at_default_delegates_to_complete():
    class _Only(LLMClient):
        def complete(self, system, user):
            return "OK"
    assert _Only().complete_at("s", "u", 0.0) == "OK"   # 未重写 → 委托 complete，零破坏


class _TempCapture(LLMClient):
    def __init__(self):
        self.temps: list[float | None] = []

    def complete(self, system, user):
        return self.complete_at(system, user, None)

    def complete_at(self, system, user, temperature=None):
        self.temps.append(temperature)
        return "他停在门口，没有说话。"


def test_scene_writer_uses_high_temperature():
    r, llm = _repo(), _TempCapture()
    SceneWriter(r, llm).write(SceneSpec(pov="hero", beat="沈砚逼近赵九", chapter=_chapter()))
    assert 0.9 in llm.temps                              # 创作层高温


# ---- ② SceneWriter ----
def test_scene_writer_offline_non_empty():
    r = _repo()
    out = SceneWriter(r, llm=None).write(SceneSpec(pov="hero", beat="沈砚推门而入", chapter=_chapter()))
    assert "沈砚" in out and "推门" in out


def test_scene_writer_strips_headers_and_injects_constraints():
    class _Hdr(LLMClient):
        def complete(self, s, u):
            return self.complete_at(s, u)

        def complete_at(self, s, u, temperature=None):
            self.sys = s
            return "# 第三场\n沈砚握住钢笔，盯着赵九。"
    r, llm = _repo(), _Hdr()
    out = SceneWriter(r, llm).write(SceneSpec(pov="hero", beat="对峙", chapter=_chapter()))
    assert not out.startswith("#")                       # 元标题被剥
    assert "反升华红线" in llm.sys                        # B0.5 反升华注入


# ---- ③ FactExtractor ----
class _ExtractLLM(LLMClient):
    def complete(self, s, u):
        return self.complete_at(s, u)

    def complete_at(self, s, u, temperature=None):
        return json.dumps({
            "new_facts": [{"content": "沈砚夺走了钢笔", "involved": ["沈砚", "赵九"], "location": ""}],
            "reveals": ["f_secret"],
            "item_transfers": [{"obj": "钢笔", "to": "沈砚", "status": "transferred"}],
            "chosen_value": "夺权", "emotion": {"emotion": "紧张", "intensity": 0.7, "cause": "对峙"},
            "cost": "暴露了自己",
        }, ensure_ascii=False)


def test_extract_filters_reveals_by_may_reveal():
    r = _repo()
    fe = FactExtractor(r, _ExtractLLM())
    spec_ok = SceneSpec(pov="hero", beat="", chapter=_chapter(), may_reveal=["f_secret"])
    spec_no = SceneSpec(pov="hero", beat="", chapter=_chapter(), may_reveal=[])
    assert fe.extract("正文", "hero", ["hero", "ally"], spec_ok).reveals == ["f_secret"]
    assert fe.extract("正文", "hero", ["hero", "ally"], spec_no).reveals == []   # 越权揭示被挡


def test_extract_offline_empty():
    assert FactExtractor(_repo(), llm=None).extract("正文", "hero", ["hero"], SceneSpec("hero", "", _chapter())).new_facts == []


def test_commit_isolation_only_present_get_knowledge():
    r = _repo()
    ch = _chapter()
    delta = SceneDelta(new_facts=[{"content": "沈砚夺走了钢笔", "involved": ["沈砚", "赵九"]}])
    eids = FactExtractor(r).commit(delta, pov="hero", present=["hero", "ally"], tick=1, chapter=ch)
    assert len(eids) == 1
    assert any("钢笔" in k.version_content for k in r.get_agent_ledger("hero"))
    assert any("钢笔" in k.version_content for k in r.get_agent_ledger("ally"))
    assert not any("钢笔" in k.version_content for k in r.get_agent_ledger("bystander"))  # 隔离
    ev = r.get_event(eids[0])
    assert ev.beat_id == "ch1"                           # 事件带章号，复用收束/审计


def test_commit_item_transfer_and_reveal():
    r = _repo()
    ch = _chapter()
    r.upsert_knowledge(KnowledgeItem(agent_id="hero", fact_id="f_secret",
                                     version_content="赵九是卧底", confidence=1.0, learned_tick=1))
    r.upsert_reveal_node(RevealNode(node_id="rv1", fact_id="f_secret", kind="truth", sequence_order=1))
    delta = SceneDelta(
        new_facts=[{"content": "沈砚夺走了钢笔", "involved": ["沈砚"]}],
        reveals=["f_secret"],
        item_transfers=[{"obj": "钢笔", "to": "沈砚", "status": "transferred"}],
        emotion={"emotion": "紧张", "intensity": 0.7, "cause": "对峙"}, cost="暴露了自己",
    )
    FactExtractor(r).commit(delta, pov="hero", present=["hero"], tick=2, chapter=ch)
    assert r.get_inventory_item("obj_pen").holder_agent_id == "hero"     # 道具易手
    assert any(n.discovered for n in r.list_reveal_nodes())              # 揭示节点已 discover
    assert any(rk.fact_id == "f_secret" for rk in r.list_reader_knowledge())  # 读者账本
