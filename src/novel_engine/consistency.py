"""出戏检测（§3.3）—— "角色一致性"的硬保证，与隔离/不幻觉校验同层。

判断一次动作是否与角色本性**明显矛盾且无外因支撑**（如怯懦者无端突然勇猛）。
命中 → 让引擎带反馈重抽一次；有外因事件支撑 → 放行。

设计取舍：离线（无 LLM）一律放行——绝不在没有判断力时误拦，保证流水线可离线跑通。
判断由廉价 LLM 完成，且只在"明显出戏"时返回 True，避免过度敏感。
"""
from __future__ import annotations

import json

from .llm.base import LLMClient
from .models import Action
from .repository import Repository


class InCharacterChecker:
    def __init__(self, repo: Repository, llm: LLMClient | None = None) -> None:
        self.repo = repo
        self.llm = llm

    def check(self, agent_id: str, action: Action) -> tuple[bool, str]:
        """返回 (ok, reason)。ok=False 表示明显出戏、应重抽。"""
        if self.llm is None:
            return True, ""  # 离线宽松：不拦
        persona = self.repo.get_persona(agent_id)
        if persona is None:
            return True, ""
        nature = self._nature(agent_id, persona)
        recent = self._recent_external(agent_id)
        act = f"intent={action.intent}；对象={action.target}；台词={action.dialogue}；内心={action.inner_thought}"
        system = (
            "你是剧组的'表演指导'。判断一个角色的这次动作是否**明显违背其本性、且没有外因事件支撑**"
            "（例：一贯怯懦者在无任何威胁/刺激下突然主动搏命）。"
            "只有**明显**出戏才算数；符合性格、或有外因可解释的，都不算。只输出 JSON。"
        )
        user = (
            f"角色本性：{nature}\n"
            f"最近发生（可作外因）：{recent or '（无）'}\n"
            f"本次动作：{act}\n"
            '输出严格 JSON：{"ooc": true/false, "reason": "若出戏，一句话说明哪里矛盾"}'
        )
        try:
            raw = self.llm.complete(system, user)
            text = raw.strip().strip("`")
            if text.lower().startswith("json"):
                text = text[4:].strip()
            i, j = text.find("{"), text.rfind("}")
            data = json.loads(text[i:j + 1] if 0 <= i < j else text)
            ooc = bool(data.get("ooc"))
            return (not ooc), str(data.get("reason", ""))
        except Exception:
            return True, ""  # 解析失败也放行，不阻塞

    def _nature(self, agent_id: str, persona) -> str:
        bits = []
        card = None
        getter = getattr(self.repo, "get_card_for_agent", None)
        if getter is not None:
            try:
                card = getter(agent_id)
            except Exception:
                card = None
        if card and getattr(card, "defining_trait", ""):
            bits.append(card.defining_trait)
        if persona.fatal_flaw:
            bits.append(f"弱点：{persona.fatal_flaw}")
        if persona.want:
            bits.append(f"欲望：{persona.want}")
        if persona.values:
            bits.append("珍视：" + "、".join(v.get("name", "") for v in persona.values))
        return "；".join(bits) or persona.name

    def _recent_external(self, agent_id: str, k: int = 5) -> str:
        evs = [e for e in self.repo.list_events() if agent_id in (e.perceivers or []) or agent_id in (e.actors or [])]
        bits = []
        for e in evs[-k:]:
            note = e.payload.get("note") or e.payload.get("dialogue") or e.action_type
            bits.append(str(note)[:40])
        return "；".join(bits)
