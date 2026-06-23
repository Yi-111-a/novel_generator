"""Risk grading + disclosure-stage secret redaction.

Guards two fixes:
- Only P0 (precise canon/spoiler breaches) block a draft; heuristic P1 signals are advisory.
- A card field accidentally carrying the twist cannot leak it before its stage-3 reveal.
"""
from __future__ import annotations

from novel_engine.narration.audit import _violation_severity
from novel_engine.narration.retrieval import (
    _assemble_with_disclosure,
    _strip_secret_overlap,
)


# ---------------- risk grading ----------------

def test_p0_types_block():
    for t in [
        "unauthorized_character",
        "unauthorized_faction",
        "premature_reveal",
        "unauthorized_truth_reveal",
        "text_encoding_corruption",
        "planning_conflict",
    ]:
        assert _violation_severity(t) == "P0"


def test_heuristic_types_are_advisory():
    for t in [
        "future_event_leak",
        "canonical_name_drift",
        "canonical_address_drift",
        "new_investigation_result",
        "invented_exact_date",
        "data_conflict",
    ]:
        assert _violation_severity(t) == "P1"


# ---------------- secret redaction ----------------

def test_strip_drops_verbatim_secret_sentence():
    secret = "整容替身，冒充林晚生活并继承遗产"
    text = "她是继承遗产的富家女。整容替身，冒充林晚生活并继承遗产。她爱喝下午茶。"
    out = _strip_secret_overlap(text, secret)
    assert "整容替身" not in out
    assert "下午茶" in out


def test_assemble_redacts_below_stage3_and_reveals_at_3():
    secret = "他其实是幕后黑手操纵全局多年"
    public = "表面温和的老板。" + secret + "。"
    assert secret not in _assemble_with_disclosure(public, 2, secret)
    assert secret in _assemble_with_disclosure(public, 3, secret)


def test_assemble_keeps_clean_public_unchanged():
    assert _assemble_with_disclosure("普通商人，开着一家米铺。", 2, "他是魔教教主") == "普通商人，开着一家米铺。"
