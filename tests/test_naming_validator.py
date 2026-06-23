from __future__ import annotations

from novel_engine.naming import validate_character_name
from novel_engine.naming_profile import CultureNamingStyle, NamingProfile
from novel_engine.naming_validator import validate_primary_name


def test_primary_name_rejects_honorific_intrusion():
    profile = NamingProfile(profile_id="p1")
    style = CultureNamingStyle(style_id="s1", profile_id="p1", culture_id="default")
    result = validate_primary_name("少主", profile, style, [])
    assert result.ok is False
    assert "honorific_intrusion" in result.hard_fail_codes or "role_like_name" in result.hard_fail_codes


def test_primary_name_flags_similar_existing_names():
    profile = NamingProfile(profile_id="p1")
    style = CultureNamingStyle(style_id="s1", profile_id="p1", culture_id="default")
    result = validate_primary_name("沈砚", profile, style, ["沈妍"])
    assert result.ok is False or "same_root" in result.warn_codes or "similar" in result.warn_codes


def test_compat_validate_character_name_keeps_old_shape():
    result = validate_character_name("铁肺哥", ["铁肺"])
    assert result.ok is False
    assert result.bad is True
