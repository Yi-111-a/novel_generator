from __future__ import annotations

from ..repository import Repository


def next_chapter_no(repo: Repository) -> int:
    meta = repo.get_continuation_meta()
    accepted_count = len(repo.list_accepted_chapters())

    if meta.write_mode == "continue_current_book":
        return max(1, meta.latest_source_chapter_no + accepted_count + 1)

    if meta.write_mode == "new_series_book":
        return max(1, accepted_count + 1)

    return max(1, accepted_count + 1)


def resolve_chapter_start_no(repo: Repository) -> int:
    meta = repo.get_continuation_meta()
    if meta.write_mode == "continue_current_book":
        return max(1, meta.latest_source_chapter_no + 1)
    if meta.write_mode == "new_series_book":
        return 1
    return max(1, meta.chapter_start_no or 1)
