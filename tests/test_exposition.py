from novel_engine.narration.exposition import Exposition
from novel_engine.seed import seed_m4


def test_drip_respects_release_condition():
    repo = seed_m4()
    exp = Exposition(repo)
    # 第 1 场早于 min_discourse_pos=2 → 不渗
    assert exp.drip(discourse_pos=1) == []
    assert not repo.reader_knows("fact_bg_massacre")


def test_drip_reveals_once_then_stops():
    repo = seed_m4()
    exp = Exposition(repo)
    revealed = exp.drip(discourse_pos=2)
    assert "fact_bg_massacre" in revealed
    assert repo.reader_knows("fact_bg_massacre")
    # 已渗过 → 不重复
    assert exp.drip(discourse_pos=3) == []
