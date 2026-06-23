"""最小 tick 循环（设计文档 §2 的精简版）。

M1 单角色、无导演重规划。一个 tick：
  1. 取处境（demo 提供）。
  2. 角色行动（CharacterAgent.decide）。
  3. 确定性校验：通过→落库；失败→带 feedback 回退重选，超次数则丢弃。
  4. 落 event + fact（canonical 真相）。
  5. 按 perceivers 写 agent_knowledge（M1 只回写行动者本人）。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from .agent import CharacterAgent
from .models import Action, Event, Fact
from .repository import Repository
from .validator import ValidationResult, Validator


@dataclass
class TickResult:
    tick: int
    agent_id: str
    action: Action
    validation: ValidationResult
    attempts: int
    committed: bool
    event_id: str | None = None
    fact_id: str | None = None


class Engine:
    def __init__(
        self,
        repo: Repository,
        agent: CharacterAgent,
        validator: Validator,
        max_retries: int = 2,
        propagator: "Propagator | None" = None,
        consistency=None,
    ) -> None:
        self.repo = repo
        self.agent = agent
        self.validator = validator
        self.max_retries = max_retries
        # §3.3 出戏检测（可选）：动作明显违背本性且无外因 → 重抽一次；离线/无则跳过。
        self.consistency = consistency
        # 默认 propagator 只做直接感知（无扭曲）；多角色二手扭曲可由上层显式调用
        from .propagation import Propagator

        self.propagator = propagator or Propagator(repo)
        self._tick = 0

    def run_tick(
        self,
        agent_id: str,
        situation: str,
        allowed_entity_ids: list[str],
        location_id: str | None = None,
        perceivers: list[str] | None = None,
        tick: int | None = None,
    ) -> TickResult:
        self._tick = tick if tick is not None else self._tick + 1
        feedback: str | None = None
        action: Action | None = None
        result: ValidationResult | None = None
        attempts = 0

        residue = self._emotional_residue(agent_id)
        for attempt in range(self.max_retries + 1):
            attempts = attempt + 1
            action = self.agent.decide(agent_id, situation, allowed_entity_ids, feedback, residue)
            result = self.validator.check(agent_id, action)
            if not result.ok:
                feedback = result.summary()
                continue
            # 通过隔离/不幻觉后，再过出戏检测；明显出戏且仍有重抽机会 → 带反馈重抽
            if self.consistency is not None and attempt < self.max_retries:
                okc, reason = self.consistency.check(agent_id, action)
                if not okc:
                    feedback = f"[出戏] {reason}（请做出符合你本性的反应）"
                    continue
            break  # 隔离 + 一致性都过（或已无重抽机会）

        assert action is not None and result is not None
        if not result.ok:
            # 超出重试次数仍不通过 → 丢弃该 tick（不落库，保持真相源干净）
            return TickResult(
                tick=self._tick,
                agent_id=agent_id,
                action=action,
                validation=result,
                attempts=attempts,
                committed=False,
            )

        event_id, fact_id = self._commit(agent_id, action, location_id, perceivers)
        return TickResult(
            tick=self._tick,
            agent_id=agent_id,
            action=action,
            validation=result,
            attempts=attempts,
            committed=True,
            event_id=event_id,
            fact_id=fact_id,
        )

    def _emotional_residue(self, agent_id: str) -> str:
        """§4.2 情绪余温（若仓储支持）：注入"你刚经历的"。未启用则空。"""
        getter = getattr(self.repo, "emotional_residue_text", None)
        if getter is None:
            return ""
        try:
            return getter(agent_id) or ""
        except Exception:
            return ""

    def _commit(
        self,
        agent_id: str,
        action: Action,
        location_id: str | None,
        perceivers: list[str] | None = None,
    ) -> tuple[str, str]:
        event_id = f"ev_{uuid.uuid4().hex[:8]}"
        fact_id = f"f_{uuid.uuid4().hex[:8]}"

        # 默认仅行动者感知；上层可传入在场的多个角色
        perceivers = list(dict.fromkeys((perceivers or [agent_id]) + [agent_id]))

        ev = Event(
            event_id=event_id,
            story_time=self._tick,
            actors=[agent_id],
            action_type=action.intent,
            payload={
                "target": action.target,
                "dialogue": action.dialogue,
                "inner_thought": action.inner_thought,
                "chosen_value": action.chosen_value,
            },
            location_id=location_id,
            perceivers=perceivers,
        )
        self.repo.append_event(ev)

        # 由动作产出一条 canonical fact（事件型）
        content = _describe(agent_id, action)
        involved = [agent_id] + ([action.target] if action.target else [])
        fact = Fact(
            fact_id=fact_id,
            fact_type="event",
            canonical_content=content,
            structured={"actor": agent_id, "intent": action.intent, "target": action.target},
            story_time=self._tick,
            location_id=location_id,
            involved_entities=involved,
            source_event_id=event_id,
        )
        self.repo.append_fact(fact)

        # 直接感知：在场者拿到 canonical 版本（无扭曲）。二手转述的扭曲可通过 propagator.tell 处理
        self.propagator.perceive(fact_id, content, perceivers, self._tick, event_id)
        return event_id, fact_id


def _describe(agent_id: str, action: Action) -> str:
    parts = [f"{agent_id} {action.intent}"]
    if action.target:
        parts.append(f"（针对 {action.target}）")
    if action.dialogue:
        parts.append(f"说：「{action.dialogue}」")
    return "".join(parts)
