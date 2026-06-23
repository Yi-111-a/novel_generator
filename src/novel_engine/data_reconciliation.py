"""Safe compatibility reconciliation for legacy project data.

No prose, draft, accepted chapter, or entity row is deleted. Superseded
entities are marked and plan references are redirected to a stable survivor.
"""
from __future__ import annotations

import dataclasses
from typing import Any

from .entity_matching import is_garbled_name, semantically_equivalent_names
from .repository import Repository


def inspect_legacy_conflicts(repo: Repository) -> dict[str, Any]:
    entities = repo.list_entities()
    object_groups: list[dict[str, Any]] = []
    visited: set[str] = set()
    objects = [row for row in entities if row.type == "object"]
    for entity in sorted(objects, key=lambda row: (-len(row.name or ""), row.entity_id)):
        if entity.entity_id in visited:
            continue
        group = [
            other
            for other in objects
            if other.entity_id not in visited
            and semantically_equivalent_names(entity.name, other.name)
        ]
        if len(group) > 1:
            survivor = max(group, key=lambda row: (len(row.name or ""), -row.created_tick))
            object_groups.append(
                {
                    "survivor_id": survivor.entity_id,
                    "survivor_name": survivor.name,
                    "merged_ids": [
                        row.entity_id for row in group if row.entity_id != survivor.entity_id
                    ],
                    "aliases": [row.name for row in group if row.entity_id != survivor.entity_id],
                }
            )
            visited.update(row.entity_id for row in group)
    garbled = [
        {"entity_id": row.entity_id, "type": row.type, "name": row.name}
        for row in entities
        if is_garbled_name(row.name)
    ]
    replaced_names = [
        {
            "agent_id": row.agent_id,
            "primary_name": row.primary_name,
            "replaced_by_agent_id": row.replaced_by_agent_id,
        }
        for row in repo.list_character_names()
        if row.status != "active" or row.replaced_by_agent_id
    ]
    return {
        "object_alias_groups": object_groups,
        "garbled_entities": garbled,
        "replaced_character_names": replaced_names,
    }


def reconcile_legacy_conflicts(repo: Repository, *, apply: bool = False) -> dict[str, Any]:
    report = inspect_legacy_conflicts(repo)
    report["applied"] = bool(apply)
    if not apply:
        return report

    replacements: dict[str, str] = {}
    for group in report["object_alias_groups"]:
        survivor_id = group["survivor_id"]
        survivor = repo.get_entity(survivor_id)
        if survivor is None:
            continue
        aliases = list((survivor.attributes or {}).get("legacy_aliases") or [])
        aliases.extend(group["aliases"])
        repo.update_entity_attributes(
            survivor_id,
            {"legacy_aliases": list(dict.fromkeys(alias for alias in aliases if alias))},
        )
        for old_id in group["merged_ids"]:
            replacements[old_id] = survivor_id
            repo.update_entity_attributes(
                old_id,
                {
                    "merged_into": survivor_id,
                    "status": "deprecated",
                    "preserve_for_history": True,
                },
            )

    for plan in repo.list_chapter_plans():
        def _replace(values: list[str]) -> list[str]:
            return list(dict.fromkeys(replacements.get(value, value) for value in values))

        updated = dataclasses.replace(
            plan,
            available_items=_replace(list(plan.available_items or [])),
            items_present=_replace(list(plan.items_present or [])),
            items_introduced=_replace(list(plan.items_introduced or [])),
            items_consumed=_replace(list(plan.items_consumed or [])),
            allowed_entity_ids=_replace(list(plan.allowed_entity_ids or [])),
        )
        repo.upsert_chapter_plan(updated)

    for history in repo.list_character_name_history():
        entity = repo.get_entity(str(history.get("agent_id") or ""))
        old_name = str(history.get("old_primary_name") or "").strip()
        if entity is None or not old_name or old_name == entity.name:
            continue
        aliases = list((entity.attributes or {}).get("legacy_aliases") or [])
        aliases.append(old_name)
        repo.update_entity_attributes(
            entity.entity_id,
            {"legacy_aliases": list(dict.fromkeys(aliases))},
        )
    report["replacement_map"] = replacements
    return report
