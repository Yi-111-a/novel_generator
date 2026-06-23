from __future__ import annotations

import re
from dataclasses import dataclass, field


_MULTI_Q_RE = re.compile(r"\?{3,}")
_PRIVATE_USE_RE = re.compile(r"[\uE000-\uF8FF]")
_CJK_RE = re.compile(r"[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]")
_ASCII_WORD_RE = re.compile(r"[A-Za-z]{4,}")
_COMMON_MOJIBAKE_HINTS = (
    "鍙緭鍑",
    "鐩爣",
    "鏈珷",
    "鏈珷",
    "姝ｆ枃",
    "绔犺妭",
)


@dataclass
class TextIntegrityIssue:
    code: str
    message: str


@dataclass
class TextIntegrityResult:
    ok: bool
    issues: list[TextIntegrityIssue] = field(default_factory=list)

    def summary(self) -> str:
        return "；".join(issue.message for issue in self.issues)


def contains_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text or ""))


def _question_ratio(text: str) -> float:
    compact = re.sub(r"\s+", "", text or "")
    if not compact:
        return 0.0
    return compact.count("?") / max(1, len(compact))


def _looks_ascii_collapsed(text: str, expected_cjk: bool) -> bool:
    if not expected_cjk:
        return False
    compact = re.sub(r"\s+", "", text or "")
    if len(compact) < 24:
        return False
    if contains_cjk(compact):
        return False
    return len(_ASCII_WORD_RE.findall(compact)) <= 2


def scan_text_integrity(
    text: str,
    *,
    label: str = "text",
    expected_cjk: bool = False,
) -> TextIntegrityResult:
    raw = text or ""
    issues: list[TextIntegrityIssue] = []

    if not raw.strip():
        return TextIntegrityResult(ok=True, issues=[])

    if "\ufffd" in raw:
        issues.append(TextIntegrityIssue("replacement_char", f"{label} contains replacement characters"))
    if _PRIVATE_USE_RE.search(raw):
        issues.append(TextIntegrityIssue("private_use_mojibake", f"{label} contains private-use mojibake glyphs"))
    if _MULTI_Q_RE.search(raw):
        issues.append(TextIntegrityIssue("triple_question", f"{label} contains repeated question-mark corruption"))
    if any(token in raw for token in _COMMON_MOJIBAKE_HINTS):
        issues.append(TextIntegrityIssue("common_mojibake", f"{label} contains mojibake-like prompt fragments"))
    if _question_ratio(raw) >= 0.08 and len(raw.strip()) >= 24:
        issues.append(TextIntegrityIssue("high_question_ratio", f"{label} has an abnormal question-mark ratio"))
    if _looks_ascii_collapsed(raw, expected_cjk=expected_cjk):
        issues.append(TextIntegrityIssue("ascii_collapse", f"{label} lost expected CJK content"))

    unique: list[TextIntegrityIssue] = []
    seen: set[tuple[str, str]] = set()
    for issue in issues:
        key = (issue.code, issue.message)
        if key in seen:
            continue
        seen.add(key)
        unique.append(issue)
    return TextIntegrityResult(ok=not unique, issues=unique)


def scan_text_bundle(
    entries: list[tuple[str, str]],
    *,
    expected_cjk: bool = False,
) -> TextIntegrityResult:
    issues: list[TextIntegrityIssue] = []
    for label, text in entries:
        result = scan_text_integrity(text, label=label, expected_cjk=expected_cjk)
        issues.extend(result.issues)
    return TextIntegrityResult(ok=not issues, issues=issues)


def ensure_text_integrity(
    text: str,
    *,
    label: str = "text",
    expected_cjk: bool = False,
) -> None:
    result = scan_text_integrity(text, label=label, expected_cjk=expected_cjk)
    if not result.ok:
        raise ValueError(f"text_encoding_corruption:{result.summary()}")
