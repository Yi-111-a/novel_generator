"""§16.3 文风参数化规划层：tension_curve_bias 改张弛曲线形状；complexity 改副线数量。"""
from __future__ import annotations

from novel_engine import db
from novel_engine.models import Entity, Fact, Foreshadow, KnowledgeItem, Persona, ToneProfile
from novel_engine.planner import Planner, _norm_tension_bias, _role_curve
from novel_engine.repository import Repository


def _repo() -> Repository:
    r = Repository(db.connect(":memory:"))
    r.insert_entity(Entity("loc_main", "location", "主场景", {}))
    for aid, name in [("hero", "云鹤子"), ("ally", "季拾遗"), ("friend", "沈澜"), ("villain", "墨渊")]:
        r.insert_entity(Entity(aid, "character", name, {}))
        r.insert_persona(Persona(agent_id=aid, name=name, want=f"{name}的执念"))
    r.append_fact(Fact("f_secret", "state", "旧案关键握在墨渊手里。", involved_entities=["villain"]))
    r.insert_knowledge(KnowledgeItem("villain", "f_secret", "旧案关键握在墨渊手里。", 1.0, 0))
    r.upsert_foreshadow(Foreshadow("fs_secret", "墨渊瞒着什么？", "f_secret", 1, True))
    return r


def test_bias_normalization():
    assert _norm_tension_bias("锯齿（频繁小高潮）") == "sawtooth"
    assert _norm_tension_bias("单调递进到爆发") == "ramp"
    assert _norm_tension_bias("波浪") == "wave"
    assert _norm_tension_bias("未定") == ""


def test_role_curve_invariants_hold_for_all_biases():
    for bias in ("", "sawtooth", "ramp", "wave"):
        for n in (5, 8, 10):
            curve = _role_curve(n, bias)
            names = [r for r, _ in curve]
            assert names[0] == "setup" and "twist" in names and "climax" in names
            assert all(0 <= t <= 1 for _, t in curve)
            climax_t = next(t for r, t in curve if r == "climax")
            setup_t = next(t for r, t in curve if r == "setup")
            assert climax_t >= setup_t


def test_default_curve_unchanged():
    # 无 bias 时与改造前的线性递增公式一致
    curve = _role_curve(8, "")
    for i, (r, t) in enumerate(curve):
        if r == "rising":
            assert abs(t - round(0.4 + 0.3 * (i / 7), 2)) < 1e-9


def test_sawtooth_has_more_local_peaks_than_default():
    def peaks(curve):
        ts = [t for _, t in curve]
        return sum(1 for i in range(1, len(ts) - 1) if ts[i] > ts[i - 1] and ts[i] > ts[i + 1])
    assert peaks(_role_curve(10, "sawtooth")) > peaks(_role_curve(10, ""))


def test_low_complexity_yields_fewer_subplots():
    # 低复杂度（爽文）
    r_low = _repo()
    r_low.set_tone_profile(ToneProfile(genre="xuanhuan_powerfantasy",
                                       primary_effect="catharsis_satisfaction", complexity="低"))
    Planner(r_low, llm=None, theme="逆袭").build_master(part_count=3)
    low_sub = sum(1 for n in r_low.list_reveal_nodes() if "副线" in n.description)

    # 高复杂度
    r_hi = _repo()
    r_hi.set_tone_profile(ToneProfile(genre="mystery", primary_effect="curiosity", complexity="高"))
    Planner(r_hi, llm=None, theme="迷案").build_master(part_count=3)
    hi_sub = sum(1 for n in r_hi.list_reveal_nodes() if "副线" in n.description)

    assert low_sub >= 1            # 仍保证 ≥1 副线
    assert low_sub < hi_sub        # 低复杂度副线更少
