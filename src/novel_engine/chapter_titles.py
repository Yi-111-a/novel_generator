from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher


_TEMPLATE_PREFIXES = (
    "第",
    "序",
    "终",
    "尾声",
    "番外",
)


def normalize_title(title: str) -> str:
    text = (title or "").strip()
    text = text.strip("《》\"'[]()（）【】")
    text = re.sub(r"\s+", "", text)
    return text


def title_signature(title: str) -> str:
    text = normalize_title(title)
    text = re.sub(r"[，。！？、,.!?;；:：\-—_~·]", "", text)
    return text


def _ngrams(text: str, n_min: int = 2, n_max: int = 4) -> set[str]:
    grams: set[str] = set()
    if not text:
        return grams
    for n in range(n_min, n_max + 1):
        if len(text) < n:
            continue
        for i in range(len(text) - n + 1):
            grams.add(text[i : i + n])
    return grams


def is_duplicate_title(title: str, existing_titles: list[str]) -> bool:
    sig = title_signature(title)
    if not sig:
        return False
    return any(title_signature(t) == sig for t in existing_titles if t)


def is_near_duplicate_title(title: str, existing_titles: list[str], threshold: float = 0.72) -> bool:
    sig = title_signature(title)
    if not sig:
        return False
    grams = _ngrams(sig)
    for other in existing_titles:
        other_sig = title_signature(other)
        if not other_sig or other_sig == sig:
            continue
        ratio = SequenceMatcher(a=sig, b=other_sig).ratio()
        overlap = len(grams & _ngrams(other_sig)) / max(1, len(grams | _ngrams(other_sig)))
        if ratio >= threshold or overlap >= threshold:
            return True
    return False


def is_template_title(title: str) -> bool:
    text = normalize_title(title)
    if not text:
        return True
    if re.fullmatch(r"第[0-9一二三四五六七八九十百千零两]+章", text):
        return True
    if len(text) <= 2:
        return True
    return any(text.startswith(prefix) and len(text) <= 4 for prefix in _TEMPLATE_PREFIXES)


@dataclass
class TitleValidation:
    ok: bool
    duplicate: bool = False
    near_duplicate: bool = False
    template: bool = False
    normalized: str = ""
    reasons: list[str] = field(default_factory=list)


def validate_chapter_title(title: str, existing_titles: list[str], pending_titles: list[str] | None = None) -> TitleValidation:
    pending_titles = pending_titles or []
    normalized = normalize_title(title)
    reasons: list[str] = []
    duplicate = is_duplicate_title(normalized, [*existing_titles, *pending_titles])
    near_duplicate = is_near_duplicate_title(normalized, [*existing_titles, *pending_titles])
    template = is_template_title(normalized)
    if not normalized:
        reasons.append("标题为空")
    if duplicate:
        reasons.append("标题与既有章节重复")
    if near_duplicate:
        reasons.append("标题与既有章节近似重复")
    if template:
        reasons.append("标题过于模板化")
    return TitleValidation(
        ok=not reasons,
        duplicate=duplicate,
        near_duplicate=near_duplicate,
        template=template,
        normalized=normalized,
        reasons=reasons,
    )


def repair_chapter_title(title: str, chapter_no: int, existing_titles: list[str], pending_titles: list[str] | None = None) -> str:
    pending_titles = pending_titles or []
    candidate = normalize_title(title)
    if candidate and validate_chapter_title(candidate, existing_titles, pending_titles).ok:
        return candidate
    base = f"第{chapter_no}章"
    if not is_duplicate_title(base, [*existing_titles, *pending_titles]):
        return base
    suffix = 2
    while True:
        candidate = f"{base}-{suffix}"
        if not is_duplicate_title(candidate, [*existing_titles, *pending_titles]):
            return candidate
        suffix += 1
