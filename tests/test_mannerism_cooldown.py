"""③ 习惯动作冷却：同一 POV 连续成场时，习惯动作不应每场复读，且冷却内可出现"本场无动作"。"""
from __future__ import annotations

from novel_engine import db
from novel_engine.models import Entity, Event, Persona
from novel_engine.narration.narrator import Narrator
from novel_engine.repository import Repository


def _repo() -> Repository:
    r = Repository(db.connect(":memory:"))
    r.insert_entity(Entity("hero", "character", "云鹤子", {}))
    r.insert_persona(Persona(agent_id="hero", name="云鹤子", mannerisms=["攥衣角", "咬唇"],
                             motif_objects=[]))
    return r


def _ev() -> Event:
    return Event(event_id="ev1", story_time=1, actors=["hero"], action_type="试探")


def test_mannerism_not_repeated_within_cooldown():
    r = _repo()
    nar = Narrator(r, llm=None)  # 离线模板，确定性
    used: list[str | None] = []
    for pos in range(1, 5):
        prose = nar.render("hero", [_ev()], "", [], [], scene_pos=pos)
        if "攥衣角" in prose:
            used.append("攥衣角")
        elif "咬唇" in prose:
            used.append("咬唇")
        else:
            used.append(None)  # 本场无习惯动作（冷却中）
    # cooldown=3：不应连续两场用同一个习惯动作
    for a, b in zip(used, used[1:]):
        assert not (a is not None and a == b)
    # 第3场两者都在冷却 → 应出现"本场无动作"
    assert None in used
    # 两个习惯动作都被轮到过（不是只复读第一个）
    assert "攥衣角" in used and "咬唇" in used
