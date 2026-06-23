"""Phase 6 — cross-chapter continuity checkers."""
from __future__ import annotations

import json

from novel_engine import db
from novel_engine.continuity import (
    _payoff_chapter,
    check_attribute_contradiction,
    check_foreshadow_lifecycle,
    check_reveal_order,
    run_continuity_checks,
)
from novel_engine.repository import Repository


def _repo() -> Repository:
    return Repository(db.connect(":memory:"))


def _add(repo, fid, question, *, must_resolve=1, target_beat=None, status="open", payoff=None):
    repo.conn.execute(
        "INSERT INTO foreshadows (foreshadow_id, question, linked_fact_id, "
        "planted_discourse_pos, must_resolve, target_payoff_beat, status, "
        "payoff_discourse_pos) VALUES (?,?,?,?,?,?,?,?)",
        (fid, question, "", 1, must_resolve, target_beat, status, payoff),
    )
    repo.conn.commit()


def test_parse_payoff_chapter():
    assert _payoff_chapter("chapter:2") == 2
    assert _payoff_chapter("chapter：3") == 3  # full-width colon
    assert _payoff_chapter(None) is None
    assert _payoff_chapter("beat_7") is None


def test_overdue_open_foreshadow_is_flagged():
    repo = _repo()
    _add(repo, "f1", "断戒之谜", target_beat="chapter:2")
    findings = check_foreshadow_lifecycle(repo, chapter_no=5)
    assert len(findings) == 1
    assert findings[0].type == "dropped_foreshadow"
    assert findings[0].severity == "P1"
    assert findings[0].chapter == 2


def test_not_yet_due_is_not_flagged():
    repo = _repo()
    _add(repo, "f1", "未来线索", target_beat="chapter:6")
    assert check_foreshadow_lifecycle(repo, chapter_no=5) == []


def test_resolved_or_paid_is_not_flagged():
    repo = _repo()
    _add(repo, "f1", "已收束A", target_beat="chapter:2", status="resolved")
    _add(repo, "f2", "已收束B", target_beat="chapter:2", payoff=120)
    assert check_foreshadow_lifecycle(repo, chapter_no=5) == []


def test_non_must_resolve_is_ignored():
    repo = _repo()
    _add(repo, "f1", "可选氛围伏笔", must_resolve=0, target_beat="chapter:2")
    assert check_foreshadow_lifecycle(repo, chapter_no=5) == []


def test_no_deadline_is_not_flagged_in_phase_6_0():
    repo = _repo()
    _add(repo, "f1", "长线无截止", target_beat=None)
    assert check_foreshadow_lifecycle(repo, chapter_no=5) == []


def test_duplicate_question_and_due_dedup():
    repo = _repo()
    _add(repo, "f1", "同一伏笔", target_beat="chapter:2")
    _add(repo, "f2", "同一伏笔", target_beat="chapter:2")
    assert len(check_foreshadow_lifecycle(repo, chapter_no=5)) == 1


def test_run_continuity_aggregates():
    repo = _repo()
    _add(repo, "f1", "断戒之谜", target_beat="chapter:2")
    findings = run_continuity_checks(repo, chapter_no=5)
    assert "dropped_foreshadow" in [f.type for f in findings]
    assert findings[0].as_violation()["severity"] == "P1"


# ---------------- Checker ① attribute contradiction ----------------

def _add_fact(repo, fid, entity, slot, value, asserted_by, story_time):
    structured = json.dumps(
        {"attributes": [{"slot": slot, "value": value, "asserted_by": asserted_by}]},
        ensure_ascii=False,
    )
    repo.conn.execute(
        "INSERT INTO facts (fact_id, fact_type, canonical_content, structured, "
        "story_time, location_id, involved_entities, source_event_id, embedding) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (fid, "state", "", structured, story_time, None, json.dumps([entity]), None, None),
    )
    repo.conn.commit()


def test_two_narration_values_is_contradiction():
    repo = _repo()
    _add_fact(repo, "a1", "p_x", "surname", "刘", "narration", 2)
    _add_fact(repo, "a2", "p_x", "surname", "程", "narration", 3)
    findings = check_attribute_contradiction(repo, chapter_no=5)
    assert len(findings) == 1
    assert findings[0].type == "attribute_contradiction"
    assert findings[0].severity == "P1"


def test_character_claim_diverging_is_unreliable_not_contradiction():
    repo = _repo()
    _add_fact(repo, "a1", "p_x", "surname", "程", "narration", 3)
    _add_fact(repo, "a2", "p_x", "surname", "刘", "character", 2)  # 角色记错/撒谎
    findings = check_attribute_contradiction(repo, chapter_no=5)
    assert len(findings) == 1
    assert findings[0].type == "unreliable_claim"
    assert findings[0].severity == "P2"


def test_consistent_attribute_no_finding():
    repo = _repo()
    _add_fact(repo, "a1", "p_x", "surname", "程", "narration", 2)
    _add_fact(repo, "a2", "p_x", "surname", "程", "narration", 3)
    assert check_attribute_contradiction(repo, chapter_no=5) == []


def test_future_attribute_fact_excluded():
    repo = _repo()
    _add_fact(repo, "a1", "p_x", "surname", "刘", "narration", 2)
    _add_fact(repo, "a2", "p_x", "surname", "程", "narration", 6)  # 未来章，不计入
    assert check_attribute_contradiction(repo, chapter_no=5) == []


# ---------------- Checker ② reveal-order (causality) ----------------

def _add_reveal(repo, nid, prereqs, discovered, dchapter):
    repo.conn.execute(
        "INSERT INTO reveal_chain (node_id, fact_id, kind, sequence_order, "
        "prereq_node_ids, part_id, description, discovered, discovered_chapter) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (nid, "f", "clue", 1, json.dumps(prereqs), None, nid, discovered, dchapter),
    )
    repo.conn.commit()


def test_reveal_before_undiscovered_prereq_flagged():
    repo = _repo()
    _add_reveal(repo, "n1", [], 0, None)       # 前置未出现
    _add_reveal(repo, "n2", ["n1"], 1, 3)      # 第3章就揭示了，但前置 n1 还没出现
    findings = check_reveal_order(repo, chapter_no=5)
    assert len(findings) == 1
    assert findings[0].type == "reveal_order_violation"


def test_reveal_in_causal_order_ok():
    repo = _repo()
    _add_reveal(repo, "n1", [], 1, 2)
    _add_reveal(repo, "n2", ["n1"], 1, 3)
    assert check_reveal_order(repo, chapter_no=5) == []
