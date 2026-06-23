from __future__ import annotations


def normalize_write_mode(value: str) -> str:
    if value in {"continue_current_book", "new_series_book"}:
        return value
    return "continue_current_book"
