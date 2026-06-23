"""Shared four-stage disclosure policy for narrative entities.

Stage 0: hidden
Stage 1: foreshadow hint only
Stage 2: public detail
Stage 3: public detail plus secret truth
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from .models import Foreshadow
from .repository import Repository

_SECRET_CLAUSE_RE = re.compile(
    r"(凶手|真凶|合谋|杀害|谋杀|尸骨|替身|冒充|幕后|黑幕|真实身份|"
    r"改命|借寿|替死|灭口|继承遗产|身份被盗用)"
)


@dataclass(frozen=True)
class DisclosureSchedule:
    entity_id: str
    entity_type: str = ""
    foreshadow_from: int = 0
    reveal_chapter: int = 0
    secret_reveal_chapter: int = 0
    foreshadow_hint: str = ""
    secret_truth: str = ""
    explicit: bool = False


def _int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _values(source: Any) -> tuple[int, int, int, str, str]:
    if isinstance(source, dict):
        get = source.get
    else:
        get = lambda key, default=None: getattr(source, key, default)
    return (
        _int(get("foreshadow_from", 0)),
        _int(get("reveal_chapter", 0)),
        _int(get("secret_reveal_chapter", 0)),
        str(get("foreshadow_hint", "") or "").strip(),
        str(get("secret_truth", "") or "").strip(),
    )


def get_disclosure_schedule(repo: Repository, entity_id: str) -> DisclosureSchedule:
    """Read one normalized schedule from cards, locations, factions or object attributes."""
    ent = getattr(repo, "get_entity", lambda _id: None)(entity_id)
    entity_type = getattr(ent, "type", "") or ""
    source: Any = None

    if entity_type == "character":
        source = getattr(repo, "get_card_for_agent", lambda _id: None)(entity_id)
    elif entity_type == "location":
        source = getattr(repo, "get_location", lambda _id: None)(entity_id)
    elif entity_type == "object":
        source = (getattr(ent, "attributes", None) or {})
    else:
        faction = getattr(repo, "get_faction", lambda _id: None)(entity_id)
        if faction is not None:
            source = faction
            entity_type = "faction"

    foreshadow_from, reveal_chapter, secret_chapter, hint, truth = _values(source or {})
    attrs = (getattr(ent, "attributes", None) or {}) if ent is not None else {}

    # Legacy binary gates become a reveal chapter. Existing empty rows remain
    # stage 2, preserving old projects.
    legacy_reveal = _int(attrs.get("available_from_chapter", attrs.get("chapter_seq", 0)))
    if not reveal_chapter and legacy_reveal:
        reveal_chapter = legacy_reveal
    if entity_type == "object" and not reveal_chapter:
        item = getattr(repo, "get_inventory_item", lambda _id: None)(entity_id)
        if item is not None:
            reveal_chapter = _int(getattr(item, "acquired_chapter", 0))

    explicit = any(
        (foreshadow_from, reveal_chapter, secret_chapter, bool(hint), bool(legacy_reveal))
    )
    faction = getattr(repo, "get_faction", lambda _id: None)(entity_id)
    if faction is not None and getattr(faction, "source", "") == "w3_far" and not explicit:
        # Preserve the old "far faction is unavailable" behavior until the
        # planner assigns it a real schedule.
        foreshadow_from = 10**9
        reveal_chapter = 10**9
        explicit = True

    if reveal_chapter and foreshadow_from > reveal_chapter:
        foreshadow_from = reveal_chapter
    return DisclosureSchedule(
        entity_id=entity_id,
        entity_type=entity_type,
        foreshadow_from=foreshadow_from,
        reveal_chapter=reveal_chapter,
        secret_reveal_chapter=secret_chapter,
        foreshadow_hint=hint,
        secret_truth=truth,
        explicit=explicit,
    )


def disclosure_stage(repo: Repository, entity_id: str, chapter_seq: int | None) -> int:
    """Return disclosure stage 0..3.

    With no chapter scope, callers are in planning/discovery mode and receive
    public detail (stage 2), never secret truth by default.
    """
    schedule = get_disclosure_schedule(repo, entity_id)
    if chapter_seq is None:
        return 2
    chapter = _int(chapter_seq)
    if not schedule.explicit:
        return 2
    if chapter < schedule.foreshadow_from:
        return 0
    if schedule.reveal_chapter and chapter < schedule.reveal_chapter:
        return 1
    if (
        schedule.secret_reveal_chapter
        and chapter >= schedule.secret_reveal_chapter
    ):
        return 3
    return 2


def set_disclosure_schedule(
    repo: Repository,
    entity_id: str,
    *,
    foreshadow_from: int | None = None,
    reveal_chapter: int | None = None,
    secret_reveal_chapter: int | None = None,
    foreshadow_hint: str | None = None,
    secret_truth: str | None = None,
) -> bool:
    """Persist a partial schedule update for any supported entity type."""
    ent = getattr(repo, "get_entity", lambda _id: None)(entity_id)
    entity_type = getattr(ent, "type", "") or ""
    target = None
    save = None
    if entity_type == "character":
        target = getattr(repo, "get_card_for_agent", lambda _id: None)(entity_id)
        save = getattr(repo, "add_card", None)
    elif entity_type == "location":
        target = getattr(repo, "get_location", lambda _id: None)(entity_id)
        save = getattr(repo, "upsert_location", None)
    else:
        target = getattr(repo, "get_faction", lambda _id: None)(entity_id)
        if target is not None:
            save = getattr(repo, "upsert_faction", None)

    values = {
        "foreshadow_from": foreshadow_from,
        "reveal_chapter": reveal_chapter,
        "secret_reveal_chapter": secret_reveal_chapter,
        "foreshadow_hint": foreshadow_hint,
        "secret_truth": secret_truth,
    }
    if target is not None and save is not None:
        for key, value in values.items():
            if value is not None:
                setattr(target, key, _int(value) if key.endswith("chapter") or key == "foreshadow_from" else str(value))
        save(target)
        return True

    if ent is None:
        return False
    attrs = dict(ent.attributes or {})
    for key, value in values.items():
        if value is not None:
            attrs[key] = _int(value) if key.endswith("chapter") or key == "foreshadow_from" else str(value)
    getattr(repo, "update_entity_attributes")(entity_id, attrs)
    return True


def _public_hint(repo: Repository, entity_id: str) -> str:
    """Build a name-free hint from public fields only."""
    ent = getattr(repo, "get_entity", lambda _id: None)(entity_id)
    entity_type = getattr(ent, "type", "") or ""
    name = str(getattr(ent, "name", "") or "")
    secret = ""
    candidates: list[str] = []
    prefix = "某个尚未露面的存在留下了间接影响"
    if entity_type == "character":
        card = getattr(repo, "get_card_for_agent", lambda _id: None)(entity_id)
        if card:
            name = card.name or name
            secret = card.secret_truth or ""
            candidates = [card.appearance, card.voice_register]
            prefix = "一个尚未露面的人留下了可辨认的痕迹"
    elif entity_type == "location":
        loc = getattr(repo, "get_location", lambda _id: None)(entity_id)
        if loc:
            name = loc.name or name
            secret = loc.secret_truth or ""
            candidates = [loc.culture_local]
            prefix = "某处尚未抵达的地方先显出了一点气息"
    elif entity_type == "object":
        attrs = dict(getattr(ent, "attributes", None) or {})
        secret = str(attrs.get("secret_truth", "") or "")
        candidates = [
            str(attrs.get("function", "") or ""),
            str(attrs.get("symbol", "") or ""),
        ]
        prefix = "一件尚未现身的物品先留下了作用或痕迹"
    else:
        faction = getattr(repo, "get_faction", lambda _id: None)(entity_id)
        if faction:
            name = faction.name or name
            secret = faction.secret_truth or ""
            candidates = [faction.ideology, faction.structure]
            prefix = "一股尚未现身的组织力量已经产生了间接影响"

    detail = next(
        (
            str(value).strip()
            for value in candidates
            if len(str(value or "").strip()) >= 4
            and str(value or "").strip() not in {"真相", "秘密", "未知"}
            and not _SECRET_CLAUSE_RE.search(str(value or ""))
            and not re.search(r"\d+\s*(?:号|栋|室|层|年|月|日)", str(value or ""))
        ),
        "",
    )
    proper_names = [
        entity.name
        for entity in repo.list_entities()
        if entity.name and len(entity.name) >= 2
    ] + [
        faction.name
        for faction in getattr(repo, "list_factions", lambda: [])()
        if faction.name and len(faction.name) >= 2
    ]
    for proper_name in proper_names:
        detail = detail.replace(proper_name, "")
    if secret:
        detail = detail.replace(secret, "")
    detail = re.sub(r"\s+", " ", detail).strip(" ，。；：")
    return f"{prefix}：{detail[:80]}。" if detail else f"{prefix}。"


def split_legacy_mystery_one_liner(text: str) -> tuple[str, str]:
    """Split legacy premise-level spoilers into public and secret surfaces."""
    raw = str(text or "").strip()
    if not raw or not _SECRET_CLAUSE_RE.search(raw):
        return raw, ""
    clauses = [part.strip() for part in re.split(r"[，；。]", raw) if part.strip()]
    public_parts = [part for part in clauses if not _SECRET_CLAUSE_RE.search(part)]
    public = "，".join(public_parts[:2]).strip()
    if public:
        public += "。"
    else:
        public = "与当前事件有关的人物，其更深身份与动机尚未公开。"
    return public, raw


def _chapter_entity_ids(repo: Repository, chapter: Any) -> set[str]:
    ids = set(
        list(getattr(chapter, "allowed_entity_ids", None) or [])
        + list(getattr(chapter, "cast", None) or [])
        + list(getattr(chapter, "location_ids", None) or [])
        + list(getattr(chapter, "items_present", None) or [])
        + list(getattr(chapter, "available_items", None) or [])
        + list(getattr(chapter, "items_introduced", None) or [])
    )
    for agent_id in getattr(chapter, "cast", None) or []:
        ent = getattr(repo, "get_entity", lambda _id: None)(agent_id)
        faction_id = (getattr(ent, "attributes", None) or {}).get("faction_id") if ent else ""
        if faction_id:
            ids.add(faction_id)
    for loc_id in getattr(chapter, "location_ids", None) or []:
        loc = getattr(repo, "get_location", lambda _id: None)(loc_id)
        if loc and loc.controlling_faction:
            faction = getattr(repo, "get_faction", lambda _id: None)(loc.controlling_faction)
            if faction is None:
                faction = next(
                    (
                        row
                        for row in getattr(repo, "list_factions", lambda: [])()
                        if row.name == loc.controlling_faction
                    ),
                    None,
                )
            if faction:
                ids.add(faction.faction_id)
    plan_text = "\n".join(
        list(getattr(chapter, "beat_goals", None) or [])
        + list(getattr(chapter, "must_happen", None) or [])
        + list(getattr(chapter, "scene_flow", None) or [])
        + [
            str(getattr(chapter, "dramatic_question", "") or ""),
            str(getattr(chapter, "exit_state", "") or ""),
            str(getattr(chapter, "required_exit_state", "") or ""),
            str(getattr(chapter, "summary", "") or ""),
        ]
    )
    if plan_text:
        for entity in repo.list_entities():
            if entity.name and len(entity.name) >= 2 and entity.name in plan_text:
                ids.add(entity.entity_id)
        for faction in getattr(repo, "list_factions", lambda: [])():
            if faction.name and len(faction.name) >= 2 and faction.name in plan_text:
                ids.add(faction.faction_id)
    return ids


def auto_schedule_disclosures(repo: Repository) -> int:
    """Assign schedules and plant ledger entries for planned non-opening entities."""
    chapters = sorted(repo.list_chapter_plans(), key=lambda row: row.sequence_order)
    if not chapters:
        return 0
    first_seen: dict[str, int] = {}
    by_sequence = {chapter.sequence_order: chapter for chapter in chapters}
    for chapter in chapters:
        for entity_id in _chapter_entity_ids(repo, chapter):
            first_seen.setdefault(entity_id, chapter.sequence_order)

    changed = 0
    for entity_id, reveal_chapter in first_seen.items():
        ent = getattr(repo, "get_entity", lambda _id: None)(entity_id)
        if getattr(ent, "type", "") == "character":
            card = getattr(repo, "get_card_for_agent", lambda _id: None)(entity_id)
            if card is not None and not card.secret_truth:
                public, secret = split_legacy_mystery_one_liner(card.one_liner)
                if secret:
                    card.one_liner = public
                    card.secret_truth = secret
                    repo.add_card(card)
        if reveal_chapter <= chapters[0].sequence_order:
            continue
        schedule = get_disclosure_schedule(repo, entity_id)
        if schedule.explicit and schedule.reveal_chapter >= 10**8:
            continue
        effective_reveal = schedule.reveal_chapter or reveal_chapter
        foreshadow_from = (
            schedule.foreshadow_from
            if schedule.foreshadow_hint
            else max(chapters[0].sequence_order, effective_reveal - 2)
        )
        hint = schedule.foreshadow_hint or _public_hint(repo, entity_id)
        if not schedule.foreshadow_hint or not schedule.reveal_chapter:
            if not set_disclosure_schedule(
                repo,
                entity_id,
                foreshadow_from=foreshadow_from,
                reveal_chapter=effective_reveal,
                foreshadow_hint=hint,
            ):
                continue
        planted_chapter = max(foreshadow_from, effective_reveal - 1)
        target = by_sequence.get(planted_chapter)
        if target is not None and entity_id not in target.allowed_entity_ids:
            target.allowed_entity_ids.append(entity_id)
            repo.upsert_chapter_plan(target)
        repo.upsert_foreshadow(Foreshadow(
            foreshadow_id=f"disclosure:{entity_id}",
            question=hint,
            linked_fact_id=entity_id,
            planted_discourse_pos=planted_chapter,
            target_payoff_beat=f"chapter:{effective_reveal}",
            status="open",
        ))
        changed += 1
    return changed
