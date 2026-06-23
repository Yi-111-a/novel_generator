from __future__ import annotations

from novel_engine.naming_audit import audit_name_batch
from novel_engine.naming_profile import CharacterNameRecord, CultureNamingStyle, NamingProfile


def test_batch_audit_flags_motif_budget_and_alias_ratio():
    profile = NamingProfile(
        profile_id="p1",
        motif_token_budget={"锈": 1},
        rare_structure_quota={"middle_dot": 0, "hyphen": 0},
    )
    style = CultureNamingStyle(style_id="s1", profile_id="p1", culture_id="default")
    records = [
        CharacterNameRecord(agent_id="a1", profile_id="p1", culture_style_id="s1", primary_name="锈舟", nickname="阿舟"),
        CharacterNameRecord(agent_id="a2", profile_id="p1", culture_style_id="s1", primary_name="锈衡", nickname="阿衡"),
    ]
    result = audit_name_batch(records, profile, [style])
    assert result.ok is False
    codes = {issue.code for issue in result.issues}
    assert "motif_budget_exceeded" in codes
    assert "alias_ratio_too_high" in codes
