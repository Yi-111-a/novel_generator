from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from .models import CharacterCard, Entity, Persona
from .naming_audit import audit_name_batch
from .naming_profile import CharacterNameRecord, CultureNamingStyle, NamingProfile
from .naming_validator import normalize_name, validate_primary_name
from .repository import Repository

DEFAULT_SURNAMES = [
    "沈", "赵", "苏", "闻", "程", "许", "韩", "周", "崔", "唐",
    "江", "顾", "陆", "钟", "林", "秦", "卫", "楚", "宁", "宋",
]
DEFAULT_GIVEN = [
    "砚", "舟", "昭", "霄", "岚", "宁", "遥", "棠", "霁", "衡",
    "川", "临", "珩", "知", "行", "昀", "朔", "澄", "湛", "宸",
]
DEFAULT_TITLES = ["先生", "姑娘", "掌事", "师兄", "师姐", "前辈"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _world_culture_source(repo: Repository) -> str:
    row = repo.conn.execute("SELECT culture FROM world_bible WHERE id=1").fetchone()
    raw = ""
    if row:
        try:
            raw = json.loads(row["culture"]).get("note", "")
        except Exception:
            raw = row["culture"] or ""
    if any(token in raw for token in ("蒸汽", "异域", "混血", "联邦", "殖民")):
        return "mixed"
    return "zh"


def build_world_naming_profile(repo: Repository) -> NamingProfile:
    source = _world_culture_source(repo)
    return NamingProfile(
        profile_id="world.default",
        label="World Naming Profile",
        genre=(repo.get_tone_profile().genre or ""),
        culture_source=source,
        phonology_style="han_mixed" if source == "mixed" else "clean_han",
        primary_length_min=2,
        primary_length_max=4 if source == "mixed" else 3,
        allow_surname=True,
        allow_compound_given_name=(source == "mixed"),
        allow_middle_dot=(source == "mixed"),
        allow_hyphen=False,
        allow_space=False,
        nickname_rules={"allow_in_body": True, "allow_as_display": False},
        honorific_rules={"allow_default_display": False},
        faction_variance_policy={"shared_core": True, "max_template_families": 2},
        rare_structure_quota={"middle_dot": 1 if source == "zh" else 2, "hyphen": 0},
        motif_token_budget={"锈": 1, "铁": 1, "骨": 1, "灰": 1, "肺": 0},
        banned_tokens=["某人", "那人", "无名", "角色", "掌柜", "司令", "少主", "师父"],
        danger_tokens=["锈", "铁", "骨", "灰", "肺"],
        stopwords_for_primary=["先生", "姑娘", "掌柜", "公子", "师父", "师兄", "师姐"],
    )


def build_culture_naming_styles(repo: Repository, profile: NamingProfile) -> list[CultureNamingStyle]:
    styles = [
        CultureNamingStyle(
            style_id="style.default",
            profile_id=profile.profile_id,
            culture_id="default",
            culture_name="默认文化圈",
            surname_pool=list(DEFAULT_SURNAMES),
            given_name_pool=list(DEFAULT_GIVEN),
            title_pool=list(DEFAULT_TITLES),
            morphology_templates=["{surname}{given}"],
            nickname_patterns=["阿{given}", "小{given}"],
            honorific_patterns=["{primary}{title}", "{surname}{title}"],
            enemy_label_patterns=["{surname}家那位", "那个人"],
            symbol_policy={"middle_dot": profile.allow_middle_dot, "hyphen": profile.allow_hyphen},
            style_fingerprint={"family": "han", "lengths": [2, 3]},
        )
    ]
    for faction in repo.list_factions():
        styles.append(
            CultureNamingStyle(
                style_id=f"style.faction.{faction.faction_id}",
                profile_id=profile.profile_id,
                culture_id=faction.faction_id,
                culture_name=faction.name or faction.faction_id,
                parent_style_id="style.default",
                surname_pool=list(DEFAULT_SURNAMES),
                given_name_pool=list(DEFAULT_GIVEN),
                title_pool=list(DEFAULT_TITLES),
                morphology_templates=["{surname}{given}"],
                nickname_patterns=["阿{given}", "小{given}"],
                honorific_patterns=["{primary}{title}", "{surname}{title}"],
                enemy_label_patterns=["{surname}家的人", "对面那位"],
                symbol_policy={"middle_dot": False, "hyphen": False},
                style_fingerprint={"family": "han", "faction": faction.faction_id, "lengths": [2, 3]},
            )
        )
    return styles


def style_for_agent(repo: Repository, styles: list[CultureNamingStyle], agent_id: str) -> CultureNamingStyle:
    entity = repo.get_entity(agent_id)
    faction_id = (entity.attributes or {}).get("faction_id") if entity else None
    if faction_id:
        for style in styles:
            if style.culture_id == faction_id:
                return style
    return styles[0]


def _deterministic_pick(pool: list[str], seed: str, offset: int = 0) -> str:
    if not pool:
        return ""
    digest = hashlib.sha256(f"{seed}:{offset}".encode("utf-8")).hexdigest()
    idx = int(digest[:8], 16) % len(pool)
    return pool[idx]


def _split_seed_name(name: str) -> tuple[str, str]:
    text = normalize_name(name)
    if len(text) >= 2:
        return text[0], text[1:]
    return "", text


def generate_primary_name(
    repo: Repository,
    agent_id: str,
    base_name: str,
    profile: NamingProfile,
    style: CultureNamingStyle,
) -> tuple[str, dict[str, Any]]:
    normalized = normalize_name(base_name)
    if normalized:
        surname, given = _split_seed_name(normalized)
        if surname and given:
            return normalized, {"surname": surname, "given": given, "template": "seed"}
    surname = _deterministic_pick(style.surname_pool or DEFAULT_SURNAMES, agent_id, 0)
    given = _deterministic_pick(style.given_name_pool or DEFAULT_GIVEN, agent_id, 1)
    return f"{surname}{given}", {"surname": surname, "given": given, "template": "{surname}{given}"}


def generate_secondary_names(primary_name: str, style: CultureNamingStyle, seed_data: dict[str, Any]) -> dict[str, str]:
    surname, given = _split_seed_name(primary_name)
    short = given or primary_name
    honorific = ""
    if style.title_pool:
        honorific = f"{surname}{style.title_pool[0]}" if surname else f"{primary_name}{style.title_pool[0]}"
    nickname = ""
    if given and len(given) == 1:
        nickname = f"阿{given}"
    elif given:
        nickname = f"小{given[-1]}"
    return {
        "short_name": short,
        "nickname": nickname,
        "honorific": honorific,
        "public_alias": str(seed_data.get("public_alias", "")),
        "self_ref": str(seed_data.get("self_ref", "")),
        "enemy_label": str(seed_data.get("enemy_label", "")),
    }


def _sync_name_to_runtime(repo: Repository, record: CharacterNameRecord) -> None:
    entity = repo.get_entity(record.agent_id)
    if entity:
        attrs = dict(entity.attributes or {})
        legacy = list(attrs.get("legacy_aliases", []) or [])
        if entity.name and entity.name != record.primary_name and entity.name not in legacy:
            legacy.append(entity.name)
        attrs["legacy_aliases"] = legacy
        repo.conn.execute(
            "UPDATE entities SET name=?, attributes=? WHERE entity_id=?",
            (record.primary_name, json.dumps(attrs, ensure_ascii=False), record.agent_id),
        )

    persona = repo.get_persona(record.agent_id)
    if persona:
        repo.insert_persona(
            Persona(
                agent_id=persona.agent_id,
                name=record.primary_name,
                want=persona.want,
                values=persona.values,
                fatal_flaw=persona.fatal_flaw,
                obstacles=persona.obstacles,
                cost_threshold=persona.cost_threshold,
                voice=persona.voice,
                mannerisms=persona.mannerisms,
                motif_objects=persona.motif_objects,
                arc_state=persona.arc_state,
                cost_ledger=persona.cost_ledger,
            )
        )

    card = repo.get_card_for_agent(record.agent_id)
    if card:
        repo.add_card(
            CharacterCard(
                card_id=card.card_id,
                agent_id=card.agent_id,
                tier=card.tier,
                slot_key=card.slot_key,
                name=record.primary_name,
                one_liner=card.one_liner,
                voice_register=card.voice_register,
                defining_trait=card.defining_trait,
                core_desire=card.core_desire,
                verbal_habits=card.verbal_habits,
                key_relation=card.key_relation,
                backstory=card.backstory,
                fatal_flaw=card.fatal_flaw,
                motif_objects=card.motif_objects,
                relationship_map=card.relationship_map,
                arc=card.arc,
                appearance=card.appearance,
                social_role=card.social_role,
                psychology=card.psychology,
                created_at=card.created_at,
            )
        )
    repo.conn.commit()


def assign_character_name(repo: Repository, agent_id: str, source: str = "migration") -> CharacterNameRecord:
    profile = repo.get_naming_profile() or build_world_naming_profile(repo)
    if repo.get_naming_profile() is None:
        repo.upsert_naming_profile(profile)

    styles = repo.list_culture_naming_styles()
    if not styles:
        styles = build_culture_naming_styles(repo, profile)
        for style in styles:
            repo.upsert_culture_naming_style(style)

    style = style_for_agent(repo, styles, agent_id)
    existing = repo.get_character_name(agent_id)
    entity = repo.get_entity(agent_id) or Entity(agent_id, "character", "", {})
    current_name = entity.name
    all_names = [record.primary_name for record in repo.list_character_names() if record.agent_id != agent_id]

    primary_name, parts = generate_primary_name(repo, agent_id, current_name, profile, style)
    validation = validate_primary_name(primary_name, profile, style, all_names)
    old_name = current_name
    if not validation.ok:
        primary_name, parts = generate_primary_name(repo, agent_id, "", profile, style)
        validation = validate_primary_name(primary_name, profile, style, all_names)

    secondary = generate_secondary_names(
        primary_name,
        style,
        {"public_alias": old_name if old_name and old_name != primary_name else ""},
    )
    now = _now()
    record = CharacterNameRecord(
        agent_id=agent_id,
        profile_id=profile.profile_id,
        culture_style_id=style.style_id,
        primary_name=primary_name,
        short_name=secondary["short_name"],
        nickname=secondary["nickname"],
        honorific=secondary["honorific"],
        public_alias=secondary["public_alias"],
        self_ref=secondary["self_ref"],
        enemy_label=secondary["enemy_label"],
        display_name_locked=primary_name,
        primary_name_normalized=validation.normalized,
        name_parts_json=parts,
        source=source if source in {"seed", "llm", "migration", "manual"} else "migration",
        status="active",
        audit_flags=validation.hard_fail_codes + validation.warn_codes,
        created_at=(existing.created_at if existing else now),
        updated_at=now,
    )
    repo.upsert_character_name(record)
    _sync_name_to_runtime(repo, record)
    if old_name and old_name != primary_name:
        repo.append_character_name_history(agent_id, old_name, primary_name, "name_repair")
    return record


def reconcile_project_names(repo: Repository) -> tuple[list[CharacterNameRecord], dict[str, Any]]:
    profile = repo.get_naming_profile()
    if profile is None:
        profile = build_world_naming_profile(repo)
        repo.upsert_naming_profile(profile)

    styles = repo.list_culture_naming_styles()
    if not styles:
        styles = build_culture_naming_styles(repo, profile)
        for style in styles:
            repo.upsert_culture_naming_style(style)

    records = [
        assign_character_name(repo, entity.entity_id, source="migration")
        for entity in repo.list_entities()
        if entity.type == "character"
    ]
    batch = audit_name_batch(records, profile, styles)
    return records, asdict(batch)
