"""两个反无聊监控（设计文档 §3.4）。

- 反完美检查：fatal_flaw 必须周期性真的让角色付出代价。连续 N 个节拍弱点零成本 → 报警，
  导演应安排一个让弱点反噬的处境（prefer_flaw=True）。
- 弧线追踪：非"静止角色"长期 arc_state 零变化 → 报警。
"""
from __future__ import annotations

from dataclasses import dataclass

from .repository import Repository


@dataclass
class Alarm:
    kind: str  # flaw_free_too_long | arc_stalled
    agent_id: str
    detail: str


@dataclass
class CompletenessRow:
    agent_id: str
    name: str
    arc_changed: bool       # 弧线是否真的动过（非静止角色应为 True）
    cost_count: int         # 代价台账长度（递增可见化）
    flaw_paid: bool         # 弱点是否至少反噬过一次
    ok: bool


def story_completeness(repo: Repository) -> list[CompletenessRow]:
    """§3.4 完成度报告：每个非静止角色是否被改变、是否付出代价、弱点是否反噬过。"""
    rows: list[CompletenessRow] = []
    for p in repo.list_personas():
        if p.arc_state.get("static"):
            continue
        arc_changed = bool(p.arc_state.get("changed"))
        cost_count = len(p.cost_ledger)
        flaw_paid = int(p.arc_state.get("last_flaw_cost_tick", 0)) > 0
        ok = arc_changed and cost_count > 0 and (flaw_paid or not p.fatal_flaw)
        rows.append(
            CompletenessRow(p.agent_id, p.name, arc_changed, cost_count, flaw_paid, ok)
        )
    return rows


class Monitors:
    def __init__(self, repo: Repository, flaw_max_free: int = 3, arc_max_stall: int = 4) -> None:
        self.repo = repo
        self.flaw_max_free = flaw_max_free
        self.arc_max_stall = arc_max_stall

    def check(self, tick: int) -> list[Alarm]:
        alarms: list[Alarm] = []
        for p in self.repo.list_personas():
            if p.arc_state.get("static"):  # 设定为静止角色 → 不追弧线/弱点
                continue

            # 反完美：距上次弱点付代价的节拍数
            last_flaw_cost = int(p.arc_state.get("last_flaw_cost_tick", 0))
            if p.fatal_flaw and (tick - last_flaw_cost) > self.flaw_max_free:
                alarms.append(
                    Alarm(
                        "flaw_free_too_long",
                        p.agent_id,
                        f"{p.name} 的弱点「{p.fatal_flaw}」已连续 {tick - last_flaw_cost} 拍零成本，"
                        f"该安排一次反噬。",
                    )
                )

            # 弧线追踪：距上次 arc 变化的节拍数
            last_change = int(p.arc_state.get("last_change_tick", 0))
            if (tick - last_change) > self.arc_max_stall:
                alarms.append(
                    Alarm(
                        "arc_stalled",
                        p.agent_id,
                        f"{p.name} 的弧线已停滞 {tick - last_change} 拍，长期零变化。",
                    )
                )
        return alarms
