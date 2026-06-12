from novel_engine.narration.selection import select_and_order
from novel_engine.narration.tension import HIGH, TensionScheduler
from novel_engine.seed import THEME, seed_m4


def test_breather_inserted_between_consecutive_highs():
    repo = seed_m4()
    sel = select_and_order(repo, THEME, threshold=0.5)  # 同时后置标注 drama_score
    sched = TensionScheduler(repo, max_consecutive_high=1).schedule(sel.selected, sel.skipped)
    roles = [s.role for s in sched.scenes]
    assert "breather" in roles
    # 喘息场景张力应明显低于高潮
    breather = next(s for s in sched.scenes if s.role == "breather")
    assert breather.target_tension < HIGH


def test_tension_curve_rises_dips_rises():
    repo = seed_m4()
    sel = select_and_order(repo, THEME, threshold=0.5)
    sched = TensionScheduler(repo, max_consecutive_high=1).schedule(sel.selected, sel.skipped)
    curve = sched.curve()
    # 至少出现一次"降下来再起"的张弛
    assert any(curve[i] < curve[i - 1] for i in range(1, len(curve)))


def test_alarm_when_no_breather_material():
    repo = seed_m4()
    sel = select_and_order(repo, THEME, threshold=0.5)
    # 无喘息素材可插 → 连续高张力报警
    sched = TensionScheduler(repo, max_consecutive_high=1).schedule(sel.selected, breathers=[])
    assert sched.alarms
