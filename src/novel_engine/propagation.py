"""信息传播与扭曲（设计文档 §2 步骤5、§1.3）。

两种传播：
  1. 直接感知（perceivers）：亲历者拿到 canonical 版本，confidence 高。
  2. 二手转述（tell）：一个角色把自己账本里的某 fact 讲给另一个角色；
     转述会**扭曲**——version_content 可能 ≠ canonical，且 confidence 衰减。
     这正是 §1.3 所说"误传/扭曲"的来源，也是 conflict_pairs 的种子。

扭曲器（Distorter）可插拔：默认 RuleDistorter（确定性，便于测试与离线跑），
后续可换 LLM 版本做更自然的口耳相传变形。
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from .models import KnowledgeItem
from .repository import Repository


class Distorter(ABC):
    @abstractmethod
    def distort(self, source_version: str, source_confidence: float, hops: int) -> tuple[str, float]:
        """给定上游持有的版本/可信度与传播跳数，返回下游接收到的(版本, 可信度)。"""
        raise NotImplementedError


class RuleDistorter(Distorter):
    """确定性扭曲：每跳可信度乘以 decay；二手及以上加'据传'前缀并标注不确定。

    确定性让测试可断言 version_content ≠ canonical，也让离线 demo 可复现。
    """

    def __init__(self, decay: float = 0.6, rumor_threshold: float = 0.8) -> None:
        self.decay = decay
        self.rumor_threshold = rumor_threshold

    def distort(self, source_version: str, source_confidence: float, hops: int) -> tuple[str, float]:
        conf = round(source_confidence * (self.decay ** max(hops, 1)), 3)
        if conf < self.rumor_threshold:
            # 已不可全信 → 文本被改写为传闻版本（version ≠ canonical）
            core = source_version.rstrip("。.").strip()
            version = f"据传，{core}（具体不详）"
        else:
            version = source_version
        return version, conf


class Propagator:
    def __init__(self, repo: Repository, distorter: Distorter | None = None, memory=None) -> None:
        self.repo = repo
        self.distorter = distorter or RuleDistorter()
        # 可选记忆层：给定后写入走 Mem0 式巩固(ADD/UPDATE/NOOP/DELETE)，否则直接 insert（向后兼容）
        self.memory = memory

    def _write(self, item: KnowledgeItem) -> None:
        if self.memory is not None:
            self.memory.consolidate(
                item.agent_id, item.fact_id, item.version_content,
                item.confidence, item.learned_tick, item.source_event_id,
            )
        else:
            self.repo.insert_knowledge(item)

    def perceive(self, fact_id: str, canonical: str, perceivers: list[str], tick: int,
                 source_event_id: str | None = None) -> None:
        """直接感知：亲历者拿到 canonical 版本（confidence=1.0）。"""
        for pid in perceivers:
            self._write(
                KnowledgeItem(
                    agent_id=pid,
                    fact_id=fact_id,
                    version_content=canonical,
                    confidence=1.0,
                    learned_tick=tick,
                    source_event_id=source_event_id,
                )
            )

    def tell(self, speaker: str, listener: str, fact_id: str, tick: int) -> KnowledgeItem | None:
        """speaker 把自己知道的 fact 转述给 listener；施加扭曲后写入 listener 账本。

        返回写入 listener 的条目；若 speaker 自己都不知道该 fact，或 listener 已知，返回 None。
        """
        speaker_item = _find(self.repo.get_agent_ledger(speaker), fact_id)
        if speaker_item is None:
            return None  # speaker 不知道，无从转述（隔离：不能凭空传播）
        if self.repo.agent_knows_fact(listener, fact_id):
            return None  # 已知则不覆盖（M2 简化：先到先得）

        # speaker 是亲历者(confidence≈1) → listener 为第 1 跳二手；否则跳数累加
        hops = 1 if speaker_item.confidence >= 0.999 else 2
        version, conf = self.distorter.distort(speaker_item.version_content, speaker_item.confidence, hops)
        item = KnowledgeItem(
            agent_id=listener,
            fact_id=fact_id,
            version_content=version,
            confidence=conf,
            learned_tick=tick,
            source_event_id=speaker_item.source_event_id,
        )
        self._write(item)
        return item


def _find(ledger: list[KnowledgeItem], fact_id: str) -> KnowledgeItem | None:
    for k in ledger:
        if k.fact_id == fact_id:
            return k
    return None
