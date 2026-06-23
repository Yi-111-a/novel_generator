from __future__ import annotations

import re


CHAPTER_RE = re.compile(r"^\s*(第[一二三四五六七八九十百千0-9]+章.*|Chapter\s+\d+.*)$", re.IGNORECASE)


def split_text_into_chapters(text: str) -> list[tuple[str, str]]:
    lines = text.replace("\r", "").split("\n")
    chunks: list[tuple[str, list[str]]] = []
    current_title = "正文"
    current_lines: list[str] = []
    for line in lines:
        if CHAPTER_RE.match(line.strip()):
            if current_lines:
                chunks.append((current_title, current_lines))
            current_title = line.strip()
            current_lines = []
        else:
            current_lines.append(line)
    if current_lines:
        chunks.append((current_title, current_lines))
    return [(title, "\n".join(body).strip()) for title, body in chunks if "\n".join(body).strip()]
