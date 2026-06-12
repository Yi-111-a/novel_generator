"""背景按需滴灌（设计文档 §4.3）。

每个 world_bible 背景事实挂 release_rule：
   触发条件 = POV角色撞上它 / 读者已对相关角色有情感投入 / 某节拍需要
只有触发才允许写进散文，并登记进 reader_knowledge。
→ 开篇零铺垫；背景在喘息场景里慢慢渗。

M4 实现：release_rule = {fact_id, min_discourse_pos}。喘息场景到达时，
若 min_discourse_pos 已满足且该背景未揭 → 滴灌一条，写入 reader_knowledge。
"""
from __future__ import annotations

from ..models import ReaderKnowledge
from ..repository import Repository


class Exposition:
    def __init__(self, repo: Repository) -> None:
        self.repo = repo
        self.rules = repo.get_exposition_rules()

    def drip(self, discourse_pos: int, pov: str | None = None) -> list[str]:
        """在（喘息）场景滴灌满足释放条件、且读者尚未知的背景事实。返回新揭示的 fact_id。"""
        revealed: list[str] = []
        for rule in self.rules:
            fid = rule.get("fact_id")
            if not fid or not self.repo.fact_exists(fid):
                continue
            if self.repo.reader_knows(fid):
                continue
            if discourse_pos < int(rule.get("min_discourse_pos", 0)):
                continue  # 触发条件未满足：读者还没投入到该读这段背景
            fact = self.repo.get_fact(fid)
            self.repo.reveal_to_reader(
                ReaderKnowledge(
                    fact_id=fid,
                    revealed_version=fact.canonical_content,
                    revealed_discourse_pos=discourse_pos,
                    via_pov=pov,
                )
            )
            revealed.append(fid)
        return revealed
