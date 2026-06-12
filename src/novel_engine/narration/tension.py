"""张弛曲线调度（设计文档 §4.4）。

维护目标张力曲线（起—顶—松—起得更高）。高潮节拍后主动插入"喘息节拍"
（target_tension 低），喘息场景承载背景渗透 + 人物/关系展开。
监控：连续高张力 → 报警（疲劳 = bug）。

实现：把高 drama 的入选事件按话语序排好，遇到"连续高张力"就从低 drama 的
被弃事件里取一个当**喘息**插进去，把曲线压下来再起。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..models import Event
from ..repository import Repository

HIGH = 0.66  # 高张力阈值


@dataclass
class ScheduledScene:
    event: Event
    target_tension: float
    role: str  # peak | rising | breather


@dataclass
class TensionSchedule:
    scenes: list[ScheduledScene] = field(default_factory=list)
    alarms: list[str] = field(default_factory=list)

    def curve(self) -> list[float]:
        return [s.target_tension for s in self.scenes]


class TensionScheduler:
    def __init__(self, repo: Repository, max_consecutive_high: int = 2) -> None:
        self.repo = repo
        self.max_consecutive_high = max_consecutive_high

    def _drama(self, ev: Event) -> float:
        return self.repo.get_event_drama_score(ev.event_id) or 0.0

    def schedule(self, selected: list[Event], breathers: list[Event]) -> TensionSchedule:
        """在连续高张力之间插入喘息事件，生成目标张力曲线。"""
        sched = TensionSchedule()
        pool = list(breathers)
        consecutive_high = 0

        for ev in selected:
            t = self._drama(ev)
            is_high = t >= HIGH

            if is_high and consecutive_high >= self.max_consecutive_high:
                # 该喘口气了：插一个低张力喘息场景（用被弃的低 drama 事件）
                if pool:
                    b = pool.pop(0)
                    sched.scenes.append(
                        ScheduledScene(b, round(min(self._drama(b), 0.3), 3), "breather")
                    )
                else:
                    sched.alarms.append(
                        f"连续 {consecutive_high} 场高张力且无喘息素材可插 → 疲劳报警"
                    )
                consecutive_high = 0

            role = "peak" if is_high else "rising"
            sched.scenes.append(ScheduledScene(ev, t, role))
            consecutive_high = consecutive_high + 1 if is_high else 0

        return sched
