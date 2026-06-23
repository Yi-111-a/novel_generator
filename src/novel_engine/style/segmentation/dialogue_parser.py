from __future__ import annotations

import re

_DIALOGUE_BLOCK_RE = re.compile(r"[“\"「『][^”\"」』\n]{1,180}[”\"」』]")


def split_paragraph_units(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"\n{1,2}", text or "") if part.strip()]


def split_dialogue_turns(text: str) -> list[str]:
    turns = [match.group(0).strip() for match in _DIALOGUE_BLOCK_RE.finditer(text or "")]
    return [turn for turn in turns if turn]
