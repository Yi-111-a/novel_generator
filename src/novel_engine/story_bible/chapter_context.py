from __future__ import annotations

from dataclasses import asdict
from typing import Any

from ..continuation.snapshot import build_continuation_snapshot
from ..models import ChapterPlan, StoryBibleRecord
from ..repository import Repository
from ..style import build_style_packet


class ChapterContextBuilder:
    def __init__(self, repo: Repository) -> None:
        self.repo = repo

    def build(self, *, chapter_plan: ChapterPlan | None, guidance: str = "",
              target_words: int = 0) -> dict[str, Any]:
        story_bible = self.repo.get_story_bible_record() or StoryBibleRecord()
        accepted = self.repo.list_accepted_chapters()
        source_chapters = self.repo.list_source_chapters()
        recent_accepted = accepted[-3:]
        recent_source = source_chapters[-3:]
        previous_tail = ""
        if recent_accepted:
            previous_tail = recent_accepted[-1].prose
        elif recent_source:
            previous_tail = recent_source[-1].text
        style_packet = build_style_packet(
            self.repo,
            chapter_plan=chapter_plan,
            guidance=guidance,
            previous_tail=previous_tail,
        )
        life_model = self.repo.latest_author_life_model()
        recent_logs: dict[str, list[dict[str, Any]]] = {}
        for persona in self.repo.list_personas()[:8]:
            logs = self.repo.get_character_logs(persona.agent_id, last_n=3)
            if logs:
                recent_logs[self.repo.get_character_display_name(persona.agent_id, persona.name)] = [asdict(log) for log in logs]
        return {
            "story_bible": asdict(story_bible),
            "continuation_meta": asdict(self.repo.get_continuation_meta()),
            "continuation_snapshot": build_continuation_snapshot(self.repo),
            "chapter_plan": asdict(chapter_plan) if chapter_plan else None,
            "guidance": guidance,
            "target_words": target_words,
            "style_packet": asdict(style_packet),
            "author_life_model": asdict(life_model) if life_model else None,
            "style_diagnostics": self.repo.style_corpus_summary(),
            "recent_accepted": [asdict(ch) for ch in recent_accepted],
            "recent_source": [asdict(ch) for ch in recent_source],
            "recent_logs": recent_logs,
        }
