"""角色记忆层（Mem0 式做法，本地自研、零外部依赖）。

学自 Mem0 的两件核心事，落在每个角色已有的 agent_knowledge 账本上：

1. **多信号检索**（retrieve）：角色决策时，不再把整本账本塞进 prompt，而是按
   「语义(关键词/bigram 重叠) + 新近度 + 置信度 + 实体命中」融合打分，取 top-k 注入。

2. **两段式巩固**（consolidate）：新记忆到达时，对照既有记忆做
   ADD / UPDATE / NOOP / DELETE —— 去重、用更可信的信念覆盖过期信念、消解冲突。
   （Mem0 把"事实随时间演变"视为开放问题；这里用 confidence 较高者胜来近似"演变"。）

嵌入策略：默认规则/关键词（中文按 2-gram），无需任何 key；
若将来要接外部嵌入服务，把 Embedder 换成调 API 的实现即可（memoryKey 的用武之地）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .models import KnowledgeItem
from .repository import Repository

# 检索打分权重
W_SEM = 0.6
W_RECENCY = 0.2
W_CONFIDENCE = 0.2
ENTITY_BOOST = 0.15
DEDUP_THRESHOLD = 0.85  # 文本近重复阈值（NOOP）


class Embedder(Protocol):
    """相似度后端协议。默认实现用关键词 bigram；可替换为外部嵌入服务。"""

    def similarity(self, a: str, b: str) -> float: ...


class KeywordEmbedder:
    """规则/关键词相似度：中文 2-gram 的 Jaccard。确定性、可离线、零成本。"""

    def _bigrams(self, s: str) -> set[str]:
        chars = [c for c in s if not c.isspace()]
        if len(chars) < 2:
            return set(chars)
        return {chars[i] + chars[i + 1] for i in range(len(chars) - 1)}

    def similarity(self, a: str, b: str) -> float:
        ba, bb = self._bigrams(a), self._bigrams(b)
        if not ba or not bb:
            return 0.0
        inter = len(ba & bb)
        union = len(ba | bb)
        return inter / union if union else 0.0


@dataclass
class ScoredMemory:
    item: KnowledgeItem
    score: float


class MemoryStore:
    def __init__(self, repo: Repository, embedder: Embedder | None = None) -> None:
        self.repo = repo
        self.embedder = embedder or KeywordEmbedder()
        # 缓存实体名，用于实体命中加权
        self._entity_names = {e.entity_id: e.name for e in repo.list_entities()}

    # ---------- 1. 多信号检索 ----------
    def retrieve(self, agent_id: str, query: str, k: int = 6) -> list[KnowledgeItem]:
        ledger = self.repo.get_agent_ledger(agent_id)
        if len(ledger) <= k:
            return ledger  # 账本本就不大，全给

        max_tick = max((it.learned_tick for it in ledger), default=0) or 1
        scored = [ScoredMemory(it, self._score(it, query, max_tick)) for it in ledger]
        scored.sort(key=lambda s: s.score, reverse=True)
        top = [s.item for s in scored[:k]]
        # 按时间还原顺序，读起来更自然
        top.sort(key=lambda it: it.learned_tick)
        return top

    def score_debug(self, agent_id: str, query: str) -> list[ScoredMemory]:
        ledger = self.repo.get_agent_ledger(agent_id)
        max_tick = max((it.learned_tick for it in ledger), default=0) or 1
        out = [ScoredMemory(it, self._score(it, query, max_tick)) for it in ledger]
        out.sort(key=lambda s: s.score, reverse=True)
        return out

    def _score(self, it: KnowledgeItem, query: str, max_tick: int) -> float:
        sem = self.embedder.similarity(query, it.version_content)
        recency = it.learned_tick / max_tick if max_tick else 0.0
        confidence = it.confidence
        score = W_SEM * sem + W_RECENCY * recency + W_CONFIDENCE * confidence
        # 实体命中：该记忆涉及的实体名出现在 query 里 → 加权
        fact = self.repo.get_fact(it.fact_id)
        if fact:
            for ent in fact.involved_entities:
                name = self._entity_names.get(ent, ent)
                if name and name in query:
                    score += ENTITY_BOOST
                    break
        return round(score, 4)

    # ---------- 2. 两段式巩固 ----------
    def consolidate(
        self,
        agent_id: str,
        fact_id: str,
        content: str,
        confidence: float,
        tick: int,
        source_event_id: str | None = None,
    ) -> str:
        """对一条到达的记忆做 ADD/UPDATE/NOOP/DELETE，返回操作名。"""
        if confidence <= 0.0:
            if self.repo.get_knowledge_entry(agent_id, fact_id):
                self.repo.delete_knowledge(agent_id, fact_id)
                return "DELETE"
            return "NOOP"

        existing = self.repo.get_knowledge_entry(agent_id, fact_id)
        item = KnowledgeItem(agent_id, fact_id, content, confidence, tick, source_event_id)

        if existing is None:
            # 与已有不同 fact 的近重复 → 视为已知，NOOP（去重）
            for e in self.repo.get_agent_ledger(agent_id):
                if self.embedder.similarity(e.version_content, content) >= DEDUP_THRESHOLD:
                    return "NOOP"
            self.repo.upsert_knowledge(item)
            return "ADD"

        if existing.version_content == content:
            return "NOOP"

        # 信念分歧：更可信者胜（近似"信念演变"）；同分则保留既有
        if confidence > existing.confidence + 1e-9:
            self.repo.upsert_knowledge(item)
            return "UPDATE"
        return "NOOP"
