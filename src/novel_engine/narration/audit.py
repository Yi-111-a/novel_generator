"""Chapter-level audit gate."""
from __future__ import annotations

import json
import re
from contextlib import nullcontext
from dataclasses import dataclass, field

from ..chapter_scope_validator import compile_chapter_package, is_generic_object_name, validate_chapter_scope
from ..continuity import run_continuity_checks
from ..chapter_titles import repair_chapter_title, validate_chapter_title
from ..entity_matching import is_garbled_name, longest_name_matches, normalize_entity_name
from ..llm.base import LLMClient
from ..models import ChapterPlan, Scene
from ..naming import validate_character_name
from ..repository import Repository
from .quality_checks import detect_repeated_opening, run_quality_checks
from .fact_delta import audit_fact_delta
from .story_clock import audit_time_regression

_SERMON_CONNECTIVES = [
    "说到底",
    "这就是",
    "这便是",
    "或许",
    "也许",
    "才是真正",
    "原来",
    "不过是",
    "终究",
    "归根结底",
    "其实",
    "所谓的",
]
_SERMON_ABSTRACT = [
    "命运",
    "人生",
    "意义",
    "真相",
    "救赎",
    "宿命",
    "人性",
    "世界",
    "时间",
    "选择",
    "代价",
    "信念",
]


@dataclass
class AuditCheck:
    ok: bool
    feedback: str = ""


@dataclass
class AuditResult:
    ok: bool
    severity: str = "pass"
    checks: dict[str, AuditCheck] = field(default_factory=dict)
    rewrite_advice: str = ""
    # Structured structural signals, graded individually by the combined audit.
    phantom_items: list[str] = field(default_factory=list)
    quality_issues: list[tuple[str, str]] = field(default_factory=list)
    sermon: bool = False
    name_issues: list[str] = field(default_factory=list)
    title_issues: list[str] = field(default_factory=list)

    def feedback_text(self) -> str:
        parts = [check.feedback for check in self.checks.values() if check.feedback]
        if self.rewrite_advice:
            parts.append(self.rewrite_advice)
        return "；".join(parts)

@dataclass
class CombinedAuditResult:
    decision: str
    classification: str = "prose_rewriteable"
    title: str = ""
    scores: dict[str, float] = field(default_factory=dict)
    violations: list[dict[str, str]] = field(default_factory=list)
    new_entity_proposals: list[dict] = field(default_factory=list)
    fact_updates: list[dict] = field(default_factory=list)
    character_updates: list[dict] = field(default_factory=list)
    item_transfers: list[dict] = field(default_factory=list)
    graph_updates: list[dict] = field(default_factory=list)
    rewrite_targets: list[str] = field(default_factory=list)
    summary: dict[str, object] = field(default_factory=dict)


def _sermon_tail(prose: str) -> bool:
    tail = (prose or "").strip()
    if not tail:
        return False
    parts = [p for p in re.split(r"\n\s*\n|\n", tail) if p.strip()]
    last = (parts[-1] if parts else tail)[-120:]
    return any(c in last for c in _SERMON_CONNECTIVES) and any(w in last for w in _SERMON_ABSTRACT)


def _advanced(repo: Repository, ch: ChapterPlan) -> bool:
    try:
        for event in repo.events_for_beat(ch.chapter_id):
            if (event.payload or {}).get("chosen_value"):
                return True
        for item in repo.list_inventory():
            if item.acquired_chapter == ch.sequence_order and item.status in ("held", "transferred"):
                return True
        if ch.reveal_gate:
            for node in repo.list_reveal_nodes():
                if node.discovered and node.fact_id in ch.reveal_gate and node.discovered_chapter == ch.sequence_order:
                    return True
    except Exception:
        return True
    return False


def _phantom_items(repo: Repository, ch: ChapterPlan, prose: str) -> list[str]:
    allowed = set(ch.items_present or []) | set(ch.available_items or []) | set(ch.items_introduced or [])
    # POV-carried tools and fixed scene objects are usable even when an old
    # chapter plan forgot to copy them into items_present.
    if ch.pov_agent:
        allowed.update(item.object_id for item in repo.items_held_by(ch.pov_agent))
    for location_id in ch.location_ids or []:
        location = repo.get_location(location_id)
        if location:
            allowed.update(location.notable_items or [])
    objects = [
        entity
        for entity in repo.list_entities()
        if entity.type == "object"
        and entity.name
        and len(entity.name) >= 2
        and not entity.name.isascii()
        and not (entity.attributes or {}).get("merged_into")
        and not is_generic_object_name(entity.name)
    ]
    by_id = {entity.entity_id: entity for entity in objects}
    return [
        by_id[match.entity_id].name
        for match in longest_name_matches(
            prose,
            ((entity.entity_id, entity.type, entity.name) for entity in objects),
        )
        if match.entity_id not in allowed
    ]


def _safe_json_loads(raw: str) -> dict:
    text = (raw or "").strip().strip("`")
    if text.lower().startswith("json"):
        text = text[4:].strip()
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start : end + 1] if 0 <= start < end else text)


def _llm_audit(
    repo: Repository,
    ch: ChapterPlan,
    prose: str,
    prev_ch: ChapterPlan | None,
    llm: LLMClient | None,
    deterministic_notes: list[str],
) -> dict[str, AuditCheck]:
    base = {
        "hook_continuity": AuditCheck(True, ""),
        "scene_transition": AuditCheck(True, ""),
        "cast_item_compliance": AuditCheck(True, ""),
        "forward_progress": AuditCheck(True, ""),
        "anti_grandstanding": AuditCheck(True, ""),
    }
    if llm is None:
        return base

    entities = {e.entity_id: e.name for e in repo.list_entities()}
    cast_names = "、".join(entities.get(a, a) for a in (ch.cast or [])) or "（未指定）"
    item_names = "、".join(entities.get(o, o) for o in (ch.items_present or [])) or "（无）"
    prev_tail = (prev_ch.ending_hook or "").strip() if prev_ch else "（无/首章）"
    beats = "；".join((ch.beat_goals or [])[:4]) or "（无）"
    advanced = _advanced(repo, ch)
    system = (
        "你是严格的小说章节审计器。只判断硬伤，不评价文笔。"
        "请检查：衔接、转场、在场人物/道具合规、推进、反升华。"
        '只输出 JSON：{"ok":true/false,"feedback":"..."}'
    )
    user = (
        f"【上一章结尾钩子】{prev_tail}\n"
        f"【本章戏剧问题】{ch.dramatic_question or '（无）'}\n"
        f"【本章推进目标】{ch.exit_state or '（无）'}\n"
        f"【本章节拍】{beats}\n"
        f"【在场人物】{cast_names}\n"
        f"【可用道具】{item_names}\n"
        f"【事实层推进】{'有' if advanced else '未检测到明确推进'}\n"
        f"【确定性预警】{'；'.join(deterministic_notes) if deterministic_notes else '无'}\n"
        f"【正文】\n{prose[:6000]}"
    )
    try:
        data = _safe_json_loads(llm.complete(system, user))
    except Exception:
        return base
    if "ok" not in data:
        return base
    ok = bool(data.get("ok"))
    feedback = str(data.get("feedback", "")).strip()
    if ok:
        return base
    return {
        "hook_continuity": AuditCheck(False, feedback or "章节衔接存在问题"),
        "scene_transition": AuditCheck(True, ""),
        "cast_item_compliance": AuditCheck(True, ""),
        "forward_progress": AuditCheck(True, ""),
        "anti_grandstanding": AuditCheck(True, ""),
    }


def audit_chapter_result(
    repo: Repository,
    ch: ChapterPlan,
    scenes: list[Scene],
    prev_ch: ChapterPlan | None,
    llm: LLMClient | None,
) -> AuditResult:
    prose = "\n\n".join(scene.prose_text for scene in scenes if scene.prose_text)
    if not prose.strip():
        return AuditResult(ok=True, severity="pass", checks={})

    checks: dict[str, AuditCheck] = {}
    deterministic_notes: list[str] = []

    phantom = _phantom_items(repo, ch, prose)
    quality = run_quality_checks(prose, ch.title)
    existing_titles = [c.title for c in repo.list_chapter_plans() if c.chapter_id != ch.chapter_id and c.title]
    title_validation = validate_chapter_title(ch.title, existing_titles) if ch.title.strip() else None

    name_issues: list[str] = []
    characters = [e for e in repo.list_entities() if e.type == "character"]
    authorized_ids = set(ch.cast or []) | set(ch.allowed_entity_ids or [])
    mentioned_ids = {
        match.entity_id
        for match in longest_name_matches(
            prose,
            ((e.entity_id, e.type, e.name) for e in characters if e.name),
        )
    }
    relevant_ids = authorized_ids | mentioned_ids
    active_records = {
        record.agent_id: record
        for record in getattr(repo, "list_character_names", lambda: [])()
        if record.status == "active" and not record.replaced_by_agent_id
    }
    relevant_names: list[tuple[str, str]] = []
    for entity in characters:
        if entity.entity_id not in relevant_ids:
            continue
        record = active_records.get(entity.entity_id)
        primary = (record.primary_name if record else entity.name) or ""
        if primary and not is_garbled_name(primary):
            relevant_names.append((entity.entity_id, primary))
        elif primary:
            name_issues.append(f"{primary}：名称疑似乱码或损坏别名")

    seen_names: set[str] = set()
    for entity_id, primary_name in relevant_names:
        normalized = normalize_entity_name(primary_name)
        if normalized in seen_names:
            name_issues.append(f"{primary_name}：当前章节相关实体存在重复正式姓名")
            continue
        seen_names.add(normalized)
        result = validate_character_name(
            primary_name,
            [name for other_id, name in relevant_names if other_id != entity_id],
        )
        if not result.ok:
            name_issues.append(f"{primary_name}：{'；'.join(result.reasons)}")

    # Secondary labels use secondary-label rules: role-like or nickname-like
    # wording is legal, but corruption and collisions with another formal name
    # are still data problems.
    formal_by_normalized = {
        normalize_entity_name(name): entity_id for entity_id, name in relevant_names
    }
    for entity_id in relevant_ids:
        record = active_records.get(entity_id)
        if record is None:
            continue
        for label in (
            record.short_name,
            record.nickname,
            record.honorific,
            record.public_alias,
            record.self_ref,
            record.enemy_label,
        ):
            normalized = normalize_entity_name(label)
            if not normalized:
                continue
            if is_garbled_name(normalized):
                name_issues.append(f"{label}：附属称呼疑似乱码")
            owner = formal_by_normalized.get(normalized)
            if owner and owner != entity_id:
                name_issues.append(f"{label}：附属称呼撞上另一角色正式姓名")

    if phantom:
        deterministic_notes.append(f"出现越界道具：{'、'.join(phantom)}")
    if not quality.ok:
        deterministic_notes.extend(issue.message for issue in quality.issues)
    if title_validation is not None and not title_validation.ok:
        deterministic_notes.extend(title_validation.reasons)
    if _sermon_tail(prose):
        deterministic_notes.append("结尾疑似强行升华或点题")

    llm_checks = _llm_audit(repo, ch, prose, prev_ch, llm, deterministic_notes)
    checks.update(llm_checks)
    checks["title_quality"] = AuditCheck(
        ok=True if title_validation is None else title_validation.ok,
        feedback="" if title_validation is None else "；".join(title_validation.reasons),
    )
    checks["name_quality"] = AuditCheck(
        ok=not name_issues,
        feedback="；".join(name_issues[:5]),
    )
    checks["bug_signals"] = AuditCheck(
        ok=quality.ok and not phantom,
        feedback="；".join(deterministic_notes),
    )

    ok = all(check.ok for check in checks.values())
    severity = "pass" if ok else "blocker"
    rewrite_advice = ""
    if not ok:
        advice_parts: list[str] = []
        if phantom:
            advice_parts.append("删除越界道具，或补上清晰来源。")
        if not quality.ok:
            advice_parts.append("清理系统提示词残留、重复段落和标题混入正文的问题。")
        if title_validation is not None and not title_validation.ok:
            advice_parts.append("重命名章节标题，避免重复、近似重复和模板标题。")
        if name_issues:
            advice_parts.append("修正异常人物命名，避免坏名字和同词根撞名。")
        rewrite_advice = " ".join(advice_parts)
    title_issues = list(title_validation.reasons) if (title_validation is not None and not title_validation.ok) else []
    return AuditResult(
        ok=ok,
        severity=severity,
        checks=checks,
        rewrite_advice=rewrite_advice,
        phantom_items=list(phantom),
        quality_issues=[(issue.code, issue.message) for issue in quality.issues],
        sermon=_sermon_tail(prose),
        name_issues=list(name_issues),
        title_issues=title_issues,
    )


def audit_chapter(
    repo: Repository,
    ch: ChapterPlan,
    scenes: list[Scene],
    prev_ch: ChapterPlan | None,
    llm: LLMClient | None,
) -> tuple[bool, str]:
    result = audit_chapter_result(repo, ch, scenes, prev_ch, llm)
    return result.ok, result.feedback_text()


# Risk grading. Only P0 (precise, high-confidence canon/spoiler breaches) blocks a
# draft. P1 are heuristic/canon-style signals (fuzzy n-gram leak overlap, name/address
# drift, investigation-topic words, invented dates, plan-data inconsistencies) — they
# are recorded as advisories but must not hard-block otherwise clean prose.
_P0_VIOLATION_TYPES = {
    "unauthorized_character",
    "unauthorized_location",
    "unauthorized_faction",
    "unauthorized_item",
    "premature_reveal",
    "unauthorized_truth_reveal",
    "unforeshadowed_introduction",
    "text_encoding_corruption",
    "structural_defect",
    "planning_conflict",
    "canonical_fact_conflict",
    "impossible_state_transition",
    "story_clock_regression",
    # NB: the following are deliberately P1 (advisory), not P0:
    #  - unsupported_backstory: most judgment-heavy fact-delta type; the
    #    established-fact set is partial, so blocking risks deleting legitimately
    #    introduced new backstory.
    #  - repeated_opening: heuristic text-overlap; as P0 it would auto-delete an
    #    opening (destructive on a false positive / stylistic callback) and could
    #    halt a batch. P1 surfaces it with advice without those risks.
}

# quality_checks-native codes are prose-fixable machine defects; everything else
# in the quality issue stream comes from text-integrity scanning = corruption.
_QUALITY_DEFECT_CODES = {"bug_signal", "repeated_paragraph", "title_leak"}

# Per-violation guidance fed to the Reviser (and recorded as rewrite targets).
# Keys are violation types; the offending `text` travels alongside in the
# violation dict so the reviser can locate the passage.
_VIOLATION_ADVICE = {
    "structural_defect": "删掉这段机械残留、重复段落或越界道具，保持上下文通顺，不要新增任何人物/道具/地点。",
    "repeated_opening": "删除与上一章开头重复的复述段落，本章直接从新场景切入，不要再复述上一章已写过的情节。",
    "text_encoding_corruption": "修复乱码或编码损坏的文字。",
    "sermon_tail": "把结尾的升华或点题改成具体的动作、对话或画面收尾。",
    "unauthorized_character": "删除未授权人物及其身份、关系和行动，不用代称保留。",
    "unauthorized_location": "删除未授权地点及其门牌、方位和内部结构。",
    "unauthorized_item": "删除未授权道具；若只是日常环境物，不把它写成剧情线索。",
    "unauthorized_faction": "删除未授权组织名称及其目标、成员和关系。",
    "unauthorized_truth_reveal": "撤回未授权真相，只保留当前章允许观察到的表面异常。",
    "premature_reveal": "把提前登场或揭秘改成不点名、不解释的间接征兆。",
    "unforeshadowed_introduction": "不要让新实体凭空正式登场；本章只保留先行征兆。",
    "future_event_leak": "删除属于未来章节的事件、结论和专有细节，不做同义改写。",
    "invented_exact_date": "删除所有未在当前章节包中明确给出的具体年月日。",
    "invented_exact_address": "删除所有未在当前章节包中明确给出的精确门牌。",
    "new_investigation_result": "删除本章未授权的新调查结论，只写记录之间存在冲突。",
    "canonical_name_drift": "只使用当前章节包授权的规范称谓。",
    "canonical_address_drift": "只使用当前章节包授权的规范地点表达。",
    "chapter_audit": "按结构审计提示修标题或硬伤，不要复制审计文本中的专有名词或数值。",
    "story_clock_regression": "把本章的钟点/时段改到不早于上一章末（时间只能往后走）；若确为次日请显式写出『次日/第二天』。",
}


def _violation_severity(violation_type: str) -> str:
    return "P0" if violation_type in _P0_VIOLATION_TYPES else "P1"


def _advice_for(violation_type: str) -> str:
    return _VIOLATION_ADVICE.get(violation_type, "")


def _previous_chapter_prose(repo: Repository, ch: ChapterPlan) -> str:
    """Prose of the immediately preceding accepted chapter, for recap detection."""
    target = int(ch.sequence_order or 0) - 1
    if target < 1:
        return ""
    try:
        for record in repo.list_accepted_chapters():
            if int(getattr(record, "chapter_no", 0) or 0) == target:
                return getattr(record, "prose", "") or ""
    except Exception:
        return ""
    return ""


def run_combined_chapter_audit(
    repo: Repository,
    ch: ChapterPlan,
    prose: str,
    prev_ch: ChapterPlan | None,
    llm: LLMClient | None,
) -> CombinedAuditResult:
    package = compile_chapter_package(repo, ch)
    package_diagnostics = package.get("diagnostics") or {}
    planning_conflicts = list(package_diagnostics.get("planning_conflicts") or [])
    data_conflicts = list(package_diagnostics.get("data_conflicts") or [])
    scene = Scene(scene_id="audit_preview", discourse_order=1, prose_text=prose)
    structural = audit_chapter_result(repo, ch, [scene], prev_ch, llm=None)
    scope = validate_chapter_scope(repo, ch, prose, llm=None)

    violations: list[dict[str, str]] = []
    rewrite_targets: list[str] = []
    # Grade structural signals individually so the final decision is a faithful
    # single standard. Machine defects (越界道具/控制信息残留/重复段落/标题混入正文)
    # are P0 and prose-fixable; encoding corruption is P0 but regeneration-only;
    # 结尾升华/命名/标题模板 are P1 advisories that must not block a clean draft.
    for name in structural.phantom_items:
        violations.append({"type": "structural_defect", "text": f"越界道具「{name}」，需删除或补清晰来源"})
    for code, message in structural.quality_issues:
        vtype = "structural_defect" if code in _QUALITY_DEFECT_CODES else "text_encoding_corruption"
        violations.append({"type": vtype, "text": message})
    if structural.sermon:
        violations.append({"type": "sermon_tail", "text": "结尾疑似强行升华或点题"})
    for issue in structural.name_issues:
        violations.append({"type": "data_conflict", "text": issue})
    for reason in structural.title_issues:
        violations.append({"type": "chapter_audit", "text": reason})
    if scope["severity"] == "blocker":
        violations.extend(
            {
                "type": str(item.get("type", "")),
                "text": str(item.get("text", "")),
            }
            for item in scope["violations"]
        )
        if scope.get("rewriteAdvice"):
            rewrite_targets.append(str(scope["rewriteAdvice"]))

    if planning_conflicts:
        violations.extend(
            {"type": "planning_conflict", "text": str(item.get("message", ""))}
            for item in planning_conflicts
        )
    if data_conflicts:
        violations.extend(
            {"type": "data_conflict", "text": str(item.get("message", ""))}
            for item in data_conflicts
        )

    # Cross-chapter continuity advisories (Phase 6): dropped foreshadows, etc.
    # Incremental reconciliation against the narrative-state ledger; always P1.
    violations.extend(
        finding.as_violation()
        for finding in run_continuity_checks(repo, ch.sequence_order)
    )

    # Cross-chapter repeated opening: this chapter re-narrates the previous
    # chapter's opening near-verbatim (a "previously on" recap). Deterministic,
    # zero-token; P0 + prose-rewriteable so the reviser trims the recap.
    previous_prose = _previous_chapter_prose(repo, ch)
    if previous_prose:
        overlap = detect_repeated_opening(prose, previous_prose)
        if overlap:
            violations.append({"type": "repeated_opening", "text": f"与上一章开头重复：「{overlap}」"})

    # Dry-run narrative facts before acceptance. The assertions are persisted
    # only after the draft passes audit and is accepted.
    fact_delta = audit_fact_delta(repo, ch, prose, llm, package)
    violations.extend(fact_delta.violations)

    # 故事时钟·确定性兜底闸：章末时间不得早于上一章（零成本，补 fact_delta 语义拦截的网）。
    time_regression = audit_time_regression(repo, ch, prose)
    if time_regression:
        violations.append(time_regression)

    for violation in violations:
        vtype = str(violation.get("type", ""))
        violation["severity"] = _violation_severity(vtype)
        if not violation.get("advice"):
            advice = _advice_for(vtype)
            if advice:
                violation["advice"] = advice
    blocking = [v for v in violations if v.get("severity") == "P0"]
    # Rewrite targets feed the reviser: prefer the per-violation advice of the
    # blocking set, falling back to any structural advice already collected.
    rewrite_targets.extend(
        str(v.get("advice", "")).strip() for v in blocking if v.get("advice")
    )

    # Classification drives rewrite-vs-block, so it must reflect only the blocking
    # (P0) set. Otherwise a P1 advisory (e.g. a data_conflict name overlap) would
    # mislabel an otherwise prose-fixable P0 (premature_reveal) as unfixable and
    # suppress the auto-rewrite. data_conflicts are P1 advisories and excluded.
    classification = _classify_audit_issues(
        blocking,
        planning_conflicts=planning_conflicts,
        data_conflicts=None,
    )
    recent_titles = [item.title for item in repo.list_chapter_plans() if item.chapter_id != ch.chapter_id and item.title]
    suggested_title = repair_chapter_title(ch.title or "", ch.sequence_order, recent_titles)
    if llm is not None and prose.strip() and classification == "prose_rewriteable":
        suggested_title = _combined_audit_title(llm, prose, recent_titles, ch.sequence_order) or suggested_title

    # Only P0 breaches block. P1 advisories travel in `violations` for reporting
    # but never flip an otherwise-clean draft away from accept.
    decision = "accept" if not blocking else (
        "rewrite" if classification == "prose_rewriteable" else "blocked"
    )
    return CombinedAuditResult(
        decision=decision,
        classification=classification,
        title=suggested_title,
        scores={
            "structure_ok": 1.0 if structural.ok else 0.0,
            "scope_ok": 1.0 if scope["ok"] else 0.0,
        },
        violations=violations,
        rewrite_targets=_dedupe(rewrite_targets),
        summary={
            "audit": {
                "ok": structural.ok,
                "severity": structural.severity,
                "checks": {k: {"ok": v.ok, "feedback": v.feedback} for k, v in structural.checks.items()},
                "rewriteAdvice": structural.rewrite_advice,
            },
            "scopeAudit": scope,
            "diagnostics": {
                "classification": classification,
                "planning_conflicts": planning_conflicts,
                "data_conflicts": data_conflicts,
            },
            "factDelta": {
                "assertions": fact_delta.assertions,
                "violations": fact_delta.violations,
            },
        },
    )


def _classify_audit_issues(
    violations: list[dict[str, str]],
    *,
    planning_conflicts: list[dict] | None = None,
    data_conflicts: list[dict] | None = None,
) -> str:
    if planning_conflicts or any(row.get("type") == "planning_conflict" for row in violations):
        return "planning_conflict"
    if data_conflicts or any(row.get("type") == "data_conflict" for row in violations):
        return "data_conflict"
    validator_types = {
        "unforeshadowed_introduction",
        "text_encoding_corruption",
    }
    if any(row.get("type") in validator_types for row in violations):
        return "validator_conflict"
    return "prose_rewriteable"


def _combined_audit_title(
    llm: LLMClient,
    prose: str,
    recent_titles: list[str],
    chapter_no: int,
) -> str:
    avoid = " / ".join(title for title in recent_titles if title) or "(none)"
    try:
        scope = getattr(llm, "scope", None)
        context = scope(caller="chapter_title", meta={"chapter_no": chapter_no}) if callable(scope) else nullcontext()
        with context:
            raw = llm.complete(
                "Generate a short Chinese chapter title. Return JSON only as {\"title\":\"...\"}.",
                f"[avoid]\n{avoid}\n\n[prose]\n{prose[:700]}",
            ).strip().strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
        start, end = raw.find("{"), raw.rfind("}")
        data = json.loads(raw[start:end + 1] if 0 <= start < end else raw)
        title = str((data or {}).get("title", "")).strip()
        if validate_chapter_title(title, recent_titles).ok:
            return title
    except Exception:
        pass
    return repair_chapter_title("", chapter_no, recent_titles)


def _dedupe(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = str(value or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out
