from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, replace
from typing import Any

from ..chapter_scope_validator import build_chapter_scope, build_prose_chapter_scope
from ..continuation.chapter_numbering import next_chapter_no
from ..coherence import check_drift
from ..llm.base import LLMClient
from ..models import ChapterPlan
from ..narration.text_integrity import contains_cjk, ensure_text_integrity, scan_text_bundle, scan_text_integrity
from ..narration.scene_writer import WRITE_TEMPERATURE, SceneSpec, SceneWriter
from ..prompt_addons import ANTI_AI_FLAVOR_GUIDANCE
from ..repository import Repository
from ..style import (
    build_local_revision_feedback,
    build_style_packet,
    score_style_candidate,
    should_trigger_local_revision,
)
from .chapter_context import ChapterContextBuilder


# ---- 段落节奏：把"一大坨"的描写/独角戏段按句号切成均匀短段（治 ch9/ch12 那种墙）----
# 仅作用于过长段落；对白多、本来就短的段不动。纯确定性，零 LLM。
_SENT_SPLIT_RE = re.compile(r'[^。！？!?…]*(?:[。！？!?]+|…+)["」』’”\')]*|[^。！？!?…]+$')
_DIALOGUE_OPENERS = ('“', '"', '「', '『', '‘')


def _split_sentences_cn(paragraph: str) -> list[str]:
    p = paragraph.replace("\n", "").strip()
    return [s for s in _SENT_SPLIT_RE.findall(p) if s.strip()]


def _split_long_paragraph(paragraph: str, max_sent: int = 3, max_chars: int = 140) -> list[str]:
    """超过 max_sent 句或 max_chars 字的段，在句号处切成若干小段；
    一句若以引号开头（对白）则另起一段，让对白自然分行。"""
    p = paragraph.replace("\n", "").strip()
    if not p:
        return []
    sents = _split_sentences_cn(p)
    if len(sents) <= max_sent and len(p) <= max_chars:
        return [p]
    # 退化情形（整段就是同一句反复）：切开只会得到一堆相同的段，没意义，原样返回
    if len({s.strip() for s in sents}) <= 1:
        return [p]
    out: list[str] = []
    buf: list[str] = []
    n = 0
    for s in sents:
        starts_dialogue = s.strip()[:1] in _DIALOGUE_OPENERS
        if buf and starts_dialogue:
            out.append("".join(buf).strip())
            buf, n = [], 0
        buf.append(s)
        n += len(s)
        if len(buf) >= max_sent or n >= max_chars:
            out.append("".join(buf).strip())
            buf, n = [], 0
    if buf:
        out.append("".join(buf).strip())
    return [x for x in out if x]


class ChapterWriter:
    def __init__(self, repo: Repository, llm: LLMClient | None = None) -> None:
        self.repo = repo
        self.llm = llm
        self.scene_writer = SceneWriter(repo, llm)
        self.context_builder = ChapterContextBuilder(repo)

    def write_next_chapter(
        self,
        *,
        chapter_plan: ChapterPlan | None,
        guidance: str = "",
        target_words: int = 0,
        min_words: int = 0,
        max_words: int = 0,
        outline_only: bool = False,
    ) -> tuple[str, str, dict[str, Any]]:
        settings = self.repo.get_writing_settings()
        target = target_words or settings.target_words
        minimum = min_words or settings.min_words
        maximum = max_words or settings.max_words
        plan = chapter_plan or self.make_fallback_plan(target_words=target)
        beat_lines = self._beat_lines(plan, guidance)
        clean_exit_state = self._exit_state_text(plan, guidance)
        drift_guidance = check_drift(
            self.repo,
            self.llm,
            last_n=5,
            chapter_no=plan.sequence_order,
        )
        if drift_guidance:
            guidance = "\n".join(x for x in [guidance.strip(), drift_guidance] if x)
        working_plan = replace(
            plan,
            target_words=target,
            target_scenes=max(1, len(beat_lines)),
            must_happen=self._clean_text_items(plan.must_happen or beat_lines, fallback=beat_lines),
            scene_flow=self._clean_text_items(plan.scene_flow or beat_lines, fallback=beat_lines),
            required_exit_state=clean_exit_state,
            allowed_entity_ids=list(
                plan.allowed_entity_ids
                or list(plan.cast or [])
                + list(plan.location_ids or [])
                + list(plan.items_present or [])
                + list(plan.available_items or [])
            ),
            allowed_fact_ids=list(plan.allowed_fact_ids or plan.reveal_gate or []),
            package_version=max(1, int(plan.package_version or 1)),
        )
        context = self.context_builder.build(
            chapter_plan=working_plan,
            guidance=guidance,
            target_words=target,
        )
        chapter_scope = build_chapter_scope(self.repo, working_plan)
        prose_scope = build_prose_chapter_scope(self.repo, working_plan)
        context = {**context, "chapter_scope": chapter_scope, "writing_constraints": guidance}
        context["word_limits"] = {
            "targetWords": target,
            "minWords": minimum,
            "maxWords": maximum,
        }
        outline = self._outline_from_scope(prose_scope, guidance)
        expected_cjk = contains_cjk(
            json.dumps(prose_scope, ensure_ascii=False)
            + "\n"
            + guidance
        )
        scope_bundle = scan_text_bundle(
            [
                ("guidance", guidance),
                ("chapter_outline", outline),
                ("chapter_scope", json.dumps(prose_scope, ensure_ascii=False)),
            ],
            expected_cjk=expected_cjk,
        )
        if not scope_bundle.ok:
            raise ValueError(f"text_encoding_corruption:{scope_bundle.summary()}")
        if outline_only:
            clean_title = working_plan.title.strip() or (
                f"第{working_plan.sequence_order}章" if working_plan.sequence_order > 0 else "新章"
            )
            return clean_title, outline, context

        pov = working_plan.pov_agent or (working_plan.cast[0] if working_plan.cast else "")
        latest = self.repo.latest_accepted_chapter()
        meta = self.repo.get_continuation_meta()
        source_chapters = self.repo.list_source_chapters()
        existing = ""
        if latest:
            existing = latest.prose
        elif meta.write_mode == "continue_current_book" and source_chapters:
            existing = source_chapters[-1].text

        chapter_packet = context.get("style_packet", {}) if isinstance(context, dict) else {}
        chapter_group_id = f"candgrp_{uuid.uuid4().hex[:10]}"
        source_texts = [item.get("text", "") for item in chapter_packet.get("positive_exemplars", [])][:6]
        retrieved_ids = [item.get("id", "") for item in chapter_packet.get("positive_exemplars", []) if item.get("id")]
        scene_context = self._scene_context_payload(context, working_plan)
        if self.llm is None or self.llm.__class__.__name__ == "MockClient":
            surface_text = self._offline_full_chapter(working_plan, guidance=guidance)
        else:
            surface_text = self._render_full_chapter(
                working_plan=working_plan,
                guidance=guidance,
                outline=outline,
                previous_text=existing,
                prose_scope=prose_scope,
                context=context,
                scene_context=scene_context,
                style_packet=chapter_packet,
                target_words=target,
                min_words=minimum,
                max_words=maximum,
            )
        surface_text = self._clean_generated_prose(surface_text)
        ensure_text_integrity(surface_text, label="generated_prose", expected_cjk=expected_cjk)
        final_score = score_style_candidate(
            surface_text,
            style_packet=chapter_packet,
            previous_tail=existing[-400:] if existing else "",
            source_texts=source_texts,
        )
        context = {
            **context,
            "style_packet": chapter_packet,
            "chapter_package": prose_scope,
            "style_candidates": [{
                "candidateGroupId": chapter_group_id,
                "candidateId": f"cand_{uuid.uuid4().hex[:10]}",
                "text": surface_text,
                "scoreBreakdown": final_score,
                "retrievedSegmentIds": retrieved_ids,
            }],
            "style_selection": {
                "candidateGroupId": chapter_group_id,
                "selectedCandidateId": "",
                "selectedScore": final_score,
            },
            "retrieved_segment_ids": list(dict.fromkeys(retrieved_ids)),
            "revision_history": [],
            "surface_text": surface_text,
        }
        title = working_plan.title.strip() or (
            f"第{working_plan.sequence_order}章" if working_plan.sequence_order > 0 else "新章"
        )
        return title, surface_text, context

    @staticmethod
    def _clean_generated_prose(text: str) -> str:
        """Remove model-only presentation artifacts without rewriting prose."""
        raw = (text or "").strip()
        if not raw:
            return raw
        lines = raw.splitlines()
        first = lines[0].strip()
        bare = first.lstrip("#").strip()
        looks_like_heading = (
            first.startswith("#")
            or (
                3 <= len(bare) <= 22
                and len(lines) > 1
                and not bare.startswith(("【", "叮", "“", "\"", "「"))
                and not re.search(r"[。！？；：.!?]$", bare)
            )
        )
        if looks_like_heading:
            lines = lines[1:]
            while lines and not lines[0].strip():
                lines.pop(0)

        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", "\n".join(lines)) if part.strip()]
        seen: set[str] = set()
        unique: list[str] = []
        for paragraph in paragraphs:
            if paragraph in seen:
                continue
            seen.add(paragraph)
            # 段落节奏：把过长的描写/独角戏段切成均匀短段（对白多的段本就短、不受影响）
            unique.extend(_split_long_paragraph(paragraph))
        return "\n\n".join(unique).strip()

    def _outline_from_scope(self, prose_scope: dict[str, Any], guidance: str) -> str:
        lines: list[str] = []
        for item in prose_scope.get("scene_flow", []) or prose_scope.get("must_happen", []) or []:
            text = str(item or "").strip()
            if text:
                lines.append(f"- {text}")
        required_exit_state = str(prose_scope.get("required_exit_state", "") or "").strip()
        if required_exit_state:
            lines.append(f"- End at: {required_exit_state}")
        if guidance.strip():
            lines.append("")
            lines.append("[Writing Constraints]")
            lines.append(guidance.strip())
        return "\n".join(lines).strip()

    def _offline_full_chapter(self, plan: ChapterPlan, *, guidance: str = "") -> str:
        beats = self._beat_lines(plan, guidance)
        pov = plan.pov_agent or (plan.cast[0] if plan.cast else "")
        paragraphs = []
        for beat in beats:
            spec = self._scene_spec(
                pov=pov,
                beat=beat,
                chapter=plan,
                prev_tail="",
                scene_context={
                    "may_reveal": list(plan.reveal_gate or []),
                    "thread_decisions": list(plan.thread_decisions_json or []),
                    "chapter_digest": plan.summary or "",
                    "pov_state": "",
                },
            )
            paragraphs.append(self.scene_writer._offline(spec))
        text = "\n\n".join(part.strip() for part in paragraphs if part.strip())
        exit_state = str(plan.required_exit_state or plan.exit_state or "").strip()
        if exit_state and exit_state not in text:
            text = f"{text}\n\n{exit_state}".strip()
        return text

    def _render_full_chapter(
        self,
        *,
        working_plan: ChapterPlan,
        guidance: str,
        outline: str,
        previous_text: str,
        prose_scope: dict[str, Any],
        context: dict[str, Any],
        scene_context: dict[str, Any],
        style_packet: dict[str, Any],
        target_words: int,
        min_words: int,
        max_words: int,
    ) -> str:
        if self.llm is None:
            return self._offline_full_chapter(working_plan, guidance=guidance)
        rag_context = self.scene_writer._rag_context(
            working_plan,
            working_plan.pov_agent or (working_plan.cast[0] if working_plan.cast else ""),
            beat_text="\n".join(working_plan.scene_flow or working_plan.must_happen or working_plan.beat_goals or []),
            allowed_entity_ids=set(prose_scope.get("allowed_entity_ids") or []),
        )
        recent = context.get("recent_accepted", [])[-2:] if isinstance(context, dict) else []
        recent_digest = "\n".join(
            str((item or {}).get("summary", "")).strip()
            for item in recent
            if isinstance(item, dict) and str((item or {}).get("summary", "")).strip()
        )
        style_targets = json.dumps((style_packet or {}).get("target_statistics", {}), ensure_ascii=False)[:1600]
        system = (
            "You are writing one complete Chinese novel chapter in a single pass. "
            "Use the supplied chapter package as a hard permission boundary. "
            "Treat established_facts as binding canon and forbidden_inventions as absolute prohibitions. "
            "A character statement, document, rumor or hypothesis must remain attributed and must not silently "
            "replace objective canon. "
            "Do not reveal future chapters, do not invent unauthorized characters, locations, items, or truths, "
            "and do not invent exact dates, addresses, phone numbers, case/order numbers, causes of death, "
            "identity fields, spouse names, or investigation conclusions. An exact value is permitted only when "
            "that same value appears literally in the current chapter package. If the package only says that "
            "records conflict, describe the conflict without fabricating the records' precise fields. "
            "and keep the prose natural rather than outline-like. Output prose only."
        )
        user = (
            f"[chapter_package]\n{json.dumps(prose_scope, ensure_ascii=False, indent=2)[:12000]}\n\n"
            f"[word_limits]\n"
            f"targetWords={target_words}\nminWords={min_words}\nmaxWords={max_words}\n"
            "The completed prose must stay inside minWords and maxWords.\n\n"
            "[precision_boundary]\n"
            "- Exact dates, addresses, phone numbers, IDs, causes of death, spouse names and official conclusions "
            "must be copied from the chapter package or omitted.\n"
            "- Never make up realistic-looking database rows to make an investigation feel concrete.\n"
            "- Use generic wording such as '记录显示三年前已经死亡' when only relative information is authorized.\n\n"
            "- An item marked non_physical=true is only an image, memory, record or projection. Never turn it "
            "into a physical object that a character can hold, scratch with or recover.\n\n"
            f"[chapter_outline]\n{outline}\n\n"
            f"[recent_story]\n{recent_digest or str(scene_context.get('chapter_digest', '') or '').strip()}\n\n"
            f"[previous_tail]\n{(previous_text or '')[-800:]}\n\n"
            f"[rag_context]\n{rag_context[:6000]}\n\n"
            f"[style_targets]\n{style_targets}\n\n"
            f"[guidance]\n{guidance or '(none)'}"
        )
        raw = self.llm.complete_at(system, user, WRITE_TEMPERATURE).strip()
        if raw.startswith("{") and raw.endswith("}"):
            return self._offline_full_chapter(working_plan, guidance=guidance)
        return raw

    @staticmethod
    def count_words(text: str) -> int:
        """Project-wide deterministic length unit: non-whitespace characters."""
        return len(re.sub(r"\s+", "", text or ""))

    def revise_word_count(
        self,
        *,
        prose: str,
        chapter_plan: ChapterPlan,
        target_words: int,
        min_words: int,
        max_words: int,
        mode: str,
        _allow_correction: bool = True,
    ) -> str:
        """Expand or compress without changing facts or package permissions."""
        if mode not in {"expand", "compress"}:
            raise ValueError("invalid_word_count_revision_mode")
        prose_scope = build_prose_chapter_scope(self.repo, chapter_plan)
        if self.llm is None or self.llm.__class__.__name__ == "MockClient":
            return self._deterministic_word_revision(
                prose, min_words=min_words, max_words=max_words, mode=mode
            )
        action = (
            "Expand only through sensory detail, action beats, dialogue rhythm and transitions. "
            "Do not add any event, clue, entity, relationship, conclusion or exact value. "
            "Aim for about targetWords; the result MUST NOT exceed maxWords."
            if mode == "expand"
            else
            "Compress repetitions and redundant explanation. Preserve every authorized event, "
            "causal link and required exit state. "
            "Aim for about targetWords; the result MUST NOT fall below minWords. "
            "If the text is already close to targetWords, trim only lightly."
        )
        system = (
            "You revise one Chinese novel chapter for length only. "
            "The chapter package is a hard permission boundary. "
            "Never add facts, names, items, locations, factions, dates or future information. "
            "Output revised prose only."
        )
        user = (
            f"[mode]{mode}\n"
            f"[word_limits] targetWords={target_words}; minWords={min_words}; maxWords={max_words}\n"
            f"[instruction]{action}\n"
            "The revised prose must land within [minWords, maxWords], targeting ~targetWords.\n"
            f"[chapter_package]\n{json.dumps(prose_scope, ensure_ascii=False, indent=2)[:10000]}\n\n"
            f"[prose]\n{prose[:22000]}"
        )
        revised = self.llm.complete_at(system, user, WRITE_TEMPERATURE).strip()
        if revised.startswith("{") and revised.endswith("}"):
            revised = prose
        revised = self._clean_generated_prose(revised)
        # One corrective LLM pass if the revision overshot in the opposite
        # direction (e.g. a compress that dropped below minWords). Guarded by
        # _allow_correction so compress<->expand can never ping-pong.
        if _allow_correction and self.llm is not None:
            count = self.count_words(revised)
            opposite = (
                "expand" if (mode == "compress" and min_words and count < min_words)
                else "compress" if (mode == "expand" and max_words and count > max_words)
                else ""
            )
            if opposite:
                return self.revise_word_count(
                    prose=revised,
                    chapter_plan=chapter_plan,
                    target_words=target_words,
                    min_words=min_words,
                    max_words=max_words,
                    mode=opposite,
                    _allow_correction=False,
                )
        return self._deterministic_word_revision(
            revised, min_words=min_words, max_words=max_words, mode=mode
        )

    @classmethod
    def _deterministic_word_revision(
        cls,
        prose: str,
        *,
        min_words: int,
        max_words: int,
        mode: str = "",
    ) -> str:
        # Hard guarantee: clamp to BOTH bounds regardless of the requested mode,
        # so an over-aggressive LLM compress/expand can never leave the text
        # outside [min_words, max_words]. ``mode`` is kept only for call-site
        # compatibility and no longer selects which bound is enforced.
        text = (prose or "").strip()
        if not text:
            return text
        # Ceiling: drop trailing paragraphs (then hard-cut) when too long.
        if max_words and cls.count_words(text) > max_words:
            paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
            kept: list[str] = []
            for paragraph in paragraphs:
                candidate = "\n\n".join([*kept, paragraph])
                if cls.count_words(candidate) > max_words and kept:
                    break
                kept.append(paragraph)
            text = "\n\n".join(kept) or text
            if cls.count_words(text) > max_words:
                text = re.sub(r"\s+", "", text)[:max_words]
        # Floor: restate existing sentences until the minimum is reached.
        if min_words and cls.count_words(text) < min_words:
            sentences = [
                item.strip()
                for item in re.split(r"(?<=[。！？!?])", text)
                if item.strip()
            ] or [text]
            additions: list[str] = []
            index = 0
            while cls.count_words(text + "\n\n" + "\n\n".join(additions)) < min_words:
                sentence = sentences[index % len(sentences)]
                additions.append(f"这一刻的动作仍沿着原有轨迹继续：{sentence}")
                index += 1
                if index > 200:
                    break
            text = "\n\n".join([text, "".join(additions)]).strip()
            if max_words and cls.count_words(text) > max_words:
                text = re.sub(r"\s+", "", text)[:max_words]
        return text

    def _generate_candidates(
        self,
        *,
        working_plan: ChapterPlan,
        pov: str,
        beat_lines: list[str],
        prev_tail: str,
        style_packet: dict[str, Any],
        source_texts: list[str],
        candidate_group_id: str,
        beat_index: int,
        next_beat_constraint: str,
        scene_context: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if self.llm is None:
            spec = self._scene_spec(
                pov=pov,
                beat="\n".join(beat_lines),
                chapter=working_plan,
                prev_tail=prev_tail,
                scene_context=scene_context,
            )
            text = self.scene_writer._offline(spec)
            return [{
                "candidateGroupId": candidate_group_id,
                "candidateId": f"cand_{uuid.uuid4().hex[:10]}",
                "text": text,
                "scoreBreakdown": score_style_candidate(
                    text,
                    style_packet=style_packet,
                    previous_tail=prev_tail,
                    source_texts=source_texts,
                ),
                "retrievedSegmentIds": [item.get("id", "") for item in style_packet.get("positive_exemplars", [])],
            }]

        # 此处只生成 1 个 candidate，避免每 beat 4x LLM 调用导致整章 100+ 调用。
        hints = [
            "优先稳住语气与叙述节奏，控制对白与短句密度，避免显眼修辞与模板感。",
        ]
        candidates: list[dict[str, Any]] = []
        exemplars = style_packet.get("positive_exemplars", [])
        for idx, hint in enumerate(hints, 1):
            candidate_id = f"cand_{uuid.uuid4().hex[:10]}"
            exemplar_slice = exemplars[idx - 1: idx + 2] if exemplars else []
            beat_lines_with_experience = self._experience_lines(
                beat_lines,
                style_packet={**style_packet, "positive_exemplars": exemplar_slice},
            )
            beat = self._compose_styled_beat(
                beat_lines=beat_lines_with_experience,
                style_packet={**style_packet, "positive_exemplars": exemplar_slice},
                hint=hint,
                prev_tail=prev_tail,
                next_beat_constraint=next_beat_constraint,
            )
            spec = self._scene_spec(
                pov=pov,
                beat=beat,
                chapter=working_plan,
                prev_tail=prev_tail,
                scene_context=scene_context,
            )
            text = self.scene_writer.write(spec)
            score = score_style_candidate(
                text,
                style_packet=style_packet,
                previous_tail=prev_tail,
                source_texts=source_texts,
            )
            candidates.append({
                "candidateGroupId": candidate_group_id,
                "candidateId": candidate_id,
                "beatIndex": beat_index,
                "hint": hint,
                "text": text,
                "scoreBreakdown": score,
                "retrievedSegmentIds": [item.get("id", "") for item in exemplar_slice],
            })
        candidates.sort(key=lambda item: item.get("scoreBreakdown", {}).get("finalScore", 0.0), reverse=True)
        return candidates

    def _compose_styled_beat(
        self,
        *,
        beat_lines: list[str],
        style_packet: dict[str, Any],
        hint: str,
        prev_tail: str = "",
        next_beat_constraint: str = "",
    ) -> str:
        lines = list(beat_lines)
        scene_profile = style_packet.get("scene_profile", {})
        target_stats = style_packet.get("target_statistics", {})
        lines.append(
            f"风格约束：声部={scene_profile.get('discourseType', 'narration')}，"
            f"场景={scene_profile.get('sceneType', 'general')}，目标统计={target_stats}。"
        )
        exemplars = style_packet.get("positive_exemplars", [])
        if exemplars:
            lines.append("参考局部范例（只学节奏和语气，不得复用原句）：")
            for exemplar in exemplars[:3]:
                lines.append(f"- {str(exemplar.get('text', ''))[:120]}")
        negatives = style_packet.get("negative_patterns", [])
        if negatives:
            labels = [",".join(item.get("failureTypes", [])) for item in negatives[:3] if item.get("failureTypes")]
            if labels:
                lines.append(f"避免失败模式：{'；'.join(labels)}。")
        if prev_tail:
            lines.append(
                "上一拍尾部（本拍必须顺接，不能重新开场、不能重复已经发生的画面）："
                f"{prev_tail[-180:]}"
            )
        if next_beat_constraint:
            lines.append(
                "下一拍边界：以下内容属于下一拍，当前拍不要完整提前展开；"
                "当前拍最多只埋一个可感知钩子。\n"
                f"{next_beat_constraint}"
            )
        lines.append(f"候选调整：{hint}")
        return "\n".join(lines)

    def _maybe_revise_candidate(
        self,
        *,
        selected: dict[str, Any],
        working_plan: ChapterPlan,
        pov: str,
        beat_line: str,
        prev_tail: str,
        style_packet: dict[str, Any],
        source_texts: list[str],
        next_beat_constraint: str,
        scene_context: dict[str, Any],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        if self.llm is None:
            return selected, []
        score = selected.get("scoreBreakdown", {}) or {}
        if not should_trigger_local_revision(score):
            return selected, []
        feedback = build_local_revision_feedback(score)
        revised_beat = self._compose_styled_beat(
            beat_lines=self._experience_lines([beat_line], style_packet=style_packet),
            style_packet=style_packet,
            hint="局部修订，只解决明确问题。",
            prev_tail=prev_tail,
            next_beat_constraint=next_beat_constraint,
        )
        spec = self._scene_spec(
            pov=pov,
            beat=revised_beat,
            chapter=working_plan,
            prev_tail=prev_tail,
            scene_context=scene_context,
        )
        revised_text = self.scene_writer.write(spec, feedback=feedback)
        revised_score = score_style_candidate(
            revised_text,
            style_packet=style_packet,
            previous_tail=prev_tail,
            source_texts=source_texts,
        )
        revised = {
            **selected,
            "text": revised_text,
            "scoreBreakdown": revised_score,
        }
        if revised_score.get("finalScore", 0.0) < score.get("finalScore", 0.0):
            return selected, []
        return revised, [{
            "candidateId": selected.get("candidateId", ""),
            "reason": feedback,
            "beforeScore": score,
            "afterScore": revised_score,
        }]

    def _beat_lines(self, plan: ChapterPlan, guidance: str) -> list[str]:
        beat_lines = list(plan.beat_goals or [])
        if not beat_lines:
            beat_lines.append(plan.dramatic_question or plan.exit_state or "推进下一章")
        return [line for line in beat_lines if line.strip()]

    def _experience_lines(self, beat_lines: list[str], *, style_packet: dict[str, Any]) -> list[str]:
        lines = list(beat_lines)
        exp_prior = style_packet.get("experience_prior", {}) or {}
        if not exp_prior:
            return lines
        core_wound = exp_prior.get("coreWound", {}) or {}
        relationship_model = exp_prior.get("relationshipModel", {}) or {}
        prose_rules = exp_prior.get("proseRules", {}) or {}
        narrative_engines = exp_prior.get("narrativeEngines", []) or []
        core_text = core_wound.get("statement") or core_wound.get("name") or exp_prior.get("summary", "")
        if core_text:
            lines.append(f"经历层内核：{core_text}")
        tone = str(prose_rules.get("tone", "")).strip()
        sentence_rules = "；".join(str(item) for item in prose_rules.get("sentenceStrategy", [])[:2])
        character_rules = "；".join(str(item) for item in prose_rules.get("characterEngine", [])[:2])
        if tone or sentence_rules or character_rules:
            lines.append(f"经历层文风：{tone} {sentence_rules} {character_rules}".strip())
        rel_base = str(relationship_model.get("baseline", "")).strip()
        rel_rules = "；".join(str(item) for item in relationship_model.get("rules", [])[:2])
        if rel_base or rel_rules:
            lines.append(f"关系推进：{rel_base} {rel_rules}".strip())
        engine_rules = "；".join(
            str(item.get("rule", "")) for item in narrative_engines[:2] if isinstance(item, dict) and item.get("rule")
        )
        if engine_rules:
            lines.append(f"叙事发动机：{engine_rules}")
        return lines

    def _beat_povs(self, plan: ChapterPlan, *, beat_count: int, default_pov: str) -> list[str]:
        povs = list(plan.beat_povs or [])
        if not povs:
            povs = [plan.pov_agent or default_pov] * beat_count
        if len(povs) < beat_count:
            povs.extend([povs[-1] if povs else (plan.pov_agent or default_pov)] * (beat_count - len(povs)))
        return povs[:beat_count]

    def _scene_context_payload(self, context: dict[str, Any], plan: ChapterPlan) -> dict[str, Any]:
        snapshot = context.get("continuation_snapshot", {}) if isinstance(context, dict) else {}
        chapter_digest = "\n".join(str(item).strip() for item in snapshot.get("recent_source_summary", [])[-2:] if str(item).strip())
        if not chapter_digest:
            chapter_digest = str((snapshot.get("ending_state", {}) or {}).get("ending_state", "")).strip()
        if not chapter_digest:
            chapter_digest = str(plan.summary or "").strip()
        pov_state_parts: list[str] = []
        ending_state = snapshot.get("ending_state", {}) if isinstance(snapshot, dict) else {}
        if isinstance(ending_state, dict):
            ending_text = str(ending_state.get("ending_state", "")).strip()
            if ending_text:
                pov_state_parts.append(f"书末状态：{ending_text}")
        open_threads = snapshot.get("open_threads", []) if isinstance(snapshot, dict) else []
        for item in open_threads[:2]:
            question = str((item or {}).get("question", "")).strip() if isinstance(item, dict) else ""
            if question:
                pov_state_parts.append(f"未解线索：{question}")
        return {
            "chapter_digest": chapter_digest,
            "pov_state": "\n".join(pov_state_parts),
            "may_reveal": list(plan.reveal_gate or []),
            "thread_decisions": list(plan.thread_decisions_json or []),
        }

    def _scene_spec(
        self,
        *,
        pov: str,
        beat: str,
        chapter: ChapterPlan,
        prev_tail: str,
        scene_context: dict[str, Any],
    ) -> SceneSpec:
        return SceneSpec(
            pov=pov,
            beat=beat,
            chapter=chapter,
            prev_tail=prev_tail,
            may_reveal=list(scene_context.get("may_reveal", []) or []),
            thread_decisions=list(scene_context.get("thread_decisions", []) or []),
            chapter_digest=str(scene_context.get("chapter_digest", "") or ""),
            pov_state=str(scene_context.get("pov_state", "") or ""),
            current_beat=str(scene_context.get("current_beat", "") or ""),
            remaining_beats_locked=list(scene_context.get("remaining_beats_locked", []) or []),
            future_chapters_locked=list(scene_context.get("future_chapters_locked", []) or []),
        )

    def _flatten_candidates(self, beat_candidate_groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
        flattened: list[dict[str, Any]] = []
        for group in beat_candidate_groups:
            for candidate in group.get("candidates", []):
                flattened.append({
                    **candidate,
                    "beat": group.get("beat", ""),
                    "pov": group.get("pov", ""),
                })
        flattened.sort(key=lambda item: item.get("scoreBreakdown", {}).get("finalScore", 0.0), reverse=True)
        return flattened[:8]

    def make_fallback_plan(self, *, target_words: int) -> ChapterPlan:
        chapter_no = next_chapter_no(self.repo)
        return ChapterPlan(
            chapter_id="chapter_freewrite",
            arc_id="",
            sequence_order=chapter_no,
            title="",
            target_words=target_words,
            target_scenes=1,
            role="rising",
        )

    def _clean_text_items(self, values: list[str], fallback: list[str] | None = None) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for value in values:
            text = str(value or "").strip()
            if not text or text in seen:
                continue
            integrity = scan_text_integrity(text, label="plan_text", expected_cjk=contains_cjk(text))
            if not integrity.ok:
                continue
            seen.add(text)
            cleaned.append(text)
        if cleaned:
            return cleaned
        return list(fallback or [])

    def _exit_state_text(self, plan: ChapterPlan, guidance: str) -> str:
        candidates = self._clean_text_items(
            [plan.required_exit_state or "", plan.exit_state or "", guidance or ""]
        )
        if candidates:
            return candidates[0]
        return "在章末留下一个更明确、可继续追查的新疑点。"

    def _beat_lines(self, plan: ChapterPlan, guidance: str) -> list[str]:
        beat_lines = self._clean_text_items(plan.beat_goals or [])
        if beat_lines:
            return beat_lines
        fallback = self._clean_text_items(
            [plan.dramatic_question or "", plan.exit_state or "", guidance or ""]
        )
        if fallback:
            return fallback
        return [
            "主角在日常场景里先察觉到新的异常。",
            "顺着线索试探，把悬念推进一步。",
        ]

    def _default_title(self, plan: ChapterPlan) -> str:
        if plan.title.strip():
            return plan.title.strip()
        if plan.sequence_order > 0:
            return f"第{plan.sequence_order}章"
        return "新章"
