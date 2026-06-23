# -*- coding: utf-8 -*-
"""Small text-cleaning helpers shared by narration writers."""
from __future__ import annotations

import re

_LEAD_PUNCT = "。！？，；、—…\n"
_ID_RE = re.compile(r"\b(?:obj|p|cast|loc|ev)_[A-Za-z0-9_]+")
_SCENE_LABEL = re.compile(
    r"^\s*#{0,6}\s*第\s*[一二三四五六七八九十百千两零0-9]+\s*[场場章回节節幕](?:\s*[:：])?(?=\s|$)"
)


def _clean_ids(text: str) -> str:
    return _ID_RE.sub("", text or "")


def _gram_overlap(a: str, b: str, n: int = 6) -> float:
    """Character n-gram Jaccard overlap for duplicate prose checks."""
    a = "".join((a or "").split())
    b = "".join((b or "").split())
    if len(a) < n or len(b) < n:
        return 0.0
    ga = {a[i:i + n] for i in range(len(a) - n + 1)}
    gb = {b[i:i + n] for i in range(len(b) - n + 1)}
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)


def _lead_clause(text: str, limit: int = 16) -> str:
    """Return the first opening clause, up to the first punctuation mark."""
    s = (text or "").lstrip()
    out = []
    for ch in s[:limit]:
        if ch in _LEAD_PUNCT:
            break
        out.append(ch)
    return "".join(out).strip()


def _strip_scene_headers(prose: str) -> str:
    """Remove accidental leading chapter/scene labels while keeping prose."""
    if not prose:
        return prose
    s = prose.lstrip()
    for _ in range(4):
        m = _SCENE_LABEL.match(s)
        if m:
            rest = s[m.end():]
            nl = rest.find("\n")
            if nl < 0:
                s = rest.lstrip()
            else:
                same_line = rest[:nl].strip()
                after = rest[nl + 1:].lstrip()
                title_like = len(same_line) <= 8 and not re.search(r"[。！？!?，,；;]", same_line)
                s = after if same_line == "" or (title_like and after) else rest.lstrip()
            continue
        if s.startswith("#"):
            nl = s.find("\n")
            head = (s[:nl] if nl >= 0 else s).lstrip("# ").strip()
            if len(head) <= 16 and not re.search(r"[。！？!?，,；;]", head):
                s = (s[nl + 1:] if nl >= 0 else "").lstrip()
                continue
        break
    return s
