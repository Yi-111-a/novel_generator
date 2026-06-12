"""伏笔台账 + 诚实性闸门（设计文档 §1.5 / §4.7）。

伏笔指向世界库里一条**真实存在**的 fact → 保证"公平"（真相一直在）。
生命周期：plant(open) → pay_off(paid_off)；或 abandon。

§4.7 诚实性闸门：在任一候选结局被定为最终结局前，所有 must_resolve 的伏笔必须
  (a) linked_fact 在世界库中存在；
  (b) 已安排 target_payoff_beat。
否则阻止收尾，回填回收节拍。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from ..models import Beat, Ending, Foreshadow
from ..repository import Repository


class ForeshadowLedger:
    def __init__(self, repo: Repository) -> None:
        self.repo = repo

    def plant(
        self,
        question: str,
        linked_fact_id: str,
        discourse_pos: int,
        must_resolve: bool = True,
        target_payoff_beat: str | None = None,
    ) -> Foreshadow | None:
        """埋下伏笔。linked_fact 必须在世界库中存在（公平性），否则拒绝。"""
        if not self.repo.fact_exists(linked_fact_id):
            return None
        # 同一 fact 已有 open 伏笔则不重复埋
        for fs in self.repo.foreshadows_for_fact(linked_fact_id):
            if fs.status == "open":
                return fs
        fs = Foreshadow(
            foreshadow_id=f"fs_{uuid.uuid4().hex[:8]}",
            question=question,
            linked_fact_id=linked_fact_id,
            planted_discourse_pos=discourse_pos,
            must_resolve=must_resolve,
            target_payoff_beat=target_payoff_beat,
            status="open",
        )
        self.repo.upsert_foreshadow(fs)
        return fs

    def pay_off_for_fact(self, fact_id: str, discourse_pos: int) -> list[Foreshadow]:
        """某 fact 在本场被揭示 → 命中它的 open 伏笔标记 paid_off。"""
        paid: list[Foreshadow] = []
        for fs in self.repo.foreshadows_for_fact(fact_id):
            if fs.status == "open":
                fs.status = "paid_off"
                fs.payoff_discourse_pos = discourse_pos
                self.repo.upsert_foreshadow(fs)
                paid.append(fs)
        return paid

    def list_open(self) -> list[Foreshadow]:
        return [f for f in self.repo.list_foreshadows() if f.status == "open"]


@dataclass
class HonestyReport:
    ok: bool
    problems: list[str] = field(default_factory=list)
    blocking: list[Foreshadow] = field(default_factory=list)

    def summary(self) -> str:
        if self.ok:
            return "PASS：所有 must_resolve 伏笔已回收或已排回收节拍。"
        return "BLOCK：" + "；".join(self.problems)


def honesty_gate(repo: Repository) -> HonestyReport:
    """§4.7 收尾前检查。返回是否放行 + 阻塞原因。"""
    problems: list[str] = []
    blocking: list[Foreshadow] = []
    for fs in repo.list_foreshadows():
        if not fs.must_resolve or fs.status == "paid_off":
            continue
        if fs.status == "abandoned":
            problems.append(f"{fs.foreshadow_id} 被放弃，但它 must_resolve")
            blocking.append(fs)
            continue
        # open 的 must_resolve 伏笔：检查 (a) linked_fact 存在 (b) 已排 payoff beat
        if not repo.fact_exists(fs.linked_fact_id):
            problems.append(f"{fs.foreshadow_id} 的 linked_fact「{fs.linked_fact_id}」不在世界库（不公平）")
            blocking.append(fs)
        elif not fs.target_payoff_beat:
            problems.append(f"{fs.foreshadow_id}「{fs.question}」尚未安排回收节拍")
            blocking.append(fs)
    return HonestyReport(ok=not problems, problems=problems, blocking=blocking)


def backfill_payoff_beats(repo: Repository) -> list[Beat]:
    """为缺回收节拍的 open must_resolve 伏笔回填一个 decision 节拍（§4.7"回填回收节拍"）。"""
    created: list[Beat] = []
    next_order = (max((b.sequence_order for b in repo.list_beats()), default=0)) + 1
    for fs in repo.list_foreshadows():
        if fs.must_resolve and fs.status == "open" and not fs.target_payoff_beat:
            if not repo.fact_exists(fs.linked_fact_id):
                continue  # 连真相都没有 → 不能假装回收
            beat = Beat(
                beat_id=f"beat_payoff_{fs.foreshadow_id}",
                sequence_order=next_order,
                type="decision",
                goal=f"回收伏笔：{fs.question}",
            )
            repo.upsert_beat(beat)
            fs.target_payoff_beat = beat.beat_id
            repo.upsert_foreshadow(fs)
            created.append(beat)
            next_order += 1
    return created


def finalize_ending(repo: Repository, ending_id: str, auto_backfill: bool = False) -> HonestyReport:
    """尝试把某候选结局定为 final。先过诚实性闸门；可选先回填回收节拍。"""
    if auto_backfill:
        backfill_payoff_beats(repo)
    report = honesty_gate(repo)
    if not report.ok:
        return report  # 阻止收尾
    endings = {e.ending_id: e for e in repo.list_endings()}
    target = endings.get(ending_id)
    if target is None:
        return HonestyReport(ok=False, problems=[f"结局 {ending_id} 不存在"])
    for e in endings.values():
        e.status = "final" if e.ending_id == ending_id else "candidate"
        repo.upsert_ending(e)
    return report
