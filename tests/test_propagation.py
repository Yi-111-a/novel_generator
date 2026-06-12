from novel_engine.propagation import Propagator, RuleDistorter
from novel_engine.seed import PROTAGONIST_ID, SENIOR_ID, seed_m2


def test_direct_perceive_is_canonical():
    repo = seed_m2()
    prop = Propagator(repo)
    prop.perceive("f_x", "甲在堂上当众认罪。", [PROTAGONIST_ID, SENIOR_ID], tick=1, source_event_id="ev_x")
    lin = next(k for k in repo.get_agent_ledger(PROTAGONIST_ID) if k.fact_id == "f_x")
    assert lin.version_content == "甲在堂上当众认罪。"
    assert lin.confidence == 1.0


def test_secondhand_tell_is_distorted():
    """秦松把只有他知道的真相转述给林晚 → 版本被扭曲、可信度衰减。"""
    repo = seed_m2()
    prop = Propagator(repo, RuleDistorter(decay=0.6))
    canonical = next(
        k for k in repo.get_agent_ledger(SENIOR_ID) if k.fact_id == "fact_jade_location"
    ).version_content

    item = prop.tell(SENIOR_ID, PROTAGONIST_ID, "fact_jade_location", tick=2)
    assert item is not None
    assert item.confidence < 1.0
    assert item.version_content != canonical  # 误传：version ≠ canonical
    assert "据传" in item.version_content


def test_cannot_tell_what_you_dont_know():
    """隔离：林晚不知道 fact_jade_location，无法把它转述出去。"""
    repo = seed_m2()
    prop = Propagator(repo)
    assert prop.tell(PROTAGONIST_ID, SENIOR_ID, "fact_jade_location", tick=1) is None


def test_conflict_pairs_detected():
    repo = seed_m2()
    prop = Propagator(repo)
    prop.tell(SENIOR_ID, PROTAGONIST_ID, "fact_jade_location", tick=2)
    conflicts = repo.find_conflict_pairs()
    fids = {c["fact_id"] for c in conflicts}
    assert "fact_jade_location" in fids
