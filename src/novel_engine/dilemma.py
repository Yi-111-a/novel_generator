"""内在冲突生成器（设计文档 §3 + §6.2）—— 导演的核心模块。

把"外部事件 → 逼出会改变自己的选择"做成可执行算法：
  1. 选目标角色：score = thread.priority*w1 + arc停滞时长*w2 + 主题相关度*w3
  2. 选冲突对：从 (want, values[], fatal_flaw) 挑两个"现在能相撞"的元素
  3. 构造处境：用世界状态搭一个"两者无法兼得"的具体情境（规则模板 / 可选 LLM §6.2）
  4. 封死退路：加时间压力/见证者/不可逆性
  5. 交权：把处境框给角色（不暗示正确答案）—— 在 director 里完成
  6. dilemma_score（§3.3）决定要不要用、放多重的戏

导演通过环境行使控制（§0 原则3）：冲突对由本模块确定，LLM 只负责把处境写"具体"，
绝不预设角色怎么选。
"""
from __future__ import annotations

import json

from .llm.base import LLMClient
from .models import Dilemma, Persona
from .repository import Repository

# 目标选择权重
W_PRIORITY = 1.0
W_STALL = 0.15
W_THEME = 0.6


class DilemmaGenerator:
    def __init__(self, repo: Repository, llm: LLMClient | None = None, theme: str = "") -> None:
        self.repo = repo
        self.llm = llm
        self.theme = theme

    # ---------- 1. 选目标角色 ----------
    def select_target(self, tick: int) -> str | None:
        threads = self.repo.list_threads()
        personas = {p.agent_id: p for p in self.repo.list_personas()}
        if not personas:
            return None

        best_agent, best_score = None, float("-inf")
        for aid, p in personas.items():
            # 该角色参与的最高优先级线
            prio = max(
                (t.priority_weight for t in threads if aid in t.involved_agents),
                default=0.3,
            )
            stall = tick - int(p.arc_state.get("last_change_tick", 0))
            theme_rel = _theme_relevance(self.theme, _persona_text(p))
            score = prio * W_PRIORITY + stall * W_STALL + theme_rel * W_THEME
            if score > best_score:
                best_agent, best_score = aid, score
        return best_agent

    # ---------- 2. 选冲突对 ----------
    def select_colliding_pair(self, persona: Persona, prefer_flaw: bool = False,
                              beat_idx: int = 0) -> tuple[str, str, str]:
        """返回 (元素A, 元素B, kind)；kind ∈ value_vs_value | want_vs_value | flaw_vs_value。

        瑕疵2 修复：同一角色在同一章不同 beat 间**轮换冲突维度**（beat_idx 取模），
        逼出角色不同侧面的挣扎，避免每场都是同一对张力 → 对白结构雷同。
        beat_idx=0 时与改造前行为一致（向后兼容）。
        """
        values = sorted(persona.values, key=lambda v: -float(v.get("weight", 0)))
        names = [v["name"] for v in values]
        top_value = names[0] if names else "某种珍视"

        if prefer_flaw and persona.fatal_flaw:  # 监控逼弱点时优先级最高，不参与轮换
            return persona.fatal_flaw, top_value, "flaw_vs_value"

        # 候选冲突维度（按 beat 轮换）。第 0 个保持与旧逻辑一致。
        candidates: list[tuple[str, str, str]] = []
        if len(names) >= 2:
            candidates.append((names[0], names[1], "value_vs_value"))
        if persona.want and names:
            candidates.append((persona.want, names[0], "want_vs_value"))
        if persona.fatal_flaw and names:
            candidates.append((persona.fatal_flaw, names[0], "flaw_vs_value"))
        if persona.want and len(names) >= 2:
            candidates.append((persona.want, names[1], "want_vs_value"))
        if not candidates:  # 极端：无 values
            if persona.want:
                candidates.append((persona.want, top_value, "want_vs_value"))
            else:
                candidates.append((persona.fatal_flaw or "冲动", top_value, "flaw_vs_value"))
        return candidates[max(0, beat_idx) % len(candidates)]

    # ---------- 3+4+6. 构造处境、封死退路、评分 ----------
    def generate(self, tick: int, target: str | None = None, prefer_flaw: bool = False,
                 beat_hint: str = "", beat_idx: int = 0) -> Dilemma | None:
        """beat_hint（B2）：导演规定本场要演到的**节拍**，作为"规定情境"的方向（不替角色决定怎么选）。
        beat_idx（瑕疵2）：本章第几个 beat，用于跨 beat 轮换冲突维度。"""
        target = target or self.select_target(tick)
        if target is None:
            return None
        persona = self.repo.get_persona(target)
        if persona is None:
            return None

        a, b, kind = self.select_colliding_pair(persona, prefer_flaw=prefer_flaw, beat_idx=beat_idx)
        threads = [t for t in self.repo.list_threads() if target in t.involved_agents]
        thread_id = threads[0].thread_id if threads else None

        if self.llm is not None:
            situation, stakes, why_no_escape = self._construct_via_llm(persona, a, b, beat_hint)
        else:
            situation, stakes, why_no_escape = self._construct_via_rule(persona, a, b, kind, beat_hint)

        score = self._score(persona, a, b, kind, why_no_escape)
        return Dilemma(
            target_agent=target,
            situation=situation,
            colliding_pair=(a, b),
            stakes=stakes,
            why_no_escape=why_no_escape,
            score=round(score, 3),
            thread_id=thread_id,
        )

    # ---------- 规则构造（默认、离线、确定性） ----------
    def _construct_via_rule(self, persona: Persona, a: str, b: str, kind: str,
                            beat_hint: str = "") -> tuple[str, str, str]:
        # 优先用道具/他人作为"具体的"承载物，避免拿目标角色自己当道具
        objects = [e for e in self.repo.list_entities() if e.type == "object"]
        others = [
            e for e in self.repo.list_entities()
            if e.type == "character" and e.entity_id != persona.agent_id
        ]
        pick = objects or others
        prop = pick[0].name if pick else "眼前之物"
        beat_lead = f"本场推进到：{beat_hint}。" if beat_hint else ""
        situation = (
            f"{beat_lead}此刻，{persona.name}被推到一个无法两全的处境：要守住「{a}」，"
            f"就必须亲手牺牲「{b}」；反之亦然。{prop}就在面前，没有第三条路。"
        )
        stakes = f"选「{a}」则失「{b}」，两边都是{persona.name}输不起的东西。"
        why_no_escape = "有见证者在场、时间紧迫、且一旦动手不可逆——轻松的折中选项已被堵死。"
        return situation, stakes, why_no_escape

    # ---------- LLM 构造（§6.2） ----------
    def _construct_via_llm(self, persona: Persona, a: str, b: str,
                           beat_hint: str = "") -> tuple[str, str, str]:
        system = (
            "你是小说导演的内在冲突构造器。设计一个具体处境，使两个元素无法兼得，"
            "封死轻松的第三选项，绝不预设角色会怎么选。只输出 JSON。"
        )
        beat_line = (f"本场必须把戏推进到这一**节拍**（导演规定的情境方向，但不要替角色决定怎么选）：{beat_hint}\n"
                     if beat_hint else "")
        user = (
            f"目标角色：{persona.name}\n"
            f"欲望：{persona.want}\n弱点：{persona.fatal_flaw}\n主题：{self.theme}\n"
            f"{beat_line}"
            f"必须相撞且无法兼得的两个元素：A=「{a}」 B=「{b}」\n"
            '输出严格 JSON：{"situation": "...", "stakes": "...", "why_no_escape": "..."}'
        )
        raw = self.llm.complete(system, user)  # type: ignore[union-attr]
        try:
            text = raw.strip().strip("`")
            if text.lower().startswith("json"):
                text = text[4:].strip()
            data = json.loads(text)
            return (
                str(data.get("situation", "")).strip(),
                str(data.get("stakes", "")).strip(),
                str(data.get("why_no_escape", "")).strip(),
            )
        except Exception:
            return self._construct_via_rule(persona, a, b, "want_vs_value", beat_hint)

    # ---------- §3.3 两难评分 ----------
    def _score(self, persona: Persona, a: str, b: str, kind: str, why_no_escape: str) -> float:
        weights = {v["name"]: float(v.get("weight", 0.5)) for v in persona.values}
        stakes = (weights.get(a, 0.85) + weights.get(b, 0.85)) / 2  # want 视作 0.85
        irreversibility = 0.8 if ("不可逆" in why_no_escape or "见证" in why_no_escape) else 0.4
        theme_relevance = _theme_relevance(self.theme, f"{a} {b}")
        flaw_pressure = 1.0 if kind == "flaw_vs_value" else 0.5
        return stakes + irreversibility + theme_relevance + flaw_pressure


def _persona_text(p: Persona) -> str:
    return " ".join([p.want, p.fatal_flaw] + [v.get("name", "") for v in p.values])


def _theme_relevance(theme: str, text: str) -> float:
    """朴素主题相关度：主题字符与文本的字符重叠比例（0..1）。离线确定性。"""
    if not theme:
        return 0.3
    theme_chars = {c for c in theme if c.strip()}
    if not theme_chars:
        return 0.3
    hit = sum(1 for c in theme_chars if c in text)
    return round(hit / len(theme_chars), 3)
