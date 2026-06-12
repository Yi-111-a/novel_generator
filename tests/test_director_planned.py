"""P3 导演计划模式：按章计划约束推进 + 章节收束 + 探索驱动揭示。

向后兼容：不传 planner 时行为不变（由既有 director 测试覆盖）。
"""
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
    for aid, name, motif in [("hero", "云鹤子", ["obj_compass"]), ("ally", "季拾遗", []), ("villain", "墨渊", [])]:
        r.insert_entity(Entity(aid, "character", name, {}))
        for o in motif:
            r.insert_entity(Entity(o, "object", o, {}))
        r.insert_persona(Persona(agent_id=aid, name=name, want="求道",
                                 values=[{"name": "道心", "weight": 0.7}, {"name": "旧情", "weight": 0.5}],
                                 fatal_flaw="执念", motif_objects=motif))
    r.append_fact(Fact("f_secret", "state", "旧案关键握在墨渊手里。", involved_entities=["villain"]))
    r.insert_knowledge(KnowledgeItem("villain", "f_secret", "旧案关键握在墨渊手里。", 1.0, 0))
    r.upsert_foreshadow(Foreshadow("fs_secret", "墨渊瞒着什么？", "f_secret", 1, True))
    return r


def _planned_director(r: Repository) -> tuple[Director, Planner]:
    planner = Planner(r, llm=None, theme="轮回证道")
    planner.build_master(part_count=3, arcs_per_part=1)
    planner.plan_next_arc()   # 建第一段 Arc 骨架
    planner.next_chapter()    # ⑤ 临场生成首章（与 lock_and_build 一致）
    d = Director(
        r,
        DilemmaGenerator(r, llm=None, theme="轮回证道"),
        CharacterAgent(r, MockClient()),       # 默认 observe 动作，必过校验
        Validator(r),
        Monitors(r, flaw_max_free=2),
        planner=planner,
    )
    return d, planner


def test_events_constrained_to_chapter_cast_and_location():
    r = _seed_repo()
    d, _ = _planned_director(r)
    ch0 = r.active_chapter_plan()
    for _ in range(3):
        d.step()
    evs = r.events_for_beat(ch0.chapter_id)
    assert evs, "本章应有事件被打上章号"
    for e in evs:
        assert e.actors[0] in ch0.cast                  # 行动者属于本章 cast
        assert e.location_id in ch0.location_ids        # 落在本章地点
        assert set(e.perceivers) <= set(ch0.cast)       # 在场者不超出 cast（不凭空牵人）


def test_chapter_advances_after_target_scenes():
    r = _seed_repo()
    d, _ = _planned_director(r)
    ch0 = r.active_chapter_plan()
    done_seen = False
    # P1 多角色：每场 K≤4 个 actor 各产 1 事件，允许更多迭代（target_scenes * cast_size + buffer）
    for _ in range(60):
        step = d.step()
        if step.chapter_done:
            done_seen = True
            break
    assert done_seen
    assert r.get_chapter_plan(ch0.chapter_id).status == "done"
    # ⑤ 软节拍：下一章在下一拍才临场懒生成；再走一拍即出现新章
    d.step()
    nxt = r.active_chapter_plan()
    assert nxt is not None and nxt.chapter_id != ch0.chapter_id


def test_exploration_driven_reveal_marks_node_and_hero_knows():
    r = _seed_repo()
    d, _ = _planned_director(r)
    ch = r.active_chapter_plan()
    # 强制本章的探索目标=揭开 f_secret（hero 此前并不知道）
    ch.reveal_gate = ["f_secret"]
    ch.cast = ["hero", "villain"]
    r.upsert_chapter_plan(ch)
    assert not r.agent_knows_fact("hero", "f_secret")

    revealed = []
    # P1：K=min(2,4)=2 actors/beat, target_scenes=3 beats → 最多 3*2+4=10 次迭代
    for _ in range(20):
        step = d.step()
        if step.chapter_done:
            revealed = step.revealed
            break
    assert "f_secret" in revealed
    assert r.agent_knows_fact("hero", "f_secret")               # 主角通过探索获知
    node = next(n for n in r.list_reveal_nodes() if n.fact_id == "f_secret")
    assert node.discovered and node.discovered_chapter == ch.sequence_order


def test_rolling_regenerates_when_arc_exhausted():
    r = _seed_repo()
    d, planner = _planned_director(r)
    arcs_before = len(r.list_arcs())
    # 狂推：把第一段所有章写满，应触发 plan_next_arc 生成下一段
    for _ in range(200):
        d.step()
        if len(r.list_arcs()) > arcs_before:
            break
    assert len(r.list_arcs()) > arcs_before
