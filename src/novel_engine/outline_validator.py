"""Deterministic outline validation gates."""
from __future__ import annotations

from typing import Any


def validation_errors(contract: dict[str, Any], payload: Any, *, part_seq: int | None = None) -> list[str]:
    text = _stringify(payload)
    errors: list[str] = []
    if not contract or not text:
        return errors

    forbidden_terms = [str(x) for x in (contract.get("forbidden_terms") or []) if str(x).strip()]
    for term in forbidden_terms:
        if term and term in text:
            errors.append(f"出现禁用污染词：{term}")

    gates = contract.get("release_gates") or {}
    if part_seq == 1:
        for term in gates.get("early_forbidden") or []:
            term = str(term).strip()
            if term and term in text:
                errors.append(f"第一阶段提前展开未到期信息：{term}")

    volume = _volume_for(contract, part_seq)
    if volume:
        for term in volume.get("forbidden") or []:
            term = str(term).strip()
            if term and term in text:
                errors.append(f"第{part_seq}卷禁止出现/展开：{term}")
        prominent = _prominent_text(payload)
        for term in volume.get("shadow_only") or []:
            term = str(term).strip()
            if term and prominent and term in prominent:
                errors.append(f"第{part_seq}卷影子信息不能成为标题/目标/反转：{term}")

    au = contract.get("active_unit") or {}
    if part_seq == 1 and au.get("locked"):
        for term in au.get("forbidden_before_completion") or []:
            term = str(term).strip()
            if term and term in text:
                errors.append(f"当前单元未结算前抢戏：{term}")
        active_name = str(au.get("name") or "").strip()
        active_goal = str(au.get("unit_goal") or "").strip()
        later_titles = [
            str(v.get("title") or "").strip()
            for v in (contract.get("volume_blueprint") or [])[1:]
            if isinstance(v, dict)
        ]
        prominent = _prominent_text(payload)
        if prominent:
            for title in later_titles:
                core = title.split("·", 1)[-1].strip() if title else ""
                if core and core not in active_name and core not in active_goal and core in prominent:
                    errors.append(f"当前单元未完成前不能把下一单元做成标题/目标/反转：{core}")
    return errors


def is_valid_outline(contract: dict[str, Any], payload: Any, *, part_seq: int | None = None) -> bool:
    return not validation_errors(contract, payload, part_seq=part_seq)


def _stringify(payload: Any) -> str:
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict):
        return "\n".join(_stringify(v) for v in payload.values())
    if isinstance(payload, (list, tuple, set)):
        return "\n".join(_stringify(x) for x in payload)
    return str(payload)


def _volume_for(contract: dict[str, Any], part_seq: int | None) -> dict[str, Any] | None:
    if part_seq is None:
        return None
    vols = contract.get("volume_blueprint") or []
    if 1 <= part_seq <= len(vols) and isinstance(vols[part_seq - 1], dict):
        return vols[part_seq - 1]
    return None


def _prominent_text(payload: Any) -> str:
    if isinstance(payload, dict):
        fields = ["title", "goal", "summary", "key_twist", "dramatic_question"]
        return "\n".join(_stringify(payload.get(k)) for k in fields if k in payload)
    return ""
