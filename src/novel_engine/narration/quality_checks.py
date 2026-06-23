from __future__ import annotations

import re
from dataclasses import dataclass, field

from .text_integrity import scan_text_integrity


_BUG_PATTERNS = [
    r"\bfac_[a-zA-Z0-9_]+\b",
    r"\bnamed_[a-zA-Z0-9_]+\b",
    r"\bchapter_id\b",
    r"\bJSON\b",
    r"\bprompt\b",
    r"只输出\s*JSON",
    r"在场人物",
    r"可用道具",
]


@dataclass
class QualityIssue:
    code: str
    message: str


@dataclass
class QualityCheckResult:
    ok: bool
    issues: list[QualityIssue] = field(default_factory=list)


def detect_bug_signals(prose: str) -> list[QualityIssue]:
    text = prose or ""
    issues: list[QualityIssue] = []
    for pattern in _BUG_PATTERNS:
        if re.search(pattern, text, flags=re.IGNORECASE):
            issues.append(QualityIssue("bug_signal", f"正文疑似泄漏系统控制信息：{pattern}"))
    return issues


def detect_repeated_paragraphs(prose: str) -> list[QualityIssue]:
    seen: set[str] = set()
    issues: list[QualityIssue] = []
    for paragraph in [p.strip() for p in re.split(r"\n\s*\n", prose or "") if p.strip()]:
        # Short dialogue/response paragraphs may legitimately recur in
        # different scenes. Only substantive repeated paragraphs indicate a
        # generation duplication.
        if len(re.sub(r"\s+", "", paragraph)) < 30:
            continue
        if paragraph in seen:
            issues.append(QualityIssue("repeated_paragraph", "正文出现重复段落"))
            break
        seen.add(paragraph)
    return issues


def detect_title_leak_into_prose(title: str, prose: str) -> list[QualityIssue]:
    if not (title or "").strip():
        return []
    lines = [line.strip() for line in (prose or "").splitlines() if line.strip()]
    if lines and lines[0] == (title or "").strip():
        return [QualityIssue("title_leak", "章节标题混入正文首行")]
    return []


def _longest_common_substring(a: str, b: str) -> str:
    """Longest contiguous run shared by a and b (DP, fine for short windows)."""
    if not a or not b:
        return ""
    prev = [0] * (len(b) + 1)
    best_len = best_end = 0
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        ai = a[i - 1]
        for j in range(1, len(b) + 1):
            if ai == b[j - 1]:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best_len:
                    best_len = cur[j]
                    best_end = i
        prev = cur
    return a[best_end - best_len : best_end]


def detect_repeated_opening(
    prose: str,
    prev_prose: str,
    *,
    window: int = 200,
    min_overlap: int = 40,
) -> str:
    """Return the overlapping run when this chapter's opening re-narrates the
    previous chapter's opening near-verbatim; else "".

    Compares the first `window` non-whitespace chars of each opening and flags a
    shared contiguous run of at least `min_overlap` chars — a "previously on"
    recap that the within-chapter repeated-paragraph check cannot see.
    """
    head = re.sub(r"\s+", "", prose or "")[:window]
    prev_head = re.sub(r"\s+", "", prev_prose or "")[:window]
    if len(head) < min_overlap or len(prev_head) < min_overlap:
        return ""
    shared = _longest_common_substring(head, prev_head)
    return shared if len(shared) >= min_overlap else ""


def run_quality_checks(prose: str, title: str = "") -> QualityCheckResult:
    issues = []
    integrity = scan_text_integrity(prose, label="prose", expected_cjk=True)
    issues.extend(QualityIssue(issue.code, issue.message) for issue in integrity.issues)
    issues.extend(detect_bug_signals(prose))
    issues.extend(detect_repeated_paragraphs(prose))
    issues.extend(detect_title_leak_into_prose(title, prose))
    return QualityCheckResult(ok=not issues, issues=issues)
