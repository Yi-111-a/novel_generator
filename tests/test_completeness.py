from novel_engine.monitors import story_completeness
from novel_engine.seed import PROTAGONIST_ID, seed_m4


def test_completeness_tracks_arc_and_cost():
    repo = seed_m4()  # 内含跑过的导演循环
    rows = story_completeness(repo)
    assert rows
    lin = next(r for r in rows if r.agent_id == PROTAGONIST_ID)
    # 主角被改变过、付出过代价
    assert lin.arc_changed
    assert lin.cost_count > 0


def test_static_character_excluded_from_completeness():
    repo = seed_m4()
    p = repo.get_persona(PROTAGONIST_ID)
    p.arc_state = {**p.arc_state, "static": True}
    repo.insert_persona(p)
    rows = story_completeness(repo)
    assert all(r.agent_id != PROTAGONIST_ID for r in rows)
