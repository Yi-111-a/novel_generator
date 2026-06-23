from __future__ import annotations

import uuid
from datetime import datetime, timezone

from ..chapter_titles import repair_chapter_title, validate_chapter_title
from ..llm.base import LLMClient
from ..models import AcceptedChapterRecord, ChapterDraftRecord, Event, Scene
from ..narration.fact_delta import commit_fact_delta
from ..narration.fact_extractor import FactExtractor
from ..narration.scene_anchor import capture_scene_anchors
from ..narration.story_clock import capture_story_clock
from ..narration.text_integrity import contains_cjk, ensure_text_integrity
from ..repository import Repository


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ChapterIndexer:
    def __init__(self, repo: Repository, llm: LLMClient | None = None) -> None:
        self.repo = repo
        self.llm = llm
        self.extractor = FactExtractor(repo, llm)

    def accept(self, draft: ChapterDraftRecord, *, force: bool = False) -> AcceptedChapterRecord:
        allowed_statuses = {"draft", "pending_acceptance"}
        if force:
            allowed_statuses.add("blocked")
        if draft.status not in allowed_statuses:
            raise ValueError("draft_not_accept_ready")

        expected_cjk = contains_cjk(draft.title + "\n" + draft.prose)
        if draft.title.strip():
            ensure_text_integrity(draft.title, label="accepted_draft_title", expected_cjk=expected_cjk)
        ensure_text_integrity(draft.prose, label="accepted_draft_prose", expected_cjk=expected_cjk)

        summary = self._summary(draft.prose)
        chapter_plan = next(
            (item for item in self.repo.list_chapter_plans() if item.sequence_order == draft.chapter_no),
            None,
        )
        combined = (draft.context_snapshot_json or {}).get("combinedAudit") or {}
        recent_titles = [item.title for item in self.repo.list_accepted_chapters()]
        title = self._resolve_title(
            prose=draft.prose,
            draft=draft,
            combined_title=str(combined.get("title", "")).strip(),
            recent_titles=recent_titles,
            chapter_no=draft.chapter_no,
        )

        accepted = AcceptedChapterRecord(
            project_id=draft.project_id,
            draft_id=draft.id,
            chapter_no=draft.chapter_no,
            title=title,
            prose=draft.prose,
            summary=summary,
            created_at=_now(),
        )

        with self.repo.transaction():
            accepted.id = self.repo.insert_accepted_chapter(accepted)
            self.repo.update_chapter_draft_status(draft.id, "accepted", accepted.created_at)

            if chapter_plan is not None:
                chapter_plan.title = title or chapter_plan.title
                chapter_plan.summary = summary
                chapter_plan.status = "done"
                self.repo.upsert_chapter_plan(chapter_plan)

            tick = len(self.repo.list_events()) + 1
            delta = self.extractor.extract(
                draft.prose,
                chapter_plan.pov_agent if chapter_plan else "",
                list(chapter_plan.cast if chapter_plan else []),
                type("Spec", (), {"may_reveal": list(chapter_plan.reveal_gate if chapter_plan else [])})(),
            )
            event_ids = self.extractor.commit(
                delta,
                chapter_plan.pov_agent if chapter_plan else "",
                list(chapter_plan.cast if chapter_plan else []),
                tick,
                chapter=chapter_plan,
            )
            if not event_ids:
                event_id = f"ev_{uuid.uuid4().hex[:8]}"
                self.repo.append_event(
                    Event(
                        event_id=event_id,
                        story_time=tick,
                        actors=list(chapter_plan.cast if chapter_plan else []),
                        action_type="chapter_accepted",
                        payload={"content": summary},
                        location_id=(chapter_plan.location_ids[0] if chapter_plan and chapter_plan.location_ids else None),
                        perceivers=list(chapter_plan.cast if chapter_plan else []),
                        beat_id=(chapter_plan.chapter_id if chapter_plan else None),
                    )
                )
                event_ids = [event_id]

            fact_delta = (draft.context_snapshot_json or {}).get("factDelta") or {}
            commit_fact_delta(
                self.repo,
                list(fact_delta.get("assertions") or []),
                chapter_no=draft.chapter_no,
                source_event_id=event_ids[0] if event_ids else None,
            )

            scene_id = f"sc_{uuid.uuid4().hex[:8]}"
            self.repo.insert_scene(
                Scene(
                    scene_id=scene_id,
                    discourse_order=len(self.repo.list_scenes()) + 1,
                    source_events=event_ids,
                    pov=chapter_plan.pov_agent if chapter_plan else "",
                    target_tension=float(chapter_plan.target_tension if chapter_plan else 0.5),
                    prose_text=draft.prose,
                    newly_revealed=list(delta.reveals),
                )
            )

        # 关键场景档案在事务外捕获（辅助数据，不必与正章原子）：锁定/追加场景不变事实，
        # 供后续章节的跨章一致性审计比对。无 LLM 时是空操作。
        try:
            capture_scene_anchors(self.repo, chapter_plan, draft.prose, self.llm)
        except Exception:
            pass

        # 故事时钟（阶段1）：抽本章 {章末时间/关键事件时间/死线} → events.story_clock + timeline。
        # 同样在事务外（辅助数据）；无时钟数据/无 LLM 时空操作。供下一章规划施加时间硬约束。
        try:
            capture_story_clock(self.repo, chapter_plan, draft.prose, self.llm, event_ids)
        except Exception:
            pass
        return accepted

    def reject(self, draft_id: int) -> None:
        self.repo.update_chapter_draft_status(draft_id, "rejected")

    def _resolve_title(
        self,
        *,
        prose: str,
        draft: ChapterDraftRecord,
        combined_title: str,
        recent_titles: list[str],
        chapter_no: int,
    ) -> str:
        for candidate in [combined_title, draft.title]:
            title = str(candidate or "").strip()
            if title and validate_chapter_title(title, recent_titles).ok:
                return title
        if prose.strip() and self.llm is not None:
            title = self._name_chapter(prose, recent_titles, chapter_no)
            if title:
                return title
        return repair_chapter_title("", chapter_no, recent_titles)

    def _name_chapter(self, prose: str, recent_titles: list[str], chapter_no: int) -> str:
        try:
            avoid = "、".join(title for title in recent_titles if title) or "（无）"
            raw = self.llm.complete(
                "为小说章节起一个 3-12 字中文标题，只输出 JSON：{\"title\":\"...\"}。",
                f"[avoid]\n{avoid}\n\n[prose]\n{prose[:700]}",
            ).strip().strip("`")
            if raw.lower().startswith("json"):
                raw = raw[4:].strip()
            start, end = raw.find("{"), raw.rfind("}")
            import json

            data = json.loads(raw[start:end + 1] if 0 <= start < end else raw)
            title = str((data or {}).get("title", "")).strip()
            if validate_chapter_title(title, recent_titles).ok:
                return title
        except Exception:
            pass
        return repair_chapter_title("", chapter_no, recent_titles)

    def _summary(self, prose: str) -> str:
        text = (prose or "").strip().replace("\r", "")
        if not text:
            return ""
        pieces = [line.strip() for line in text.split("\n") if line.strip()]
        joined = " ".join(pieces)
        return joined[:220]
