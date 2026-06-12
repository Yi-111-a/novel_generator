"""§13.5 cast 兑现校验：章收束时，声明 cast 收敛为实际行动过的人（治"声明 cast≠实际 cast"）。"""
from __future__ import annotations

from novel_engine import db
from novel_engine.agent import CharacterAgent
from novel_engine.dilemma import DilemmaGenerator
from novel_engine.director import Director
from novel_engine.llm.mock import MockClient
from novel_engine.models import Entity, Fact, Foreshadow, KnowledgeItem, Persona
from novel_engine.monitors import Monitors
from novel_engine.planner import Planner
from novel_engine.repository import Repository
from novel_engine.validator import Validator


def _seed_repo() -> Repository:
    r = Repository(db.connect(":memory:"))
    r.insert_entity(Entity("loc_main", "location", "主场景", {}))
    for aid, name in [("hero", "云鹤子"), ("ally", "季拾遗"), ("villain", "墨渊"), ("ghost", "影")]:
        r.insert_entity(Entity(aid, "character", name, {}))
        r.insert_persona(Persona(agent_id=aid, name=name, want="求道",
                                 values=[{"name": "道心", "weight": 0.7}], fatal_flaw="执念"))
    r.append_fact(Fact("f_secret", "state", "旧案关键握在墨渊手里。", involved_entities=["villain"]))
    r.insert_knowledge(KnowledgeItem("villain", "f_secret", "旧案关键握在墨渊手里。", 1.0, 0))
    r.upsert_foreshadow(Foreshadow("fs_secret", "墨渊瞒着什么？", "f_secret", 1, True))
    return r


def _director(r: Repository) -> Director:
    planner = Planner(r, llm=None, theme="证道")
    planner.build_master(part_count=3, arcs_per_part=1)
    planner.plan_next_arc()
    planner.next_chapter()
    return Director(
        r, DilemmaGenerator(r, llm=None, theme="证道"),
        CharacterAgent(r, MockClient()), Validator(r),
        Monitors(r, flaw_max_free=2), planner=planner,
    )


def test_phantom_cast_member_pruned_on_close():
    """P1 多角色后，cast 内的人都会轮流行动，_fulfil_cast 收敛为实际行动者。
    "ghost" 在 cast=[hero,ghost] 下会作为 actor_idx=1 真实行动，所以不被剔除。
    测试现在验证：收束后 closed.cast 是实际行动者的子集（而非超集），且非空。"""
    r = _seed_repo()
    d = _director(r)
    ch = r.active_chapter_plan()
    ch.cast = ["hero", "ghost"]
    r.upsert_chapter_plan(ch)

    # K=2（hero+ghost），target_scenes=3 beats → 需 2*3=6 步，多给 buffer
    for _ in range(20):
        step = d.step()
        if step.chapter_done:
            break
    closed = r.get_chapter_plan(ch.chapter_id)
    assert closed.status == "done"
    acted = {a for e in r.events_for_beat(ch.chapter_id) for a in e.actors}
    # P1：cast 成员轮流行动，_fulfil_cast 后 closed.cast ⊆ acted，且非空
    assert closed.cast, "兑现校验不得清空 cast"
    assert set(closed.cast) <= acted, "收束 cast 应只含真实行动过的人"


def test_cast_not_emptied_when_someone_acted():
    r = _seed_repo()
    d = _director(r)
    ch = r.active_chapter_plan()
    for _ in range(ch.target_scenes + 3):
        step = d.step()
        if step.chapter_done:
            break
    closed = r.get_chapter_plan(ch.chapter_id)
    assert closed.cast, "兑现校验不得清空 cast"
