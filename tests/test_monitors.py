from novel_engine.monitors import Monitors
from novel_engine.seed import PROTAGONIST_ID, seed_m2


def test_arc_stall_and_flaw_alarms_fire():
    repo = seed_m2()
    mon = Monitors(repo, flaw_max_free=3, arc_max_stall=4)
    alarms = mon.check(tick=10)  # 自 tick0 起无任何变化
    kinds = {(a.kind, a.agent_id) for a in alarms}
    assert ("arc_stalled", PROTAGONIST_ID) in kinds
    assert ("flaw_free_too_long", PROTAGONIST_ID) in kinds


def test_no_alarm_when_recent():
    repo = seed_m2()
    p = repo.get_persona(PROTAGONIST_ID)
    p.arc_state = {"last_change_tick": 9, "last_flaw_cost_tick": 9}
    repo.insert_persona(p)
    mon = Monitors(repo, flaw_max_free=3, arc_max_stall=4)
    alarms = [a for a in mon.check(tick=10) if a.agent_id == PROTAGONIST_ID]
    assert alarms == []


def test_static_character_skipped():
    repo = seed_m2()
    p = repo.get_persona(PROTAGONIST_ID)
    p.arc_state = {"static": True, "last_change_tick": 0}
    repo.insert_persona(p)
    mon = Monitors(repo)
    assert [a for a in mon.check(tick=99) if a.agent_id == PROTAGONIST_ID] == []
