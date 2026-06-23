from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


NameType = Literal["primary", "short", "nickname", "honorific", "self_ref", "public_alias", "enemy_label"]
Severity = Literal["info", "warn", "fail", "blocker"]


@dataclass
class NamingProfile:
    profile_id: str
    scope: Literal["world"] = "world"
    label: str = ""
    genre: str = ""
    culture_source: str = "zh"
    phonology_style: str = "clean_han"
    primary_length_min: int = 2
    primary_length_max: int = 4
    allow_surname: bool = True
    allow_compound_given_name: bool = False
    allow_middle_dot: bool = False
    allow_hyphen: bool = False
    allow_space: bool = False
    nickname_rules: dict[str, Any] = field(default_factory=dict)
    honorific_rules: dict[str, Any] = field(default_factory=dict)
    faction_variance_policy: dict[str, Any] = field(default_factory=dict)
    rare_structure_quota: dict[str, int] = field(default_factory=dict)
    motif_token_budget: dict[str, int] = field(default_factory=dict)
    banned_tokens: list[str] = field(default_factory=list)
    danger_tokens: list[str] = field(default_factory=list)
    stopwords_for_primary: list[str] = field(default_factory=list)
    version: int = 1


@dataclass
class CultureNamingStyle:
    style_id: str
    profile_id: str
    culture_id: str
    culture_name: str = ""
    parent_style_id: str | None = None
    surname_pool: list[str] = field(default_factory=list)
    given_name_pool: list[str] = field(default_factory=list)
    title_pool: list[str] = field(default_factory=list)
    morphology_templates: list[str] = field(default_factory=list)
    disallowed_templates: list[str] = field(default_factory=list)
    nickname_patterns: list[str] = field(default_factory=list)
    honorific_patterns: list[str] = field(default_factory=list)
    enemy_label_patterns: list[str] = field(default_factory=list)
    symbol_policy: dict[str, bool] = field(default_factory=dict)
    style_fingerprint: dict[str, Any] = field(default_factory=dict)


@dataclass
class CharacterNameRecord:
    agent_id: str
    profile_id: str
    culture_style_id: str
    primary_name: str
    short_name: str = ""
    nickname: str = ""
    honorific: str = ""
    public_alias: str = ""
    self_ref: str = ""
    enemy_label: str = ""
    display_name_locked: str = ""
    primary_name_normalized: str = ""
    name_parts_json: dict[str, Any] = field(default_factory=dict)
    source: Literal["seed", "llm", "migration", "manual"] = "llm"
    status: Literal["active", "replaced", "deprecated"] = "active"
    replaced_by_agent_id: str | None = None
    audit_flags: list[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""


@dataclass
class NameValidationResult:
    ok: bool
    severity: Severity
    normalized: str
    hard_fail_codes: list[str] = field(default_factory=list)
    warn_codes: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)
    suggested_repairs: list[str] = field(default_factory=list)


@dataclass
class NameBatchAuditIssue:
    code: str
    severity: Severity
    message: str
    affected_agent_ids: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass
class NameBatchAuditResult:
    ok: bool
    blocked: bool
    profile_id: str
    issues: list[NameBatchAuditIssue] = field(default_factory=list)
    summary_metrics: dict[str, Any] = field(default_factory=dict)
    regeneration_queue: list[str] = field(default_factory=list)
