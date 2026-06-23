from __future__ import annotations

from datetime import datetime, timezone

from ..continuation.distill import continuation_graph_summary
from ..models import StoryBibleRecord
from ..repository import Repository


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class StoryBibleBuilder:
    def __init__(self, repo: Repository) -> None:
        self.repo = repo

    def _character_payloads(self) -> list[dict]:
        out: list[dict] = []
        for persona in self.repo.list_personas():
            name_record = self.repo.get_character_name(persona.agent_id)
            out.append(
                {
                    "agentId": persona.agent_id,
                    "name": persona.name,
                    "primaryName": name_record.primary_name if name_record else persona.name,
                    "displayName": self.repo.get_character_display_name(persona.agent_id, persona.name),
                    "shortName": name_record.short_name if name_record else "",
                    "nickname": name_record.nickname if name_record else "",
                    "honorific": name_record.honorific if name_record else "",
                    "publicAlias": name_record.public_alias if name_record else "",
                    "enemyLabel": name_record.enemy_label if name_record else "",
                    "cultureStyleId": name_record.culture_style_id if name_record else "",
                    "want": persona.want,
                    "fatal_flaw": persona.fatal_flaw,
                }
            )
        return out

    def _location_payloads(self) -> list[dict]:
        faction_names = {f.faction_id: f.name for f in self.repo.list_factions()}
        return [
            {
                "locId": loc.loc_id,
                "name": loc.name,
                "summary": loc.summary or loc.geo_full,
                "detail": loc.detail,
                "parent": loc.parent,
                "controllingFaction": faction_names.get(loc.controlling_faction, loc.controlling_faction),
            }
            for loc in self.repo.list_locations()
        ]

    def _faction_payloads(self) -> list[dict]:
        loc_names = {loc.loc_id: loc.name for loc in self.repo.list_locations()}
        out: list[dict] = []
        for faction in self.repo.list_factions():
            out.append(
                {
                    "factionId": faction.faction_id,
                    "name": faction.name,
                    "summary": faction.summary,
                    "ideology": faction.ideology,
                    "goals": faction.goals,
                    "territory": faction.territory,
                    "territoryNames": [loc_names.get(loc_id, loc_id) for loc_id in faction.territory],
                    "relations": faction.relations,
                }
            )
        return out

    def _relationship_payloads(self) -> list[dict]:
        name_of = {entity.entity_id: entity.name for entity in self.repo.list_entities()}
        name_of.update({faction.faction_id: faction.name for faction in self.repo.list_factions()})
        return [
            {
                "src": edge.src,
                "srcName": name_of.get(edge.src, edge.src),
                "rel": edge.rel,
                "dst": edge.dst,
                "dstName": name_of.get(edge.dst, edge.dst),
                "intensity": edge.intensity,
                "sinceChapter": edge.since_chapter,
                "lastActiveChapter": edge.last_active_chapter,
                **({"note": edge.meta.get("note", "")} if edge.meta.get("note") else {}),
            }
            for edge in self.repo.list_edges()
        ]

    def _world_config_payload(self, *, title: str, theme: str = "", source_title: str = "", series_id: str = "") -> dict:
        sections: dict[str, str] = {}
        for row in self.repo.list_bible_sections():
            text = (row["body_full"] or "").strip()
            if not text:
                continue
            sections[row["section"]] = (sections.get(row["section"], "") + ("\n" if row["section"] in sections else "") + text).strip()
        return {
            "title": title,
            **({"theme": theme} if theme else {}),
            **({"source_book_title": source_title} if source_title else {}),
            **({"series_bible": {"series_id": series_id}} if series_id else {}),
            "sections": sections,
            "graphSummary": continuation_graph_summary(self.repo),
        }

    def build_for_original(self, *, title: str = "", theme: str = "", source_text: str = "") -> StoryBibleRecord:
        record = StoryBibleRecord(
            source_type="original",
            title_style_json={"mode": "generated"},
            world_config_json={**self._world_config_payload(title=title, theme=theme), "seed": source_text[:2000]},
            characters_json=self._character_payloads(),
            locations_json=self._location_payloads(),
            factions_json=self._faction_payloads(),
            items_json=[{"name": e.name} for e in self.repo.list_entities() if e.type == "object"],
            relationships_json=self._relationship_payloads(),
            timeline_json=[],
            open_threads_json=[{"question": t.central_question, "status": t.status} for t in self.repo.list_threads()],
            last_state_json={"accepted_chapters": len(self.repo.list_accepted_chapters())},
            narrative_constraints_json={"mode": "chapter_first"},
            updated_at=_now(),
        )
        self.repo.upsert_story_bible_record(record)
        return record

    def build_for_continuation(self, *, title: str = "") -> StoryBibleRecord:
        chapters = self.repo.list_source_chapters()
        meta = self.repo.get_continuation_meta()
        current_title = meta.current_book_title or title
        source_title = meta.source_book_title or title
        recent = chapters[-8:]
        record = StoryBibleRecord(
            source_type="continuation",
            title_style_json={"mode": "source_derived", "examples": [c.title for c in chapters[:5] if c.title]},
            world_config_json={
                **self._world_config_payload(title=current_title, source_title=source_title, series_id=meta.series_id),
                "series_bible": {
                    "source_chapters": len(chapters),
                    "series_id": meta.series_id,
                },
            },
            characters_json=self._character_payloads(),
            locations_json=self._location_payloads(),
            factions_json=self._faction_payloads(),
            items_json=[{"name": e.name} for e in self.repo.list_entities() if e.type == "object"],
            relationships_json=self._relationship_payloads(),
            timeline_json=[{"chapter_no": c.chapter_no, "title": c.title, "summary": c.summary} for c in recent],
            open_threads_json=[{"question": t.central_question, "status": t.status} for t in self.repo.list_threads()],
            last_state_json={
                "latest_source_chapter_no": chapters[-1].chapter_no if chapters else 0,
                "ending_state": recent[-1].summary if recent else "",
            },
            narrative_constraints_json={
                "write_mode": meta.write_mode or "continue_current_book",
                "chapter_start_no": meta.chapter_start_no or (chapters[-1].chapter_no + 1 if chapters else 1),
                "current_book_title": current_title,
                "time_position": meta.time_position,
                "protagonist_strategy": meta.protagonist_strategy,
                "inherit_unresolved_threads": meta.inherit_unresolved_threads,
                "continuation_hint": meta.continuation_hint,
                "preserve_source_style": True,
            },
            updated_at=_now(),
        )
        self.repo.upsert_story_bible_record(record)
        return record
