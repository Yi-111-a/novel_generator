from __future__ import annotations

import dataclasses
import re
import uuid
from contextlib import nullcontext
from datetime import datetime, timezone

from ..chapter_scope_validator import compile_chapter_package
from ..continuation import ensure_continuation_chapter_plan
from ..continuation.chapter_numbering import next_chapter_no
from ..disclosure import auto_schedule_disclosures
from ..llm.base import LLMClient
from ..models import ChapterDraftRecord, StyleNegativeSample
from ..narration.audit import run_combined_chapter_audit
from ..narration.reviser import Reviser
from ..narration.text_integrity import contains_cjk, ensure_text_integrity
from ..repository import Repository
from .chapter_indexer import ChapterIndexer
from .chapter_writer import ChapterWriter


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DraftManager:
    MAX_AUTOMATIC_AUDIT_REWRITES = 3

    def __init__(self, repo: Repository, llm: LLMClient | None = None, project_id: str = "") -> None:
        self.repo = repo
        self.llm = llm
        self.project_id = project_id
        self.writer = ChapterWriter(repo, llm)
        self.reviser = Reviser(repo, llm)
        self.indexer = ChapterIndexer(repo, llm)

    def _llm_scope(self, caller: str, **meta):
        scope = getattr(self.llm, "scope", None)
        if callable(scope):
            return scope(caller=caller, meta={"project_id": self.project_id, **meta})
        return nullcontext()

    @staticmethod
    def _effective_word_limits(settings, requested_target: int) -> tuple[int, int, int]:
        target = int(requested_target or settings.target_words or 0)
        minimum = int(settings.min_words or 0)
        maximum = int(settings.max_words or 0)
        if target <= 0:
            target = max(1, minimum or 1)
        # API callers historically override only targetWords. Keep that
        # compatible by deriving a proportional band when the stored band
        # cannot contain the requested target.
        if minimum <= 0 or maximum <= 0 or not (minimum <= target <= maximum):
            minimum = max(1, int(target * 0.85))
            maximum = max(minimum, int(target * 1.20))
        return target, minimum, maximum

    def _revise_to_word_limits(
        self,
        *,
        content: str,
        chapter_plan,
        target: int,
        minimum: int,
        maximum: int,
        phase: str,
        attempt: int,
    ) -> tuple[str, dict]:
        before = self.writer.count_words(content)
        if minimum <= before <= maximum:
            return content, {
                "phase": phase,
                "attempt": attempt,
                "action": "none",
                "before": before,
                "after": before,
                "targetWords": target,
                "minWords": minimum,
                "maxWords": maximum,
                "ok": True,
            }
        mode = "expand" if before < minimum else "compress"
        caller = "word_count_expand" if mode == "expand" else "word_count_compress"
        with self._llm_scope(
            caller,
            chapter_no=chapter_plan.sequence_order,
            phase=phase,
            attempt=attempt,
        ):
            revised = self.writer.revise_word_count(
                prose=content,
                chapter_plan=chapter_plan,
                target_words=target,
                min_words=minimum,
                max_words=maximum,
                mode=mode,
            )
        after = self.writer.count_words(revised)
        return revised, {
            "phase": phase,
            "attempt": attempt,
            "action": mode,
            "before": before,
            "after": after,
            "targetWords": target,
            "minWords": minimum,
            "maxWords": maximum,
            "ok": minimum <= after <= maximum,
        }

    @staticmethod
    def _safe_rewrite_targets(violations: list[dict]) -> list[str]:
        """Translate violations to instructions without echoing leaked values."""
        instructions = {
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
            "chapter_audit": "修复结构审计指出的硬伤，但不要复制审计文本中的专有名词或数值。",
        }
        out: list[str] = []
        for row in violations or []:
            instruction = instructions.get(str(row.get("type") or ""))
            if instruction and instruction not in out:
                out.append(instruction)
        return out or ["严格按当前章节包重写，不新增任何专有名词、数值、调查结论或未来事件。"]

    @staticmethod
    def _severity_counts(combined) -> tuple[int, int]:
        """(P0, P1) 计数，用于改写回路的「留最优版 + 防回归」判断。"""
        violations = getattr(combined, "violations", None) or []
        p0 = sum(1 for v in violations if v.get("severity") == "P0")
        p1 = sum(1 for v in violations if v.get("severity") == "P1")
        return p0, p1

    @staticmethod
    def _shares_substring(a: str, b: str, n: int) -> bool:
        """a、b 是否有长度≥n 的公共连续子串（确定性，鲁棒于改写）。"""
        a, b = str(a or ""), str(b or "")
        if len(a) < n or len(b) < n:
            return False
        return any(a[i: i + n] in b for i in range(len(a) - n + 1))

    def _redact_plan_for_rewrite(self, plan, violations):
        """审计判定越界后，**真正改 plan** 再重写——而非只在 guidance 追加文字。

        旧回路重写时 chapter_plan 一字不改，导致 must_happen 仍在逼模型写出被误塞的
        未来材料，3 次重写全 blocked。这里据本章契约的 forbidden（未来章独有事件，逐条比对）
        + 审计点名的未授权实体，从 must_happen/scene_flow/beat_goals 摘掉「与某未来 forbidden
        marker 有≥6字公共子串」或「点名未授权实体」的 beat，并从道具/白名单摘掉未授权实体。
        """
        pkg = compile_chapter_package(self.repo, plan)
        forbidden_markers = [m for m in (pkg.get("forbidden") or []) if str(m).strip()]
        bad_names = {
            str(v.get("text", "")).strip()
            for v in (violations or [])
            if str(v.get("type", "")) in {
                "unauthorized_character", "unauthorized_location", "unauthorized_item",
                "unauthorized_faction", "premature_reveal",
            }
            and str(v.get("text", "")).strip()
        }

        def _beat_ok(beat: str) -> bool:
            beat = str(beat or "")
            if not beat.strip():
                return False
            # 与某条未来 forbidden marker 有显著公共子串 → 本章误含的未来材料
            if any(self._shares_substring(beat, fm, 6) for fm in forbidden_markers):
                return False
            # 点名未授权实体（名字核心≥3 字命中，容忍「锦澜湾别墅区」↔「锦澜湾18号」这类变体）
            if any(self._shares_substring(beat, n, 3) for n in bad_names):
                return False
            return True

        new_must = [b for b in (plan.must_happen or []) if _beat_ok(b)]
        new_goals = [b for b in (plan.beat_goals or []) if _beat_ok(b)]
        new_flow = [b for b in (plan.scene_flow or []) if _beat_ok(b)]
        if not new_must and not new_goals:
            # 整章 beat 都被判越界 → 这是规划层失败，prose 重写救不了；保持原样交 guidance。
            return plan
        name_of = {e.entity_id: e.name for e in self.repo.list_entities()}

        def _item_ok(eid: str) -> bool:
            name = name_of.get(eid, "")
            return not any(self._shares_substring(name, n, 3) for n in bad_names)

        return dataclasses.replace(
            plan,
            must_happen=new_must or list(plan.must_happen or []),
            beat_goals=new_goals or list(plan.beat_goals or []),
            scene_flow=new_flow,
            items_introduced=[i for i in (plan.items_introduced or []) if _item_ok(i)],
            items_present=[i for i in (plan.items_present or []) if _item_ok(i)],
            available_items=[i for i in (plan.available_items or []) if _item_ok(i)],
            allowed_entity_ids=[i for i in (plan.allowed_entity_ids or []) if _item_ok(i)],
        )

    def generate(
        self,
        *,
        guidance: str = "",
        target_words: int = 0,
        outline_only: bool = False,
        mode: str = "manual",
    ) -> ChapterDraftRecord:
        # Existing projects may predate P4. Backfill schedules immediately
        # before writing so their old plans receive the same disclosure gates
        # as newly planned projects.
        auto_schedule_disclosures(self.repo)
        settings = self.repo.get_writing_settings()
        effective_target, min_words, max_words = self._effective_word_limits(
            settings, target_words
        )
        if not outline_only and settings.require_human_acceptance:
            pending = self.repo.list_chapter_drafts(status="pending_acceptance")
            if pending:
                return pending[-1]

        chapter_no = next_chapter_no(self.repo)
        chapter_plan = next(
            (item for item in self.repo.list_chapter_plans() if item.sequence_order == chapter_no),
            None,
        )
        if chapter_plan is None:
            chapter_plan = ensure_continuation_chapter_plan(
                self.repo,
                target_words=effective_target,
                guidance=guidance,
            )
        if chapter_plan is None:
            chapter_plan = self.writer.make_fallback_plan(
                target_words=effective_target
            )

        package = compile_chapter_package(self.repo, chapter_plan)
        package_diagnostics = package.get("diagnostics") or {}
        # Only a planning_conflict (P0: plan requires an entity the package never
        # authorized) can pre-flight-block. data_conflicts are P1 advisories — a
        # dangling/duplicate id reference must not stop prose from being written;
        # it surfaces post-generation in the combined audit instead.
        preflight_classification = ""
        if package_diagnostics.get("planning_conflicts"):
            preflight_classification = "planning_conflict"
        if preflight_classification and not outline_only:
            snapshot = {
                "chapter_package": package,
                "diagnostics": {
                    "classification": preflight_classification,
                    **package_diagnostics,
                },
                "combinedAudit": {
                    "decision": "blocked",
                    "classification": preflight_classification,
                    "title": chapter_plan.title,
                    "scores": {},
                    "violations": [
                        {
                            "type": preflight_classification,
                            "text": str(item.get("message", "")),
                        }
                        for item in (
                            package_diagnostics.get("planning_conflicts")
                            if preflight_classification == "planning_conflict"
                            else package_diagnostics.get("data_conflicts")
                        )
                    ],
                    "rewriteTargets": [],
                },
                "automaticAuditRewriteCount": 0,
                "automaticAuditRewriteLimit": self.MAX_AUTOMATIC_AUDIT_REWRITES,
                "manualRewriteConfirmationRequired": True,
                "pipelineAudit": {
                    "status": "blocked",
                    "wordCount": {},
                    "permission": {
                        "decision": "blocked",
                        "classification": preflight_classification,
                    },
                },
            }
            draft = ChapterDraftRecord(
                project_id=self.project_id,
                chapter_no=chapter_no,
                title=chapter_plan.title or f"第{chapter_no}章",
                outline=self._outline_from_plan(chapter_plan, guidance),
                prose="",
                guidance=guidance,
                target_words=effective_target,
                mode=mode,
                status="blocked",
                context_snapshot_json=snapshot,
                created_at=_now(),
            )
            draft.id = self.repo.create_chapter_draft(draft)
            return draft

        word_count_history: list[dict] = []
        with self._llm_scope(
            "chapter_writer",
            chapter_no=chapter_no,
            phase="initial",
            attempt=1,
        ):
            title, content, snapshot = self.writer.write_next_chapter(
                chapter_plan=chapter_plan,
                guidance=guidance,
                target_words=effective_target,
                min_words=min_words,
                max_words=max_words,
                outline_only=outline_only,
            )
        expected_cjk = contains_cjk(guidance + "\n" + title + "\n" + content)
        if title.strip():
            ensure_text_integrity(title, label="draft_title", expected_cjk=expected_cjk)
        if content.strip():
            ensure_text_integrity(content, label="draft_content", expected_cjk=expected_cjk)

        if not outline_only and content.strip():
            content, word_revision = self._revise_to_word_limits(
                content=content,
                chapter_plan=chapter_plan,
                target=effective_target,
                minimum=min_words,
                maximum=max_words,
                phase="initial",
                attempt=1,
            )
            word_count_history.append(word_revision)
            snapshot = {
                **snapshot,
                "surface_text": content,
                "wordCount": word_revision,
                "wordCountHistory": list(word_count_history),
            }
            if not word_revision["ok"]:
                snapshot["diagnostics"] = {
                    "classification": "validator_conflict",
                    "validator_conflicts": [{
                        "type": "word_count_revision_failed",
                        "message": "字数修订后仍未进入允许区间，已停止内容审计。",
                    }],
                }
            if (
                self.llm is not None
                and (not title.strip() or title.strip().startswith("第") and title.strip().endswith("章"))
            ):
                with self._llm_scope(
                    "chapter_title",
                    chapter_no=chapter_no,
                    phase="title_after_word_count",
                    attempt=1,
                ):
                    title = self._llm_chapter_title(content, chapter_plan, fallback=title)

            previous = next(
                (
                    item
                    for item in reversed(self.repo.list_chapter_plans())
                    if item.sequence_order < chapter_plan.sequence_order
                ),
                None,
            )
            word_count_failed = (
                (snapshot.get("diagnostics") or {}).get("classification")
                == "validator_conflict"
            )
            if word_count_failed:
                combined = None
            else:
                with self._llm_scope(
                    "chapter_scope_audit",
                    chapter_no=chapter_no,
                    phase="permission_audit",
                    attempt=1,
                ):
                    combined = run_combined_chapter_audit(
                        self.repo,
                        chapter_plan,
                        content,
                        previous,
                        self.llm,
                    )
            if combined is None:
                snapshot = {
                    **snapshot,
                    "combinedAudit": {
                        "decision": "blocked",
                        "classification": "validator_conflict",
                        "title": title,
                        "scores": {},
                        "violations": list(
                            (snapshot.get("diagnostics") or {}).get("validator_conflicts", [])
                        ),
                        "rewriteTargets": [],
                    },
                    "automaticAuditRewriteCount": 0,
                    "automaticAuditRewriteLimit": self.MAX_AUTOMATIC_AUDIT_REWRITES,
                    "manualRewriteConfirmationRequired": True,
                }
            else:
                combined_classification = getattr(
                    combined, "classification", "prose_rewriteable"
                )
                attempts = [
                    {
                        "attempt": 1,
                        "rewriteCount": 0,
                        "decision": combined.decision,
                        "classification": combined_classification,
                        "violations": combined.violations,
                    }
                ]
            if combined is not None and combined.title.strip():
                audited_title = self._sanitize_generated_title(combined.title)
                if audited_title:
                    title = audited_title
            # 改写回路：把「出问题内容 + 完整正文 + 白名单」交给 Reviser，由它自判局部改
            # 还是重写某段，并在原文基础上产出完整修订正文（不是整章从零重写）。每轮重审后
            # 「留最优版（P0 最少，其次 P1）」，且只要某轮 P0 没下降就早停——避免越改越坏。
            best_p0, best_p1 = self._severity_counts(combined) if combined else (0, 0)
            best = {"content": content, "title": title, "snapshot": snapshot, "combined": combined}
            rewrite_count = 0
            while (
                combined is not None
                and combined.decision != "accept"
                and getattr(combined, "classification", "prose_rewriteable")
                == "prose_rewriteable"
                and rewrite_count < self.MAX_AUTOMATIC_AUDIT_REWRITES
            ):
                rewrite_count += 1
                # P3：据本轮违例把越界 beat/道具从 plan 摘掉，让修订器拿到干净白名单与重审契约。
                rewrite_plan = self._redact_plan_for_rewrite(chapter_plan, combined.violations)
                with self._llm_scope(
                    "chapter_reviser",
                    chapter_no=chapter_no,
                    phase="audit_revise",
                    attempt=rewrite_count,
                ):
                    revision = self.reviser.revise(rewrite_plan, content, combined.violations)
                if not revision.ok:
                    break  # 修订失败 → 保留当前最优版
                revised_content, word_revision = self._revise_to_word_limits(
                    content=revision.prose,
                    chapter_plan=rewrite_plan,
                    target=effective_target,
                    minimum=min_words,
                    maximum=max_words,
                    phase="audit_revise",
                    attempt=rewrite_count,
                )
                word_count_history.append(word_revision)
                if not word_revision["ok"]:
                    attempts.append({
                        "attempt": rewrite_count + 1,
                        "rewriteCount": rewrite_count,
                        "decision": "blocked",
                        "classification": "validator_conflict",
                        "changeScope": revision.change_scope,
                        "violations": [{"type": "validator_conflict", "text": "字数修订后仍未进入允许区间。"}],
                    })
                    break  # 字数没救回来 → 本次作废，保留当前最优版
                ensure_text_integrity(
                    revised_content,
                    label="revised_draft_content",
                    expected_cjk=contains_cjk(content + "\n" + revised_content),
                )
                with self._llm_scope(
                    "chapter_scope_audit",
                    chapter_no=chapter_no,
                    phase="permission_audit",
                    attempt=rewrite_count + 1,
                ):
                    new_combined = run_combined_chapter_audit(
                        self.repo,
                        rewrite_plan,
                        revised_content,
                        previous,
                        self.llm,
                    )
                new_p0, new_p1 = self._severity_counts(new_combined)
                new_title = title
                if new_combined.title.strip():
                    audited_title = self._sanitize_generated_title(new_combined.title)
                    if audited_title:
                        new_title = audited_title
                new_snapshot = {
                    **snapshot,
                    "surface_text": revised_content,
                    "wordCount": word_revision,
                    "wordCountHistory": list(word_count_history),
                }
                attempts.append({
                    "attempt": rewrite_count + 1,
                    "rewriteCount": rewrite_count,
                    "decision": new_combined.decision,
                    "classification": getattr(new_combined, "classification", "prose_rewriteable"),
                    "changeScope": revision.change_scope,
                    "violations": new_combined.violations,
                })
                # 防回归：只有真正修掉 P0 才采纳为新最优并继续迭代；否则早停保留最优。
                if new_p0 < best_p0:
                    best = {"content": revised_content, "title": new_title, "snapshot": new_snapshot, "combined": new_combined}
                    best_p0, best_p1 = new_p0, new_p1
                    content, title, snapshot, combined = revised_content, new_title, new_snapshot, new_combined
                    continue
                if new_p0 == best_p0 and new_p1 < best_p1:
                    # P0 持平但 P1 更少：采纳为最优，但 P0 没降无需再烧 → 早停。
                    best = {"content": revised_content, "title": new_title, "snapshot": new_snapshot, "combined": new_combined}
                break
            content = best["content"]
            title = best["title"]
            snapshot = best["snapshot"]
            combined = best["combined"]
            if combined is not None:
                snapshot = {
                    **snapshot,
                    **combined.summary,
                    "combinedAudit": {
                        "decision": combined.decision,
                        "classification": getattr(
                            combined, "classification", "prose_rewriteable"
                        ),
                        "title": combined.title,
                        "scores": combined.scores,
                        "violations": combined.violations,
                        "rewriteTargets": combined.rewrite_targets,
                    },
                    "scopeRewriteAttempts": attempts,
                    "automaticAuditRewriteCount": rewrite_count,
                    "automaticAuditRewriteLimit": self.MAX_AUTOMATIC_AUDIT_REWRITES,
                    "manualRewriteConfirmationRequired": combined.decision != "accept",
                    "wordCountHistory": list(word_count_history),
                }

        # Single source of truth: combinedAudit.decision already re-grades every
        # structural and scope signal into P0/P1 and blocks only on P0. The legacy
        # raw `audit.severity`/`scopeAudit.severity` flags are P1-inclusive and must
        # NOT re-block here, or a P1-only draft would show decision=accept yet end
        # up blocked (the long-standing "审计通过却被阻断" contradiction).
        blocked = bool(
            not outline_only
            and (snapshot.get("combinedAudit") or {}).get("decision") != "accept"
        )
        if not outline_only:
            snapshot = {
                **snapshot,
                "pipelineAudit": {
                    "status": "blocked" if blocked else "pending_acceptance",
                    "wordCount": snapshot.get("wordCount") or {},
                    "permission": snapshot.get("combinedAudit") or {},
                },
            }

        draft = ChapterDraftRecord(
            project_id=self.project_id,
            chapter_no=chapter_no,
            title=title,
            outline=content if outline_only else self._outline_from_plan(chapter_plan, guidance),
            prose="" if outline_only else content,
            guidance=guidance,
            target_words=effective_target,
            mode=mode,
            status="draft" if outline_only else ("blocked" if blocked else "pending_acceptance"),
            context_snapshot_json=snapshot,
            candidate_group_id=((snapshot.get("style_selection", {}) or {}).get("candidateGroupId", "")),
            style_packet_json=snapshot.get("style_packet", {}) if isinstance(snapshot, dict) else {},
            score_breakdown_json=((snapshot.get("style_selection", {}) or {}).get("selectedScore", {})),
            retrieved_segment_ids_json=list(snapshot.get("retrieved_segment_ids", [])) if isinstance(snapshot, dict) else [],
            revision_history_json=list(snapshot.get("revision_history", [])) if isinstance(snapshot, dict) else [],
            created_at=_now(),
        )
        ensure_text_integrity(
            "\n".join(part for part in [draft.title, draft.outline, draft.prose, draft.guidance] if part),
            label="draft_persistence_payload",
            expected_cjk=contains_cjk(guidance + "\n" + draft.outline + "\n" + draft.prose),
        )
        draft.id = self.repo.create_chapter_draft(draft)
        return draft

    def accept(self, draft_id: int):
        draft = self.repo.get_chapter_draft(draft_id)
        if draft is None:
            raise ValueError("draft_not_found")
        combined = draft.context_snapshot_json.get("combinedAudit") or {}
        # Mirror generate(): trust combinedAudit.decision only. P1-inclusive raw
        # severities are advisory and must not block acceptance.
        if (
            draft.status == "blocked"
            or combined.get("decision") in ("rewrite", "blocked")
        ):
            raise ValueError("draft_blocked_by_audit")
        return self.indexer.accept(draft)

    def force_accept(self, draft_id: int, *, reason: str):
        draft = self.repo.get_chapter_draft(draft_id)
        if draft is None:
            raise ValueError("draft_not_found")
        reason = (reason or "").strip()
        if not reason:
            raise ValueError("force_accept_reason_required")
        snapshot = {
            **(draft.context_snapshot_json or {}),
            "forceAccept": {"reason": reason, "at": _now()},
        }
        self.repo.update_chapter_draft_snapshot(draft_id, snapshot)
        draft.context_snapshot_json = snapshot
        return self.indexer.accept(draft, force=True)

    def reject(self, draft_id: int) -> None:
        draft = self.repo.get_chapter_draft(draft_id)
        if draft and draft.prose.strip():
            self.repo.insert_style_negative_sample(
                StyleNegativeSample(
                    id=f"stynegs_{uuid.uuid4().hex[:12]}",
                    project_id=self.project_id,
                    text=draft.prose,
                    failure_types_json=self._failure_types_from_draft(draft),
                    related_source_segment_ids_json=draft.retrieved_segment_ids_json,
                    score_json=draft.score_breakdown_json,
                    created_at=_now(),
                )
            )
        self.indexer.reject(draft_id)

    def auto_write(self, *, chapters: int, target_words: int = 0, guidance: str = "") -> list[int]:
        settings = self.repo.get_writing_settings()
        ids: list[int] = []
        for _ in range(max(0, chapters)):
            draft = self.generate(guidance=guidance, target_words=target_words, mode="auto")
            ids.append(draft.id)
            if draft.status == "blocked":
                break
            if settings.require_human_acceptance:
                break
            self.accept(draft.id)
            # 方案一·强承接：连写时，每接受一章就用刚写出的正文重规划下一个待写章，
            # 让它的章纲承接上一章实际结尾（_chapter_spec 已加承接硬约束）。非致命。
            self._revise_next_chapter_against_prev()
        return ids

    def _revise_next_chapter_against_prev(self) -> None:
        try:
            from ..planner import Planner
            Planner(self.repo, self.llm).revise_next_chapter()
        except Exception:
            pass

    def _llm_chapter_title(self, prose: str, chapter_plan, fallback: str) -> str:
        try:
            from ..planner import Planner  # noqa: F401
            from .. import templates as _templates

            tmpl = None
            if "叮——" in prose or "【" in prose or "黑化值" in prose:
                tmpl = _templates.get("shuangwen_zhuangbi")
            recent = [
                item.title
                for item in self.repo.list_chapter_plans()
                if item.title and item.title.strip() and not (item.title.startswith("第") and item.title.endswith("章"))
            ]
            avoid = "、".join(recent[-8:]) or "（无）"
            material = (prose or "")[:600]
            pov_name = ""
            pov_id = str(getattr(chapter_plan, "pov_agent", "") or "")
            if pov_id:
                persona = self.repo.get_persona(pov_id)
                pov_name = self.repo.get_character_display_name(pov_id, persona.name if persona else pov_id)
            hook_clause = ""
            if tmpl is not None:
                hooks = (tmpl.structural or {}).get("chapter_title_hooks") or []
                if hooks:
                    hook_clause = "可参考这些钩子词，但不要与近期标题重复：" + "、".join(hooks[:16])
            system = (
                "你为小说本章起一个有钩子的中文标题。长度 3-18 字，只输出 JSON："
                "{\"title\":\"...\"}。避免与近期标题重复：" + avoid + "。" + hook_clause
            )
            raw = self.llm.complete(system, f"本章 POV：{pov_name or '（未知）'}\n正文片段：{material}").strip().strip("`")
            if raw.lower().startswith("json"):
                raw = raw[4:].strip()
            start, end = raw.find("{"), raw.rfind("}")
            import json as _json

            data = _json.loads(raw[start:end + 1] if 0 <= start < end else raw)
            title = str((data or {}).get("title", "")).strip().strip("“”\"' ")
            title = self._sanitize_generated_title(title)
            from ..chapter_titles import validate_chapter_title
            if title and 3 <= len(title) <= 22 and validate_chapter_title(title, recent).ok:
                return title
        except Exception:
            pass
        return fallback

    @staticmethod
    def _sanitize_generated_title(title: str) -> str:
        clean = str(title or "").strip().lstrip("#").strip()
        clean = re.sub(
            r"^第\s*[一二三四五六七八九十百千万两零〇\d]+\s*章(?:\s*[·:：—-]\s*|\s+)?",
            "",
            clean,
        ).strip()
        return clean

    def _outline_from_plan(self, chapter_plan, guidance: str) -> str:
        beats = list(getattr(chapter_plan, "scene_flow", []) or getattr(chapter_plan, "must_happen", []) or getattr(chapter_plan, "beat_goals", []) or [])
        outline = "\n".join(f"- {beat}" for beat in beats if str(beat).strip())
        exit_state = str(getattr(chapter_plan, "required_exit_state", "") or getattr(chapter_plan, "exit_state", "") or "").strip()
        if exit_state:
            outline += ("\n" if outline else "") + f"- End at: {exit_state}"
        if guidance.strip():
            outline += ("\n\n" if outline else "") + "[Writing Constraints]\n" + guidance.strip()
        return outline

    def _failure_types_from_draft(self, draft: ChapterDraftRecord) -> list[str]:
        score = draft.score_breakdown_json or {}
        failures: list[str] = []
        if score.get("caricaturePenalty", 0) >= 0.3:
            failures.append("caricature")
        if score.get("repetitionPenalty", 0) >= 0.25:
            failures.append("syntactic_repetition")
        if score.get("sourceOverlapPenalty", 0) >= 0.2:
            failures.append("source_leakage")
        if score.get("voiceSimilarity", 1) <= 0.7:
            failures.append("voice_collapse")
        if (draft.context_snapshot_json.get("combinedAudit") or {}).get("decision") == "rewrite":
            failures.append("combined_audit_block")
        if not failures:
            failures.append("manual_reject")
        return failures
