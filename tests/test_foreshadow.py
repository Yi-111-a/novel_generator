from novel_engine.narration.foreshadow import (
    ForeshadowLedger,
    backfill_payoff_beats,
    finalize_ending,
    honesty_gate,
)
from novel_engine.seed import seed_m4


def test_plant_requires_real_fact():
    repo = seed_m4()
    led = ForeshadowLedger(repo)
    assert led.plant("无中生有？", "fact_not_exist", 1) is None
    assert led.plant("真有此事？", "fact_bg_massacre", 1) is not None


def test_pay_off_marks_status():
    repo = seed_m4()
    led = ForeshadowLedger(repo)
    paid = led.pay_off_for_fact("fact_bg_massacre", discourse_pos=5)
    assert any(f.foreshadow_id == "fs_bg_massacre" for f in paid)
    fs = repo.get_foreshadow("fs_bg_massacre")
    assert fs.status == "paid_off" and fs.payoff_discourse_pos == 5


def test_honesty_gate_blocks_then_backfill_unblocks():
    repo = seed_m4()
    # 预埋的 must_resolve 伏笔尚无回收节拍 → 阻止收尾
    rpt = honesty_gate(repo)
    assert not rpt.ok
    assert rpt.blocking

    created = backfill_payoff_beats(repo)
    assert created  # 回填了回收节拍
    assert honesty_gate(repo).ok


def test_finalize_ending_blocked_until_resolved():
    repo = seed_m4()
    # 直接收尾应被闸门挡住，结局不应变 final
    rpt = finalize_ending(repo, "end_truth", auto_backfill=False)
    assert not rpt.ok
    assert all(e.status == "candidate" for e in repo.list_endings())

    # 自动回填后放行
    rpt2 = finalize_ending(repo, "end_truth", auto_backfill=True)
    assert rpt2.ok
    finals = [e for e in repo.list_endings() if e.status == "final"]
    assert len(finals) == 1 and finals[0].ending_id == "end_truth"
