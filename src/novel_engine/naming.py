from __future__ import annotations

from dataclasses import dataclass, field

from .naming_generator import assign_character_name, build_culture_naming_styles, build_world_naming_profile
from .naming_profile import CharacterNameRecord, CultureNamingStyle, NameBatchAuditResult, NamingProfile
from .naming_validator import (
    check_name_similarity,
    check_primary_vs_secondary_boundary,
    check_symbol_policy,
    extract_name_signature,
    name_root,
    normalize_name,
    validate_primary_name,
)


@dataclass
class NameValidation:
    ok: bool
    duplicate: bool = False
    similar: bool = False
    bad: bool = False
    normalized: str = ""
    reasons: list[str] = field(default_factory=list)
    hard_fail_codes: list[str] = field(default_factory=list)
    warn_codes: list[str] = field(default_factory=list)
    details: dict = field(default_factory=dict)


def is_duplicate_name(name: str, existing_names: list[str]) -> bool:
    normalized = normalize_name(name)
    return any(normalize_name(other) == normalized for other in existing_names if other)


def is_similar_name(name: str, existing_names: list[str], threshold: float = 0.75) -> bool:
    hard, warn = check_name_similarity(name, existing_names, threshold=threshold)
    return bool(hard or warn)


def is_bad_character_name(name: str) -> bool:
    profile = NamingProfile(profile_id="adhoc")
    result = validate_primary_name(name, profile, existing_names=[])
    return bool(result.hard_fail_codes or check_primary_vs_secondary_boundary(name) or check_symbol_policy(name, profile))


def validate_character_name(name: str, existing_names: list[str]) -> NameValidation:
    profile = NamingProfile(
        profile_id="adhoc",
        banned_tokens=["某人", "那人", "无名", "角色", "掌柜", "司令", "少主"],
    )
    result = validate_primary_name(name, profile, existing_names=existing_names)
    duplicate = "duplicate" in result.hard_fail_codes or normalize_name(name) in {normalize_name(other) for other in existing_names}
    similar = bool(set(result.hard_fail_codes + result.warn_codes) & {"near_duplicate", "same_root", "similar"})
    bad = bool(set(result.hard_fail_codes) - {"duplicate", "near_duplicate"})
    reasons: list[str] = []
    if duplicate:
        reasons.append("角色名与已有角色重复")
    if similar:
        reasons.append("角色名与已有角色过于相似")
    if bad:
        reasons.append("角色名结构异常，疑似外号、职业名、器官名、材料名或模板化污染")
    return NameValidation(
        ok=result.ok,
        duplicate=duplicate,
        similar=similar,
        bad=bad,
        normalized=result.normalized,
        reasons=reasons,
        hard_fail_codes=result.hard_fail_codes,
        warn_codes=result.warn_codes,
        details=extract_name_signature(name),
    )


__all__ = [
    "CharacterNameRecord",
    "CultureNamingStyle",
    "NameBatchAuditResult",
    "NameValidation",
    "NamingProfile",
    "assign_character_name",
    "build_culture_naming_styles",
    "build_world_naming_profile",
    "is_bad_character_name",
    "is_duplicate_name",
    "is_similar_name",
    "name_root",
    "normalize_name",
    "validate_character_name",
]
