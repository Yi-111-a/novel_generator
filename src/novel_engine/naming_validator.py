from __future__ import annotations

import re
from collections.abc import Iterable
from difflib import SequenceMatcher

from .naming_profile import CultureNamingStyle, NameValidationResult, NamingProfile

BAD_THEME_TOKENS = {"锈", "铁", "骨", "灰", "肺", "血", "影", "夜", "刃"}
ROLE_TOKENS = {
    "掌柜",
    "老板",
    "大夫",
    "师爷",
    "师父",
    "师兄",
    "师姐",
    "长老",
    "司令",
    "少主",
    "小姐",
    "公子",
    "队长",
    "首领",
    "老头",
}
OBJECT_TOKENS = {"刀", "剑", "枪", "灯", "锁", "盾", "笛", "锚", "钟"}
# 舟 means "boat", not a body part — keeping it here wrongly flagged ordinary
# given names such as 陆舟 as organ-like names.
BODY_TOKENS = {"骨", "肺", "喉", "眼", "手", "指", "牙", "颅"}
MATERIAL_TOKENS = {"铜", "铁", "银", "金", "锈", "钢", "灰"}
PLACEHOLDER_TOKENS = {"某人", "那人", "无名", "甲乙", "路人", "角色"}
NICKNAME_PREFIXES = {"老", "小", "阿"}
NICKNAME_SUFFIXES = {"哥", "姐", "爷", "娘", "叔", "伯"}


def normalize_name(name: str) -> str:
    text = (name or "").strip()
    text = re.sub(r"\s+", "", text)
    text = text.replace("“", "").replace("”", "").replace('"', "").replace("'", "")
    return text


def name_root(name: str) -> str:
    text = normalize_name(name)
    parts = re.split(r"[·•\-—]", text)
    core = parts[-1] if parts and parts[-1] else text
    return core[-2:] if len(core) >= 2 else core


def extract_name_signature(name: str) -> dict[str, str | int | bool]:
    text = normalize_name(name)
    return {
        "normalized": text,
        "root": name_root(text),
        "length": len(text),
        "has_middle_dot": "·" in text or "•" in text,
        "has_hyphen": "-" in text or "—" in text,
        "has_space": " " in (name or ""),
        "last_char": text[-1] if text else "",
    }


def check_primary_structure(name: str, profile: NamingProfile) -> list[str]:
    text = normalize_name(name)
    issues: list[str] = []
    if not text:
        issues.append("empty")
    if re.fullmatch(r"[A-Za-z0-9_]+", text):
        issues.append("ascii_only")
    if any(token in text for token in PLACEHOLDER_TOKENS):
        issues.append("placeholder_token")
    if not profile.allow_surname and len(text) > 2:
        issues.append("surname_disallowed")
    return issues


def check_primary_length(name: str, profile: NamingProfile) -> list[str]:
    text = normalize_name(name)
    if not text:
        return []
    if len(text) < profile.primary_length_min:
        return ["too_short"]
    if len(text) > profile.primary_length_max:
        return ["too_long"]
    return []


def check_symbol_policy(name: str, profile: NamingProfile) -> list[str]:
    text = normalize_name(name)
    issues: list[str] = []
    if ("·" in text or "•" in text) and not profile.allow_middle_dot:
        issues.append("middle_dot_disallowed")
    if ("-" in text or "—" in text) and not profile.allow_hyphen:
        issues.append("hyphen_disallowed")
    if " " in (name or "") and not profile.allow_space:
        issues.append("space_disallowed")
    return issues


def check_lexical_category(name: str, profile: NamingProfile) -> list[str]:
    text = normalize_name(name)
    issues: list[str] = []
    if len(text) <= 1:
        issues.append("single_char_primary")
    if any(text == token or text.endswith(token) for token in ROLE_TOKENS):
        issues.append("role_like_name")
    object_like = OBJECT_TOKENS | BODY_TOKENS | MATERIAL_TOKENS
    if text in object_like:
        issues.append("object_like_name")
    # A single object/material/body character inside a short name (小眼镜、陆舟) is
    # fine; only flag names that stack two or more such characters (铁肺、骨刀).
    elif len(text) <= 3 and sum(1 for ch in text if ch in object_like) >= 2:
        issues.append("object_like_name")
    if any(token in text for token in profile.banned_tokens):
        issues.append("banned_token")
    if any(token in text for token in profile.danger_tokens):
        issues.append("danger_token")
    if sum(text.count(token) for token in BAD_THEME_TOKENS) >= 2:
        issues.append("motif_stack")
    return issues


def check_primary_vs_secondary_boundary(name: str) -> list[str]:
    text = normalize_name(name)
    issues: list[str] = []
    # Colloquial personal names that begin with 阿/小/老 (NICKNAME_PREFIXES) or end
    # in 哥/姐/爷/娘/叔/伯 (NICKNAME_SUFFIXES) — 阿珍、小眼镜、老周头 — are perfectly
    # ordinary character names in Chinese fiction, so they are no longer treated as
    # an intrusion into the primary-name slot. Only role/honorific titles used *as*
    # a name (掌柜、司令、少主) remain rejected.
    if any(token in text for token in ROLE_TOKENS):
        issues.append("honorific_intrusion")
    return issues


def check_template_overuse_signature(name: str) -> list[str]:
    text = normalize_name(name)
    issues: list[str] = []
    if len(text) >= 2 and text[0] in BAD_THEME_TOKENS:
        issues.append("theme_prefix")
    if len(text) >= 2 and text[-1] in BAD_THEME_TOKENS:
        issues.append("theme_suffix")
    if "之" in text:
        issues.append("template_of")
    return issues


def check_name_similarity(
    name: str,
    existing_names: Iterable[str],
    threshold: float = 0.75,
) -> tuple[list[str], list[str]]:
    text = normalize_name(name)
    hard: list[str] = []
    warn: list[str] = []
    root = name_root(text)
    for other in existing_names:
        other_text = normalize_name(other)
        if not other_text or other_text == text:
            continue
        if root and root == name_root(other_text):
            warn.append("same_root")
        elif len(text) >= 2 and len(other_text) >= 2 and text[0] == other_text[0]:
            warn.append("same_root")
        ratio = SequenceMatcher(a=text, b=other_text).ratio()
        if ratio >= 0.9:
            hard.append("near_duplicate")
        elif ratio >= threshold or (text[:1] == other_text[:1] and ratio >= 0.45):
            warn.append("similar")
    return sorted(set(hard)), sorted(set(warn))


def validate_primary_name(
    name: str,
    profile: NamingProfile,
    style: CultureNamingStyle | None = None,
    existing_names: Iterable[str] | None = None,
) -> NameValidationResult:
    normalized = normalize_name(name)
    hard: list[str] = []
    warn: list[str] = []
    hard.extend(check_primary_structure(normalized, profile))
    hard.extend(check_primary_length(normalized, profile))
    hard.extend(check_symbol_policy(normalized, profile))
    hard.extend(check_lexical_category(normalized, profile))
    hard.extend(check_primary_vs_secondary_boundary(normalized))
    sim_hard, sim_warn = check_name_similarity(normalized, existing_names or [])
    hard.extend(sim_hard)
    warn.extend(sim_warn)
    warn.extend(check_template_overuse_signature(normalized))
    if style and style.disallowed_templates:
        for template in style.disallowed_templates:
            if template and template in normalized:
                warn.append("style_disallowed_template")
                break
    return NameValidationResult(
        ok=not hard,
        severity="fail" if hard else ("warn" if warn else "info"),
        normalized=normalized,
        hard_fail_codes=sorted(set(hard)),
        warn_codes=sorted(set(warn)),
        details=extract_name_signature(normalized),
        suggested_repairs=[],
    )
