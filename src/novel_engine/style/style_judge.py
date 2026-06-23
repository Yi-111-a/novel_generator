# -*- coding: utf-8 -*-
"""Layer 2 LLM 风格闸门：对比 prose vs AWS Language+Creativity claims。

论文维度路由：Language + Creativity → SceneWriter 强约束 + gate
                Plot + Development → 规划上下文参考（不做 gate）
原因：论文发现只有前两个维度在生成中可靠迁移。
"""
from __future__ import annotations

from ..llm.base import LLMClient
from ..models import AuthorWritingSheet


def style_judge_gate(
    prose: str,
    sheet: AuthorWritingSheet,
    llm: LLMClient,
    threshold: float = 0.6,
) -> tuple[bool, str]:
    """Layer 2 LLM-judge：prose 是否符合 AWS Language+Creativity claims。

    Returns (pass, feedback)。pass=True 时 feedback=""。
    threshold: 通过率阈值（0-1），低于此值判不通过。
    """
    if not sheet.is_set() or not prose.strip():
        return True, ""

    claims: list[str] = []
    for dim, label in [("language", "语言运用"), ("creativity", "创意手法")]:
        items = getattr(sheet, dim, [])
        for c in items[:5]:
            claims.append(f"[{label}] {c.claim}")

    if not claims:
        return True, ""

    claims_block = "\n".join(f"{i+1}. {c}" for i, c in enumerate(claims))

    system = (
        "你是文风一致性评审。给定一段小说正文和一份作者文风画像（claims 列表），"
        "逐条判断正文是否体现了该 claim。\n"
        "输出格式（每条一行）：\n"
        "  ✓ claim编号 — 理由（10字内）\n"
        "  ✗ claim编号 — 缺失原因 + 如何改（20字内）\n"
        "最后一行输出通过率（如 7/10）。只输出评审，不要复述原文。"
    )
    user = (
        f"【正文（节选）】\n{prose[:2000]}\n\n"
        f"【文风画像 claims】\n{claims_block}\n\n"
        "逐条评审。"
    )
    try:
        result = llm.complete(system, user).strip()
    except Exception:
        return True, ""

    lines = result.strip().split("\n")
    fail_lines = [l.strip() for l in lines if l.strip().startswith("✗")]

    passed = 0
    total = len(claims)
    for l in lines:
        if l.strip().startswith("✓"):
            passed += 1

    ratio = passed / total if total else 1.0
    if ratio >= threshold:
        return True, ""

    feedback_parts = ["文风偏离目标画像，以下 claims 未体现："]
    for fl in fail_lines[:5]:
        feedback_parts.append(fl)
    feedback_parts.append("请向上述文风特征靠拢重写。")
    return False, "\n".join(feedback_parts)
