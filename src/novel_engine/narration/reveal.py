"""揭示决策：操作读者账本（设计文档 §4.6）。

进入一场前，计算：
   mystery_set → 哪些真相可在本场揭（推进悬念）
   irony_set   → 读者已知而 POV 不知 → 可制造戏剧反讽
本场决定：揭 X、藏 Y。揭示 → 写入 reader_knowledge。

隔离约束：本场能"揭"的，必须是 POV 角色此刻账本里**确实知道**的事实——
叙述者看不到世界全貌，不能凭空向读者揭示 POV 不知道的真相。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..models import Event, ReaderKnowledge
from ..repository import Repository


@dataclass
class RevealPlan:
    reveal: list[str] = field(default_factory=list)   # 本场要揭给读者的 fact_id
    conceal: list[str] = field(default_factory=list)  # 本场 POV 知道但选择藏住的 fact_id
    irony: list[str] = field(default_factory=list)    # 读者已知、POV 不知 → 反讽素材


def plan_reveals(
    repo: Repository,
    pov: str,
    source_events: list[Event],
    reveal_budget: int = 2,
) -> RevealPlan:
    """决定本场揭/藏。只在"POV 知道 ∩ 读者还不知道"里挑要揭的，预算控制节奏。"""
    pov_known = {k.fact_id: k for k in repo.get_agent_ledger(pov)}

    # 本场事件直接产出的 facts，且 POV 确实知道、读者还不知道 → 候选揭示
    candidates: list[str] = []
    for ev in source_events:
        for f in repo.get_facts_by_event(ev.event_id):
            if f.fact_id in pov_known and not repo.reader_knows(f.fact_id):
                candidates.append(f.fact_id)

    reveal = candidates[:reveal_budget]
    conceal = candidates[reveal_budget:]
    irony = repo.irony_set(pov)
    return RevealPlan(reveal=reveal, conceal=conceal, irony=irony)


def commit_reveals(
    repo: Repository, plan: RevealPlan, pov: str, discourse_pos: int
) -> list[str]:
    """把本场揭示写入读者账本；读者看到的是 POV 持有的版本（可能 ≠ canonical）。"""
    pov_known = {k.fact_id: k for k in repo.get_agent_ledger(pov)}
    revealed: list[str] = []
    for fid in plan.reveal:
        item = pov_known.get(fid)
        if item is None:
            continue
        repo.reveal_to_reader(
            ReaderKnowledge(
                fact_id=fid,
                revealed_version=item.version_content,
                revealed_discourse_pos=discourse_pos,
                via_pov=pov,
            )
        )
        revealed.append(fid)
    return revealed
