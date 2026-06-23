"""Pre-acceptance narrative fact extraction and contradiction audit.

The writer may propose facts, but only acceptance is allowed to commit them.
Character claims and hypotheses stay separate from objective narration so a lie
cannot silently overwrite canon.
"""
from __future__ import annotations

import json
import re
import uuid
from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import Any

from ..entity_matching import normalize_entity_name
from ..llm.base import LLMClient
from ..models import Fact
from ..repository import Repository


def _subject_relevant(established_subject: str, proposed_norms: set[str]) -> bool:
    """Same-subject match with normalization + containment, so "林晚" aligns with
    "死者林晚" / "真林晚" instead of fragmenting into separate subjects."""
    e = normalize_entity_name(established_subject)
    if not e:
        return False
    return any(e == p or e in p or p in e for p in proposed_norms if p)


_ADDR_TOKEN_RE = re.compile(r"\d+号")


def _scene_tokens(subject: str) -> set[str]:
    """门牌号 tokens of a subject, e.g. "17号与18号共用隔墙" -> {17号,18号}.

    场景归并的桥：同一犯罪现场在不同章可能叫"17号地下室/17号储藏室/17-18共用隔墙"，
    实体名各异但共享门牌号 token，据此把它们拉到一起比对（治"墙从17挪到18"这类漂移）。
    """
    return set(_ADDR_TOKEN_RE.findall(subject or ""))


def _with_scene_tokens(row: dict[str, Any]) -> dict[str, Any]:
    """Expose a fact's 门牌号 tokens to the audit LLM so it can align same-scene facts."""
    tokens = sorted(_scene_tokens(str(row.get("subject", ""))))
    return {**row, "scene_tokens": tokens} if tokens else dict(row)


@dataclass
class FactDeltaResult:
    assertions: list[dict[str, Any]] = field(default_factory=list)
    violations: list[dict[str, Any]] = field(default_factory=list)


def _safe_json(raw: str) -> dict[str, Any] | None:
    text = (raw or "").strip().strip("`")
    if text.lower().startswith("json"):
        text = text[4:].strip()
    start, end = text.find("{"), text.rfind("}")
    try:
        data = json.loads(text[start : end + 1] if 0 <= start < end else text)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _scope(llm: LLMClient, caller: str, chapter_no: int):
    scope = getattr(llm, "scope", None)
    if callable(scope):
        return scope(caller=caller, meta={"chapter_no": chapter_no})
    return nullcontext()


def _extract_assertions(
    llm: LLMClient,
    prose: str,
    chapter_no: int,
) -> list[dict[str, Any]]:
    system = (
        "你是小说事实抽取器，只抽取正文明确写出的状态和变化，不推测。"
        "必须区分：narration=叙述确认的客观事实；claim=角色、文件、媒体的说法；"
        "hypothesis=怀疑、推测、计划。只输出JSON："
        '{"assertions":[{"subject":"规范主体名","predicate":"稳定的英文蛇形字段",'
        '"value":"简短明确的值","fact_class":"narration|claim|hypothesis",'
        '"source_text":"正文中的连续原句，不超过80字","immutable":false}]}。'
        "优先抽取身份、关系、婚史、经历年限、生死、时间、地点、持有物、知识来源、"
        "案件状态、行动结果和人物前史。"
        "对**关键场景**（犯罪现场、藏尸/物证位置、反复出现的重要地点）要额外抽取其"
        "**固定布局事实**：所属门牌号、哪一面墙、裂缝走向、物证位置、房间结构、出入通道——"
        "且 subject 必须带上**具体门牌号或规范地名**（如『锦澜湾17号别墅地下室』，"
        "不要只写『地下室』『南墙』『现场』这种无门牌号的泛称）。"
        "不要把比喻、条件句、否定的假设当事实。"
    )
    user = f"[chapter]{chapter_no}\n[prose]\n{prose[:18000]}\n\n只输出JSON。"
    try:
        with _scope(llm, "fact_delta_extract", chapter_no):
            data = _safe_json(llm.complete_at(system, user, 0.0))
    except Exception:
        return []
    if not data:
        return []
    out: list[dict[str, Any]] = []
    for row in data.get("assertions") or []:
        if not isinstance(row, dict):
            continue
        subject = str(row.get("subject", "")).strip()
        predicate = str(row.get("predicate", "")).strip()
        value = str(row.get("value", "")).strip()
        fact_class = str(row.get("fact_class", "")).strip().lower()
        source_text = str(row.get("source_text", "")).strip()
        if (
            not subject
            or not predicate
            or not value
            or fact_class not in {"narration", "claim", "hypothesis"}
            or not source_text
            or source_text not in prose
        ):
            continue
        out.append(
            {
                "subject": subject,
                "predicate": predicate,
                "value": value,
                "fact_class": fact_class,
                "source_text": source_text[:80],
                "immutable": bool(row.get("immutable", False)),
                "source_chapter": chapter_no,
            }
        )
    return out


def _established_assertions(repo: Repository, chapter_no: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for fact in repo.list_facts():
        if int(fact.story_time or 0) >= chapter_no:
            continue
        structured = fact.structured if isinstance(fact.structured, dict) else {}
        for row in structured.get("assertions") or []:
            if not isinstance(row, dict) or row.get("fact_class") != "narration":
                continue
            out.append(
                {
                    "fact_id": fact.fact_id,
                    "subject": str(row.get("subject", "")),
                    "predicate": str(row.get("predicate", "")),
                    "value": str(row.get("value", "")),
                    "source_text": str(row.get("source_text", fact.canonical_content)),
                    "source_chapter": int(row.get("source_chapter", fact.story_time or 0)),
                    "immutable": bool(row.get("immutable", False)),
                }
            )
    return out[-240:]


def _audit_assertions(
    llm: LLMClient,
    *,
    chapter_no: int,
    established: list[dict[str, Any]],
    proposed: list[dict[str, Any]],
    chapter_contract: dict[str, Any],
) -> list[dict[str, Any]]:
    objective = [row for row in proposed if row.get("fact_class") == "narration"]
    if not objective:
        return []
    system = (
        "你是长篇小说事实一致性审计器。比较既有客观事实、当前章节合同和本章新事实。"
        "只报告高置信硬错误：同一主体同一属性互斥；时间地点身份关系直接矛盾；"
        "没有任何合同或既有事实支持却突然出现的关键前史、能力、亲属、婚史、长期经历、"
        "钥匙、线人、证据来源；或者状态变化缺少必要前置。"
        "注意：若两条事实的主体共享相同门牌号 token（scene_tokens 有交集，如都含「17号」），"
        "很可能在描述**同一处或相邻地点**——请按同一现场比对其位置、布局、墙体归属、"
        "裂缝走向、物证位置是否矛盾，不要因为主体名字不同就放过。"
        "普通新动作、感受、环境细节不是错误。只输出JSON："
        '{"conflicts":[{"type":"canonical_fact_conflict|unsupported_backstory|'
        'impossible_state_transition","source_text":"必须原样复制本章新事实的source_text",'
        '"established_fact":"冲突的旧事实或缺失的前置条件",'
        '"message":"简短说明","repair":"只修哪一句，必须服从旧事实"}]}。'
        "没有硬错误时返回空数组。"
    )
    payload = {
        "established_facts": [_with_scene_tokens(row) for row in established],
        "chapter_contract": {
            "must_happen": chapter_contract.get("must_happen", []),
            "scene_flow": chapter_contract.get("scene_flow", []),
            "required_exit_state": chapter_contract.get("required_exit_state", ""),
            "allowed_cast": chapter_contract.get("allowed_cast", []),
            "allowed_locations": chapter_contract.get("allowed_locations", []),
            "allowed_items": chapter_contract.get("allowed_items", []),
        },
        "proposed_objective_facts": [_with_scene_tokens(row) for row in objective],
    }
    try:
        with _scope(llm, "fact_delta_audit", chapter_no):
            data = _safe_json(
                llm.complete_at(system, json.dumps(payload, ensure_ascii=False), 0.0)
            )
    except Exception:
        return []
    if not data:
        return []
    source_rows = {str(row.get("source_text", "")): row for row in objective}
    violations: list[dict[str, Any]] = []
    allowed_types = {
        "canonical_fact_conflict",
        "unsupported_backstory",
        "impossible_state_transition",
    }
    for row in data.get("conflicts") or []:
        if not isinstance(row, dict):
            continue
        kind = str(row.get("type", "")).strip()
        source_text = str(row.get("source_text", "")).strip()
        if kind not in allowed_types or source_text not in source_rows:
            continue
        established_fact = str(row.get("established_fact", "")).strip()
        message = str(row.get("message", "")).strip()
        repair = str(row.get("repair", "")).strip()
        violations.append(
            {
                "type": kind,
                "text": message or f"新事实“{source_text}”与既有叙事状态冲突。",
                "source_text": source_text,
                "established_fact": established_fact,
                "advice": repair or "只修改冲突原句，使其服从既有事实；不要改动其他段落。",
                "repair_scope": "sentence",
            }
        )
    return violations


def audit_fact_delta(
    repo: Repository,
    chapter,
    prose: str,
    llm: LLMClient | None,
    chapter_contract: dict[str, Any],
) -> FactDeltaResult:
    if llm is None or not (prose or "").strip() or chapter is None:
        return FactDeltaResult()
    chapter_no = int(getattr(chapter, "sequence_order", 0) or 0)
    assertions = _extract_assertions(llm, prose, chapter_no)
    # Only compare against established facts that share a subject with this
    # chapter's objective facts: sharper alignment + a much smaller audit prompt
    # that does not grow unbounded over a long novel.
    narration = [row for row in assertions if row.get("fact_class") == "narration"]
    proposed_norms = {normalize_entity_name(row.get("subject", "")) for row in narration}
    proposed_norms.discard("")
    proposed_tokens: set[str] = set()
    for row in narration:
        proposed_tokens |= _scene_tokens(row.get("subject", ""))
    established = _established_assertions(repo, chapter_no)
    if proposed_norms or proposed_tokens:
        # 纳入：同主体(归一化/包含) 或 共享门牌号 token(同一/相邻现场)。
        established = [
            row for row in established
            if _subject_relevant(row.get("subject", ""), proposed_norms)
            or (_scene_tokens(row.get("subject", "")) & proposed_tokens)
        ]
    violations = _audit_assertions(
        llm,
        chapter_no=chapter_no,
        established=established,
        proposed=assertions,
        chapter_contract=chapter_contract,
    )
    return FactDeltaResult(assertions=assertions, violations=violations)


def commit_fact_delta(
    repo: Repository,
    assertions: list[dict[str, Any]],
    *,
    chapter_no: int,
    source_event_id: str | None = None,
) -> None:
    """Commit already-audited assertions. This function never creates entities."""
    for row in assertions or []:
        if not isinstance(row, dict):
            continue
        subject = str(row.get("subject", "")).strip()
        predicate = str(row.get("predicate", "")).strip()
        value = str(row.get("value", "")).strip()
        source_text = str(row.get("source_text", "")).strip()
        if not subject or not predicate or not value or not source_text:
            continue
        repo.append_fact(
            Fact(
                fact_id=f"fd_{uuid.uuid4().hex[:12]}",
                fact_type="narrative_assertion",
                canonical_content=f"{subject}.{predicate}={value}",
                structured={
                    "assertions": [{**row, "source_chapter": chapter_no}]
                },
                story_time=chapter_no,
                involved_entities=[],
                source_event_id=source_event_id,
            )
        )
