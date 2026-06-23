"""Cross-chapter narrative continuity checks (Phase 6).

These reconcile a chapter against the accumulated narrative-state ledger and emit
*advisories* (P1/P2) in the same severity model as the chapter audit. They flag
mechanically-detectable continuity defects the permission/spoiler audit cannot see
(dropped foreshadows, attribute contradictions, knowledge-provenance gaps, …).

Phase 6.0 implements Checker ④ (foreshadow lifecycle) — a pure table scan over the
planning `foreshadows` ledger, no LLM. Later phases add the typed-attribute,
knowledge-provenance and state-transition checkers on the same write-path.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .repository import Repository

# Statuses that count as "resolved / no longer owing a payoff".
_RESOLVED_STATUSES = {"paid", "paid_off", "resolved", "closed", "done", "cancelled"}
_BEAT_CHAPTER_RE = re.compile(r"chapter\s*[:：]\s*(\d+)")


@dataclass
class ContinuityFinding:
    type: str
    text: str
    severity: str = "P1"
    chapter: int | None = None

    def as_violation(self) -> dict:
        """Shape compatible with run_combined_chapter_audit violations."""
        item: dict = {"type": self.type, "text": self.text, "severity": self.severity}
        if self.chapter is not None:
            item["belongs_to_chapter"] = self.chapter
        return item


def _payoff_chapter(target_payoff_beat: str | None) -> int | None:
    """Parse a target_payoff_beat like 'chapter:2' into its chapter number."""
    if not target_payoff_beat:
        return None
    match = _BEAT_CHAPTER_RE.search(str(target_payoff_beat))
    return int(match.group(1)) if match else None


def check_foreshadow_lifecycle(repo: Repository, chapter_no: int) -> list[ContinuityFinding]:
    """Checker ④: a must-resolve foreshadow whose payoff chapter has already passed,
    yet is still open with no payoff recorded, is an overdue/dropped thread.

    The planning foreshadows live in the `foreshadows` table whose repo accessor is
    currently shadowed by the continuation `foreshadow_setups` reader, so query the
    table directly to stay robust to that collision.
    """
    rows = repo.conn.execute(
        "SELECT foreshadow_id, question, must_resolve, target_payoff_beat, status, "
        "payoff_discourse_pos FROM foreshadows"
    ).fetchall()

    findings: list[ContinuityFinding] = []
    seen: set[tuple[str, int]] = set()
    for fid, question, must_resolve, target_beat, status, payoff in rows:
        if not must_resolve:
            continue
        if str(status or "").lower() in _RESOLVED_STATUSES:
            continue
        if payoff is not None:  # a payoff position was recorded -> resolved
            continue
        due = _payoff_chapter(target_beat)
        if due is None or due >= chapter_no:
            # No deadline, or not yet due (a foreshadow due at the current chapter
            # may still be paid off within it). Only flag deadlines strictly passed.
            continue
        key = (str(question or "").strip(), due)
        if key in seen:
            continue
        seen.add(key)
        findings.append(
            ContinuityFinding(
                type="dropped_foreshadow",
                text=f"伏笔「{str(question or '').strip()}」应于第{due}章前回收，至第{chapter_no}章仍未收束。",
                severity="P1",
                chapter=due,
            )
        )
    return findings


def _attribute_facts(repo: Repository, up_to_chapter: int) -> list[tuple[str, str, str, str, int]]:
    """Typed attribute assertions, read from facts.structured.

    Expected per-fact shape (populated by the Phase 6.1 typed extractor):
        structured = {"attributes": [{"slot": "surname", "value": "程",
                                       "asserted_by": "narration"}]}
    `story_time` is the chapter ordering; seed facts (story_time 0) count as ch0.
    """
    out: list[tuple[str, str, str, str, int]] = []
    rows = repo.conn.execute(
        "SELECT structured, involved_entities, story_time FROM facts"
    ).fetchall()
    for structured, involved, story_time in rows:
        chapter = int(story_time or 0)
        if chapter > up_to_chapter:
            continue
        try:
            data = json.loads(structured or "{}")
        except (ValueError, TypeError):
            continue
        attrs = data.get("attributes") if isinstance(data, dict) else None
        if not attrs:
            continue
        try:
            entities = json.loads(involved or "[]") or [""]
        except (ValueError, TypeError):
            entities = [""]
        for attr in attrs:
            slot = str(attr.get("slot") or "").strip()
            value = str(attr.get("value") or "").strip()
            if not slot or not value:
                continue
            asserted_by = str(attr.get("asserted_by") or "narration").strip()
            for entity_id in entities:
                out.append((entity_id, slot, value, asserted_by, chapter))
    return out


def check_attribute_contradiction(repo: Repository, chapter_no: int) -> list[ContinuityFinding]:
    """Checker ①: the same entity·slot asserted two different *narration* (canon)
    values is a contradiction (P1); a *character* claim diverging from canon is only
    an unreliable claim (P2) — it may be an intended lie/misremembering.

    Dormant until the Phase 6.1 typed extractor populates facts.structured.
    """
    by_key: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for entity_id, slot, value, asserted_by, _chapter in _attribute_facts(repo, chapter_no):
        by_key.setdefault((entity_id, slot), []).append((value, asserted_by))

    findings: list[ContinuityFinding] = []
    for (entity_id, slot), entries in by_key.items():
        values = {value for value, _ in entries}
        if len(values) < 2:
            continue
        narration_values = sorted({value for value, src in entries if src == "narration"})
        if len(narration_values) >= 2:
            findings.append(
                ContinuityFinding(
                    type="attribute_contradiction",
                    text=f"实体「{entity_id}」的「{slot}」出现互相矛盾的设定：{'、'.join(narration_values)}。",
                    severity="P1",
                )
            )
        else:
            findings.append(
                ContinuityFinding(
                    type="unreliable_claim",
                    text=f"实体「{entity_id}」的「{slot}」存在与设定不一致的说法：{'、'.join(sorted(values))}"
                    "（可能是角色谎言/误记，待人工判定）。",
                    severity="P2",
                )
            )
    return findings


def check_reveal_order(repo: Repository, chapter_no: int) -> list[ContinuityFinding]:
    """Checker ② (causality form): a reveal node discovered before one of its
    prerequisite nodes means an effect was revealed before its cause.

    Runs on real `reveal_chain` data once the accept pipeline marks discovery;
    finds nothing while everything is still undiscovered (correct).
    """
    rows = repo.conn.execute(
        "SELECT node_id, prereq_node_ids, discovered, discovered_chapter, description "
        "FROM reveal_chain"
    ).fetchall()
    disc: dict[str, tuple[bool, int | None, str, str]] = {}
    for node_id, prereq, discovered, dchapter, desc in rows:
        disc[node_id] = (bool(discovered), dchapter, prereq or "[]", desc or "")

    findings: list[ContinuityFinding] = []
    for node_id, (is_disc, dchapter, prereq_raw, desc) in disc.items():
        if not is_disc or dchapter is None or dchapter > chapter_no:
            continue
        try:
            prereqs = json.loads(prereq_raw)
        except (ValueError, TypeError):
            prereqs = []
        for pid in prereqs:
            p_disc, p_chapter, _, _ = disc.get(pid, (False, None, "[]", ""))
            if (not p_disc) or p_chapter is None or p_chapter > dchapter:
                findings.append(
                    ContinuityFinding(
                        type="reveal_order_violation",
                        text=f"揭示「{(desc or node_id)[:30]}」时，其前置线索尚未出现或晚于它。",
                        severity="P1",
                        chapter=dchapter,
                    )
                )
                break
    return findings


def run_continuity_checks(repo: Repository, chapter_no: int) -> list[ContinuityFinding]:
    """Aggregate all available cross-chapter continuity checkers for a chapter.

    Incremental by design: each checker compares the current chapter against the
    accumulated ledger, never re-reading the whole book. Knowledge-provenance and
    state-transition checkers (which need prose + an LLM judgment) plug in here once
    their ledgers are maintained.
    """
    findings: list[ContinuityFinding] = []
    findings.extend(check_foreshadow_lifecycle(repo, chapter_no))
    findings.extend(check_attribute_contradiction(repo, chapter_no))
    findings.extend(check_reveal_order(repo, chapter_no))
    return findings
