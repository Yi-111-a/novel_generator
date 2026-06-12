"""确定性校验层（设计文档 §5）—— M1 的核心证明点。

零 token 检查角色输出的结构化动作：
  1. 引用的实体是否存在于 entities（防止凭空捏造实体）。
  2. 引用的 fact 是否在该角色账本内（防越权知情 —— "不幻觉"的关键）。
  3. 动作是否违反 physics_rules（不可变层硬约束）。

仅未通过项才需要升级到 LLM 纠正（§5 第 2 步）；M1 只暴露 needs_llm_fix 标志，不实现纠正。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .models import Action
from .repository import Repository


@dataclass
class Violation:
    code: str  # unknown_entity | unknown_fact | unauthorized_fact | physics_violation
    detail: str


@dataclass
class ValidationResult:
    ok: bool
    violations: list[Violation] = field(default_factory=list)

    @property
    def needs_llm_fix(self) -> bool:
        """是否存在可由廉价 LLM 纠正的违规（M1 仅标记，不执行）。"""
        return not self.ok

    def summary(self) -> str:
        if self.ok:
            return "PASS"
        return "FAIL: " + "; ".join(f"[{v.code}] {v.detail}" for v in self.violations)


class Validator:
    def __init__(self, repo: Repository) -> None:
        self.repo = repo
        self._physics_rules = repo.get_physics_rules()

    def _violates_physics(self, text: str) -> str | None:
        """朴素关键词匹配：动作文本命中任一物理法则的禁止关键词即判违规。

        physics_rules 形如 "修真世界没有手机/电话/电"。取每条规则中 '没有' 之后、
        以 '/'、'、'、',' 分隔的关键词作为禁止词表。
        """
        haystack = text
        for rule in self._physics_rules:
            banned = _extract_banned_terms(rule)
            for term in banned:
                if term and term in haystack:
                    return f"动作内容命中禁止项「{term}」，违反规则：{rule}"
        return None

    def check(self, agent_id: str, action: Action) -> ValidationResult:
        violations: list[Violation] = []

        # 1. 引用实体存在性（含 target，去重）
        ref_entities = list(dict.fromkeys(action.referenced_entities + ([action.target] if action.target else [])))
        for ent in ref_entities:
            if not self.repo.entity_exists(ent):
                violations.append(
                    Violation("unknown_entity", f"引用了不存在的实体：{ent}")
                )

        # 2. 越权知情：引用的 fact 必须在自己账本内
        for fid in action.referenced_facts:
            if not self.repo.fact_exists(fid):
                violations.append(
                    Violation("unknown_fact", f"引用了世界库中不存在的 fact：{fid}")
                )
            elif not self.repo.agent_knows_fact(agent_id, fid):
                violations.append(
                    Violation(
                        "unauthorized_fact",
                        f"越权知情：{agent_id} 的账本里没有 fact「{fid}」",
                    )
                )

        # 3. 物理法则（扫描动作的自然语言字段）
        text = " ".join(
            filter(None, [action.intent, action.dialogue, action.inner_thought])
        )
        phys = self._violates_physics(text)
        if phys:
            violations.append(Violation("physics_violation", phys))

        return ValidationResult(ok=not violations, violations=violations)


def _extract_banned_terms(rule: str) -> list[str]:
    marker = "没有"
    if marker in rule:
        tail = rule.split(marker, 1)[1]
    else:
        tail = rule
    # 统一分隔符
    for sep in ("、", ",", "，"):
        tail = tail.replace(sep, "/")
    return [t.strip() for t in tail.split("/") if t.strip()]
