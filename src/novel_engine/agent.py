"""角色决策 Agent（设计文档 §6.1）。

隔离的关键：拼 prompt 时"你只知道以下信息"段**只**来自该角色账本
（repository.get_agent_ledger），其余世界状态一律不进 prompt。
"""
from __future__ import annotations

import json

from .llm.base import LLMClient
from .models import Action, KnowledgeItem, Persona
from .repository import Repository

# §3 入戏式：第二人称、只演"我是谁 + 此刻处境"，不替剧情操心。
_SYSTEM_TMPL = """你是{name}。{one_liner}
你的本性：{nature}
你说话的方式：{voice}

【你此刻知道的】（只有这些；不得使用或编造任何未列出的信息、人物、地点）：
{ledger}

【你刚经历的】：
{residue}

可在动作中引用的实体 id（仅这些）：
{entity_ids}

【人名硬约束】你的对白（dialogue）与行动里，只能称呼上文【你此刻知道的】或处境中**已经出现过**的人。
绝不可发明、杜撰任何新的人名、绰号、化名或第三者；需要指代不认识的人时，只用「那人」「有人」「一个女人」这类模糊说法。

现在，导演给你布置了一个处境（不含台词，剧情往哪走不是你该操心的）。
以你的本性和你想要的东西，你**此刻**会怎么做？只产出**符合你身份**的、结构化的行动。
【主动性】如果处境已经把你逼到该做决断的关口，就**采取实际行动、做出选择**（哪怕有代价、不可逆、会暴露自己），
推动局面发生改变；不要一味观察、拖延、回避或"再等等看"——被动旁观不是你这种人在险局里的活法。
但要忠于本性，该谨慎时谨慎，不必硬充英雄。
【道德灰度】你的**本性优先于"正确"**：可以做出自私、短视、怯懦或有代价的选择，不必站在道德高地、
也不必让结果光明圆满。该动摇就动摇、该妥协就妥协；胜利可以要付代价，善意也可能酿成恶果。
不要总是选那个"最正确、最高尚"的选项——选那个**最像你这个人**的选项。
输出严格 JSON，字段如下：
{{
  "intent": "你打算做什么（动词短语）",
  "target": "动作针对的实体 id，没有则填 null",
  "dialogue": "你说出口的话，没有则空字符串",
  "inner_thought": "内心独白",
  "chosen_value": "这个选择优先了你哪个珍视之物 / 是否被弱点驱动",
  "referenced_facts": ["你在思考/行动中依据的 fact_id，仅限上面账本里出现过的"],
  "referenced_entities": ["你在动作中涉及的实体 id"]
}}
只输出 JSON，不要额外解释。"""


def _format_ledger(ledger: list[KnowledgeItem]) -> str:
    if not ledger:
        return "（你目前什么都不知道）"
    lines = []
    for k in ledger:
        lines.append(f"- [{k.fact_id}] {k.version_content}（可信度 {k.confidence}）")
    return "\n".join(lines)


def _format_values(persona: Persona) -> str:
    if not persona.values:
        return "（无）"
    return "、".join(
        f"{v.get('name')}(权重{v.get('weight')})" for v in persona.values
    )


class CharacterAgent:
    def __init__(self, repo: Repository, llm: LLMClient, memory=None, memory_k: int = 6) -> None:
        self.repo = repo
        self.llm = llm
        # 可选记忆层：给定后用 Mem0 式检索注入 top-k 相关记忆，而非整本账本（向后兼容：默认 None）
        self.memory = memory
        self.memory_k = memory_k

    def build_system_prompt(
        self, agent_id: str, allowed_entity_ids: list[str], situation: str = "",
        emotional_residue: str = "",
    ) -> str:
        persona = self.repo.get_persona(agent_id)
        if persona is None:
            raise ValueError(f"找不到 persona：{agent_id}")
        if self.memory is not None and situation:
            ledger = self.memory.retrieve(agent_id, situation, self.memory_k)
        else:
            ledger = self.repo.get_agent_ledger(agent_id)
        prof = self._profile(agent_id, persona)
        body = _SYSTEM_TMPL.format(
            name=prof["name"],
            one_liner=prof["one_liner"],
            nature=prof["nature"],
            voice=prof["voice"],
            ledger=_format_ledger(ledger),
            residue=emotional_residue or "（平静，无特别余温）",
            entity_ids="、".join(allowed_entity_ids) if allowed_entity_ids else "（无）",
        )
        # §16.5 文风契约前置块（项目级，约束表演层的行动与说话方式）+ 自检尾注
        tone = ""
        getter = getattr(self.repo, "tone_profile_prompt", None)
        if getter is not None:
            try:
                tone = getter()
            except Exception:
                tone = ""
        if tone:
            tp = self.repo.get_tone_profile()
            check = (f"\n\n产出后自检：你的行动与说话方式是否落在「{tp.primary_effect or '本书'}」的基调里？"
                     f"是否触犯任一禁忌（{('、'.join(tp.diction_dont[:6]) or '无')}）？若否，按基调修正。")
            return f"{tone}\n\n{body}{check}"
        return body

    def _profile(self, agent_id: str, persona: Persona) -> dict[str, str]:
        """身份画像：优先角色卡（§1 选角层），回退 persona。card 缺失时不报错（向前兼容）。"""
        card = None
        getter = getattr(self.repo, "get_card_for_agent", None)
        if getter is not None:
            try:
                card = getter(agent_id)
            except Exception:
                card = None
        name = (card.name if card and card.name else persona.name)
        one_liner = (card.one_liner if card and getattr(card, "one_liner", "") else "")
        # 本性：定义性特征 + 致命弱点 + 核心欲望 + 珍视（有什么用什么）
        bits: list[str] = []
        if card and getattr(card, "defining_trait", ""):
            bits.append(card.defining_trait)
        flaw = (getattr(card, "fatal_flaw", "") if card else "") or persona.fatal_flaw
        if flaw:
            bits.append(f"弱点是{flaw}")
        desire = (getattr(card, "core_desire", "") if card else "") or persona.want
        if desire:
            bits.append(f"你想要{desire}")
        if persona.values:
            bits.append("你珍视" + _format_values(persona))
        nature = "；".join(bits) or "（未定）"
        voice = ((card.voice_register if card and getattr(card, "voice_register", "") else "") or persona.voice or "（自然）")
        vh = getattr(card, "verbal_habits", "") if card else ""
        if vh:
            voice = f"{voice}（习惯：{vh}）"
        return {"name": name, "one_liner": one_liner, "nature": nature, "voice": voice}

    def decide(
        self,
        agent_id: str,
        situation: str,
        allowed_entity_ids: list[str],
        feedback: str | None = None,
        emotional_residue: str = "",
    ) -> Action:
        """给定处境，返回结构化动作。feedback 用于校验失败后的重选提示。"""
        system = self.build_system_prompt(agent_id, allowed_entity_ids, situation, emotional_residue)
        user = f"[眼前的处境]\n{situation}"
        if feedback:
            user += f"\n\n[上一次动作被驳回，请修正]\n{feedback}"
        raw = self.llm.complete(system, user)
        return _parse_action(raw)


def _parse_action(raw: str) -> Action:
    """解析模型输出的 JSON 动作；容错地剥离可能的代码围栏。"""
    text = raw.strip()
    if text.startswith("```"):
        # 去掉 ```json ... ``` 围栏
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # 解析失败 → 视为非法/空动作，交给校验层与引擎处理
        return Action(intent="(unparseable)", inner_thought=raw[:200])
    if not isinstance(data, dict):
        return Action(intent="(unparseable)", inner_thought=str(data)[:200])
    return Action.from_dict(data)
