"""Cross-chapter audit and compressed continuity memory."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

from ..llm.base import LLMClient
from ..models import BatchAudit
from ..repository import Repository


@dataclass
class BatchAuditResult:
    item_violations: list[dict[str, Any]] = field(default_factory=list)
    faction_usage: list[dict[str, Any]] = field(default_factory=list)
    world_coverage: list[dict[str, Any]] = field(default_factory=list)
    reveal_progress: dict[str, Any] = field(default_factory=dict)
    character_continuity_issues: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, str] = field(default_factory=dict)


GONE_STATUSES = {"consumed", "destroyed", "sacrificed"}


class BatchAuditor:
    def __init__(self, repo: Repository, llm: LLMClient | None = None) -> None:
        self.repo = repo
        self.llm = llm

    def run(self, chapter_seq: int, tick: int = 0) -> BatchAuditResult:
        start = max(1, chapter_seq - 9)
        result = BatchAuditResult(
            item_violations=self._item_violations(chapter_seq),
            faction_usage=self._faction_usage(start, chapter_seq),
            world_coverage=self._world_coverage(),
            reveal_progress=self._reveal_progress(),
            character_continuity_issues=self._character_issues(start, chapter_seq),
        )
        result.summary = self._summary(start, chapter_seq, result)
        self.repo.upsert_batch_audit(BatchAudit(
            chapter_seq=chapter_seq,
            result_json=asdict(result),
            summary_json=result.summary,
            created_tick=tick,
        ))
        return result

    def _names(self) -> dict[str, str]:
        return {e.entity_id: e.name for e in self.repo.list_entities()}

    def _chapter_texts(self) -> list[tuple[int, str]]:
        ev_to_ch = {ev.event_id: ev.beat_id for ev in self.repo.list_events() if ev.beat_id}
        ch_seq = {c.chapter_id: c.sequence_order for c in self.repo.list_chapter_plans()}
        out: list[tuple[int, str]] = []
        for scene in self.repo.list_scenes():
            seqs = [ch_seq.get(ev_to_ch.get(eid, ""), 0) for eid in scene.source_events]
            seq = next((s for s in seqs if s), 0)
            if seq:
                out.append((seq, scene.prose_text or ""))
        return out

    def _item_violations(self, chapter_seq: int) -> list[dict[str, Any]]:
        names = self._names()
        texts = self._chapter_texts()
        out = []
        for item in self.repo.list_inventory():
            if item.status not in GONE_STATUSES:
                continue
            nm = names.get(item.object_id, item.object_id)
            seen_after = [seq for seq, text in texts
                          if seq > item.acquired_chapter and seq <= chapter_seq and nm in text]
            if seen_after:
                out.append({
                    "object_id": item.object_id,
                    "name": nm,
                    "status": item.status,
                    "gone_chapter": item.acquired_chapter,
                    "seen_after": seen_after,
                })
        return out

    def _faction_usage(self, start: int, end: int) -> list[dict[str, Any]]:
        chapters = [c for c in self.repo.list_chapter_plans()
                    if start <= (c.sequence_order or 0) <= end]
        cast = {aid for c in chapters for aid in (c.cast or [])}
        out = []
        for f in self.repo.list_factions():
            members = [m.get("agent_id") for m in (f.key_members or []) if m.get("agent_id")]
            used = [m for m in members if m in cast]
            out.append({
                "faction_id": f.faction_id,
                "name": f.name,
                "key_members": len(members),
                "used_members": len(used),
                "usage_rate": round(len(used) / max(1, len(members)), 3),
                "used_agent_ids": used,
            })
        return out

    def _world_coverage(self) -> list[dict[str, Any]]:
        out = []
        for row in self.repo.list_bible_sections():
            src = row.get("source", "")
            out.append({
                "section": row.get("section", ""),
                "title": row.get("title", ""),
                "source": src,
                "deepened": src == "w1_deepened" or "deepened" in src,
            })
        return out

    def _reveal_progress(self) -> dict[str, Any]:
        nodes = self.repo.list_reveal_nodes()
        total = len(nodes)
        discovered = len([n for n in nodes if n.discovered])
        return {
            "total": total,
            "discovered": discovered,
            "undiscovered": total - discovered,
            "rate": round(discovered / max(1, total), 3),
        }

    def _character_issues(self, start: int, end: int) -> list[dict[str, Any]]:
        out = []
        for ch in self.repo.list_chapter_plans():
            if not (start <= (ch.sequence_order or 0) <= end) or ch.status != "done":
                continue
            logged = {log.agent_id for log in self.repo.get_logs_for_chapter(ch.sequence_order)}
            missing = [aid for aid in (ch.cast or []) if aid not in logged]
            if missing:
                out.append({
                    "chapter_seq": ch.sequence_order,
                    "kind": "missing_character_log",
                    "agent_ids": missing,
                })
        return out

    def _summary(self, start: int, end: int, result: BatchAuditResult) -> dict[str, str]:
        chapters = [c for c in self.repo.list_chapter_plans()
                    if start <= (c.sequence_order or 0) <= end]
        base = {
            "plot": "；".join((c.summary or (c.beat_goals[0] if c.beat_goals else "")).strip()
                             for c in chapters if (c.summary or c.beat_goals))[:500],
            "foreshadow": f"揭示进度 {result.reveal_progress.get('discovered', 0)}/{result.reveal_progress.get('total', 0)}",
            "character": f"人物轨迹缺口 {len(result.character_continuity_issues)} 处",
        }
        if self.llm is None:
            return base
        system = "你是长篇小说审计员。把最近十章压缩为 JSON：plot, foreshadow, character。"
        user = json.dumps({
            "chapters": [{"seq": c.sequence_order, "summary": c.summary, "beats": c.beat_goals}
                         for c in chapters],
            "audit": asdict(result),
        }, ensure_ascii=False)[:6000]
        try:
            raw = self.llm.complete_at(system, user, 0.1).strip().strip("`")
            if raw.lower().startswith("json"):
                raw = raw[4:].strip()
            i, j = raw.find("{"), raw.rfind("}")
            data = json.loads(raw[i:j + 1] if 0 <= i < j else raw)
            if isinstance(data, dict):
                return {k: str(data.get(k, base[k])) for k in ("plot", "foreshadow", "character")}
        except Exception:
            pass
        return base

