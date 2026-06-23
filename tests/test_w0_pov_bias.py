"""W0 主角 POV 偏置（硬上限优先：全书 ≥70% 章主角主讲）。

覆盖：节律 helper `_pov_lead`、偏置 helper `_bias_pov`、以及离线整书 ≥70% 的端到端占比。
"""
from __future__ import annotations

from collections import Counter

from novel_engine import db
from novel_engine.models import Entity, Persona
from novel_engine.planner import Planner
from novel_engine.repository import Repository


def _repo() -> Repository:
    r = Repository(db.connect(":memory:"))
    for aid, name in [("hero", "沈砚"), ("ally", "赵九"), ("foe", "苏静")]:
        r.insert_entity(Entity(aid, "character", name, {}))
        r.insert_persona(Persona(agent_id=aid, name=name, want="x",
                                 values=[{"name": "v", "weight": 0.5}]))
    return r


# ---------------- _pov_lead 节律 ----------------
def test_pov_lead_defaults_to_hero():
    p = Planner(_repo(), llm=None, theme="x")
    elig = ["hero", "ally", "foe"]
    for seq in (1, 2, 3, 5, 6, 7, 9, 10, 11):   # 非 4 的倍数 → 主角主讲
        lead, ok = p._pov_lead(seq, elig, "hero")
        assert ok and lead == "hero", (seq, lead)


def test_pov_lead_locks_hero_every_chapter():
    # 旧版每全书第 4/8/12 章轮配角主讲；f97f1e8 起改为「全程锁主角视角」纪律，
    # 主角 eligible 时恒主角主讲（群像由 cast/beat 体现，不再切章主视角）。
    p = Planner(_repo(), llm=None, theme="x")
    elig = ["hero", "ally", "foe"]
    leads = [p._pov_lead(s, elig, "hero")[0] for s in (4, 8, 12)]
    assert leads == ["hero", "hero", "hero"]        # 第 4/8/12 章仍主角主讲


def test_pov_lead_hero_not_eligible_falls_back():
    p = Planner(_repo(), llm=None, theme="x")
    lead, ok = p._pov_lead(1, ["ally", "foe"], "hero")  # 主角不在 eligible（藏身份/缺席）
    assert lead is None and ok is False


# ---------------- _bias_pov 多数化 ----------------
def test_bias_pov_hero_strict_majority():
    p = Planner(_repo(), llm=None, theme="x")
    pov, beats = p._bias_pov("hero", True, ["hero", "ally", "foe"],
                             ["ally", "foe", "ally"], "hero")
    assert pov == "hero"
    assert beats.count("hero") >= 2                  # n=3 → 严格多数 ≥2


def test_bias_pov_supporting_chapter_lead_majority():
    p = Planner(_repo(), llm=None, theme="x")
    pov, beats = p._bias_pov("ally", True, ["hero", "ally"],
                             ["hero", "hero", "ally"], "ally")
    assert pov == "ally"
    assert beats.count("ally") >= 2


def test_bias_pov_blocked_hero_keeps_beat_majority():
    p = Planner(_repo(), llm=None, theme="x")
    pov, beats = p._bias_pov(None, False, ["ally", "foe"],
                             ["ally", "ally", "foe"], "ally")
    assert pov == "ally"                            # 退回 beat 多数，不强加
    assert beats == ["ally", "ally", "foe"]


# ---------------- 端到端：离线整书 ≥70% 主角主讲 ----------------
def test_hero_pov_at_least_70pct_offline():
    r = _repo()
    p = Planner(r, llm=None, theme="x")
    p.build_master(part_count=1, arcs_per_part=2)
    p.plan_next_arc()
    povs: list[str] = []
    for _ in range(10):
        ch = p.next_chapter()
        if ch is None:
            break
        povs.append(ch.pov_agent)
    assert povs, "应生成若干章"
    hero_n = sum(1 for x in povs if x == "hero")
    assert hero_n / len(povs) >= 0.7, dict(Counter(povs))
    # 配角主讲章仍然存在（群像不至于完全单一），但占比 ≤30%
    assert (len(povs) - hero_n) / len(povs) <= 0.3
