from __future__ import annotations

from typing import Any


def should_trigger_local_revision(score: dict[str, Any]) -> bool:
    return bool(
        score.get("finalScore", 1.0) < 0.72
        or score.get("caricaturePenalty", 0.0) >= 0.25
        or score.get("repetitionPenalty", 0.0) >= 0.22
        or score.get("voiceCollapsePenalty", 0.0) >= 0.18
        or score.get("sourceOverlapPenalty", 0.0) >= 0.18
    )


def build_local_revision_feedback(score: dict[str, Any]) -> str:
    issues: list[str] = []
    if score.get("caricaturePenalty", 0.0) >= 0.25:
        issues.append("连续显眼风格词或短句过密，压低表演感。")
    if score.get("repetitionPenalty", 0.0) >= 0.22:
        issues.append("句法模板重复偏高，换开头、换句群长度。")
    if score.get("voiceCollapsePenalty", 0.0) >= 0.18:
        issues.append("当前角色语气过于接近统一作者腔，拉开对白与旁白的区别。")
    if score.get("sourceOverlapPenalty", 0.0) >= 0.18:
        issues.append("与参考范例局部重合偏高，不得复用原句。")
    drift = score.get("drift", {}) or {}
    if abs(float(drift.get("sentence_p50_delta", 0.0) or 0.0)) >= 6:
        issues.append("句长中位数偏离目标过多，调回参考节奏。")
    if abs(float(drift.get("dialogue_ratio_delta", 0.0) or 0.0)) >= 0.12:
        issues.append("对白与叙述比例偏移过大，恢复当前 beat 需要的声部比重。")
    if not issues:
        issues.append("内容不变，只做局部风格校正，禁止新增事件。")
    return (
        "当前文本内容和角色状态不变。\n"
        "只修正以下问题：\n"
        + "\n".join(f"{idx}. {issue}" for idx, issue in enumerate(issues, 1))
        + "\n禁止增加新事件。\n禁止复用参考范例中的完整表达。"
    )
