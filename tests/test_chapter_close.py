"""§2 戏剧问题驱动收束：谓词命中即收（即便未到上界）；未命中且到上界强制收；下界保护。"""
from __future__ import annotations

from novel_engine import db
from novel_engine.agent import CharacterAgent
from novel_engine.dilemma import DilemmaGenerator
from novel_engine.director import Director
from novel_engine.llm.mock import MockClient
from novel_engine.models import ChapterPlan, Entity, Event, Fact, Persona, RevealNode
from novel_engine.monitors import Monitors
from novel_engine.repository import Repository
from novel_engine.validator import Validator


def _director(r: Repository) -> Director:
    return Director(r, DilemmaGenerator(r, llm=None), CharacterAgent(r, MockClient()),
                    Validator(r), Monitors(r))


def _repo() -> Repository:
    r = Repository(db.connect(":memory:"))
    r.insert_entity(Entity("hero", "character", "云鹤子", {}))
    r.insert_persona(Persona(agent_id="hero", name="云鹤子"))
    return r


def _add_event(r: Repository, beat_id: str, idx: int, chosen: str = "") -> None:
    eid = f"ev_{beat_id}_{idx}"
    r.append_event(Event(eid, story_time=idx, actors=["hero"], action_type="act",
                         payload={"chosen_value": chosen}))
    r.set_event_beat(eid, beat_id)


def test_lower_bound_protects_short_chapter():
    r = _repo()
    d = _director(r)
    ch = ChapterPlan("c1", "a1", 1, resolution_predicate="decision_made(hero)", min_scenes=2, target_scenes=4)
    _add_event(r, "c1", 1, chosen="道心")          # 谓词已可命中，但只有 1 场
    assert d._should_close(ch, r.count_events_for_beat("c1")) is False


def test_predicate_closes_before_max():
    r = _repo()
    d = _director(r)
    ch = ChapterPlan("c1", "a1", 1, resolution_predicate="decision_made(hero)", min_scenes=2, target_scenes=4)
    _add_event(r, "c1", 1, chosen="")
    _add_event(r, "c1", 2, chosen="道心")          # 第2场做出抉择 → 命中谓词
    assert d._should_close(ch, r.count_events_for_beat("c1")) is True   # 未到 max(4) 也收


def test_unanswered_runs_to_max():
    r = _repo()
    d = _director(r)
    ch = ChapterPlan("c1", "a1", 1, resolution_predicate="decision_made(hero)", min_scenes=2, target_scenes=3)
    _add_event(r, "c1", 1, chosen="")
    _add_event(r, "c1", 2, chosen="")              # 没人做抉择，谓词不命中
    assert d._should_close(ch, 2) is False          # 到下界但未答 → 不收
    _add_event(r, "c1", 3, chosen="")
    assert d._should_close(ch, 3) is True            # 到上界 → 强制收（保护）


def test_reveal_predicate():
    r = _repo()
    d = _director(r)
    r.append_fact(Fact("f_secret", "state", "真相", involved_entities=["hero"]))
    r.upsert_reveal_node(RevealNode("n1", fact_id="f_secret", kind="truth"))
    ch = ChapterPlan("c1", "a1", 1, resolution_predicate="reveal_discovered_fact(f_secret)",
                     min_scenes=2, target_scenes=5)
    _add_event(r, "c1", 1); _add_event(r, "c1", 2)
    assert d._should_close(ch, 2) is False           # 真相未撞到
    r.mark_node_discovered("n1", chapter=1)
    assert d._should_close(ch, 2) is True            # 撞到真相 → 收
