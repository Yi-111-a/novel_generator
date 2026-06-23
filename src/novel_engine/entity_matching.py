"""Deterministic entity-name matching and conservative alias reconciliation."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


_SPACE_RE = re.compile(r"\s+")
_BAD_ALIAS_RE = re.compile(r"[\ufffd]|(?:锟|烫烫烫|屯屯屯)")


def normalize_entity_name(value: str) -> str:
    return _SPACE_RE.sub("", str(value or "").strip()).strip("“”\"'「」『』")


def is_garbled_name(value: str) -> bool:
    text = normalize_entity_name(value)
    return bool(text and _BAD_ALIAS_RE.search(text))


def is_specific_variant(generic: str, specific: str) -> bool:
    """Return True for modifier + head-noun variants, without merging compounds.

    Examples: ``纸条`` -> ``染血纸条`` and ``手机`` -> ``黑色手机``.
    ``手机`` -> ``手机壳`` deliberately stays distinct.
    """
    short = normalize_entity_name(generic)
    long = normalize_entity_name(specific)
    if not short or short == long or len(short) < 2 or len(long) <= len(short):
        return False
    if not long.endswith(short):
        return False
    modifier = long[: -len(short)]
    return 1 <= len(modifier) <= 8


def semantically_equivalent_names(left: str, right: str) -> bool:
    a = normalize_entity_name(left)
    b = normalize_entity_name(right)
    return bool(a and b and (a == b or is_specific_variant(a, b) or is_specific_variant(b, a)))


@dataclass(frozen=True)
class NameMatch:
    entity_id: str
    entity_type: str
    name: str
    start: int
    end: int


def longest_name_matches(
    text: str,
    rows: Iterable[tuple[str, str, str]],
) -> list[NameMatch]:
    """Match non-overlapping names, preferring the longest legal name first."""
    haystack = str(text or "")
    candidates: list[NameMatch] = []
    seen_rows: set[tuple[str, str, str]] = set()
    for entity_id, entity_type, raw_name in rows:
        name = normalize_entity_name(raw_name)
        key = (str(entity_id), str(entity_type), name)
        if not name or key in seen_rows:
            continue
        seen_rows.add(key)
        for found in re.finditer(re.escape(name), haystack):
            candidates.append(
                NameMatch(
                    entity_id=str(entity_id),
                    entity_type=str(entity_type),
                    name=name,
                    start=found.start(),
                    end=found.end(),
                )
            )

    accepted: list[NameMatch] = []
    occupied: list[tuple[int, int]] = []
    for match in sorted(
        candidates,
        key=lambda item: (-(item.end - item.start), item.start, item.entity_id),
    ):
        if any(match.start < end and start < match.end for start, end in occupied):
            continue
        accepted.append(match)
        occupied.append((match.start, match.end))
    return sorted(accepted, key=lambda item: (item.start, item.end))


def best_existing_name(name: str, candidates: Iterable[tuple[str, str]]) -> str:
    """Resolve a generic/specific variant to the most specific existing entity id."""
    matches = [
        (entity_id, normalize_entity_name(existing))
        for entity_id, existing in candidates
        if semantically_equivalent_names(name, existing)
    ]
    if not matches:
        return ""
    matches.sort(key=lambda row: (-len(row[1]), row[0]))
    return matches[0][0]
