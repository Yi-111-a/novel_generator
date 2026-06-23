"""Repository layer."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from .models import (
    AcceptedChapterRecord,
    Arc,
    AuthorExperienceFragment,
    AuthorExperienceSource,
    AuthorLifeModel,
    AuthorWritingSheet,
    BatchAudit,
    Beat,
    ChapterDraftRecord,
    ChapterPlan,
    CharacterCard,
    CharacterChapterLog,
    ContinuationJobRecord,
    ContinuationMeta,
    EmotionalState,
    Ending,
    Entity,
    Event,
    Fact,
    Faction,
    GraphEdge,
    Foreshadow,
    InventoryItem,
    KnowledgeItem,
    Location,
    Part,
    Persona,
    ReaderKnowledge,
    RevealNode,
    Scene,
    SceneAnchor,
    SourceChapter,
    SourceChunk,
    SourceDocument,
    StoryBibleRecord,
    StyleCluster,
    StyleClaim,
    StyleNegativeSample,
    StylePacket,
    StyleProfile,
    StyleSegment,
    Thread,
    ToneProfile,
    WritingSettings,
)
from .naming_profile import CharacterNameRecord, CultureNamingStyle, NamingProfile


class Repository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self._tx_depth = 0

    def _commit(self) -> None:
        if self._tx_depth == 0:
            self.conn.commit()

    @contextmanager
    def transaction(self):
        outermost = self._tx_depth == 0
        self._tx_depth += 1
        if outermost:
            self.conn.execute("BEGIN")
        try:
            yield
        except Exception:
            self._tx_depth -= 1
            if outermost:
                self.conn.rollback()
            raise
        else:
            self._tx_depth -= 1
            if outermost:
                self.conn.commit()

    # ---------- world_bible ----------
    def set_world_bible(
        self,
        *,
        setting_core: str = "",
        geography: dict[str, Any] | None = None,
        culture: dict[str, Any] | None = None,
        physics_rules: list[str] | None = None,
        protagonist_want: str = "",
        theme: str = "",
        exposition_release_rules: list[dict[str, Any]] | None = None,
    ) -> None:
        self.conn.execute(
            """INSERT INTO world_bible
                 (id, setting_core, geography, culture, physics_rules, protagonist_want,
                  theme, exposition_release_rules)
               VALUES (1, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 setting_core=excluded.setting_core,
                 geography=excluded.geography,
                 culture=excluded.culture,
                 physics_rules=excluded.physics_rules,
                 protagonist_want=excluded.protagonist_want,
                 theme=excluded.theme,
                 exposition_release_rules=excluded.exposition_release_rules""",
            (
                setting_core,
                json.dumps(geography or {}, ensure_ascii=False),
                json.dumps(culture or {}, ensure_ascii=False),
                json.dumps(physics_rules or [], ensure_ascii=False),
                protagonist_want,
                theme,
                json.dumps(exposition_release_rules or [], ensure_ascii=False),
            ),
        )
        self._commit()

    def get_exposition_rules(self) -> list[dict[str, Any]]:
        row = self.conn.execute(
            "SELECT exposition_release_rules FROM world_bible WHERE id=1"
        ).fetchone()
        return json.loads(row["exposition_release_rules"]) if row else []

    def get_physics_rules(self) -> list[str]:
        row = self.conn.execute("SELECT physics_rules FROM world_bible WHERE id=1").fetchone()
        if not row:
            return []
        return json.loads(row["physics_rules"])

    def get_world_bible(self) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM world_bible WHERE id=1").fetchone()
        if not row:
            return {}
        out = dict(row)
        for key in ("geography", "culture", "physics_rules", "exposition_release_rules", "antagonist_profile"):
            if key in out:
                try:
                    out[key] = json.loads(out[key] or ("[]" if key in {"physics_rules", "exposition_release_rules"} else "{}"))
                except Exception:
                    out[key] = [] if key in {"physics_rules", "exposition_release_rules"} else {}
        return out

    def set_world_bible_antagonist(self, antagonist_id: str, profile: dict[str, Any]) -> None:
        self.conn.execute(
            """INSERT INTO world_bible (id, antagonist_id, antagonist_profile)
               VALUES (1, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 antagonist_id=excluded.antagonist_id,
                 antagonist_profile=excluded.antagonist_profile""",
            (antagonist_id, json.dumps(profile or {}, ensure_ascii=False)),
        )
        self._commit()

    # ---------- locations锛埪?2.3 鍦扮偣涓€绛夊疄浣擄級 ----------
    def upsert_location(self, loc: Location) -> None:
        self.conn.execute(
            """INSERT INTO locations
                 (loc_id, part_id, name, geo_full, connects_to, controlling_faction, notable_items,
                  level, parent, culture_local, summary, detail, foreshadow_from, reveal_chapter,
                  secret_reveal_chapter, foreshadow_hint, secret_truth)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(loc_id) DO UPDATE SET
                 part_id=excluded.part_id, name=excluded.name, geo_full=excluded.geo_full,
                 connects_to=excluded.connects_to,
                 controlling_faction=excluded.controlling_faction,
                 notable_items=excluded.notable_items,
                 level=excluded.level, parent=excluded.parent,
                 culture_local=excluded.culture_local,
                 summary=excluded.summary, detail=excluded.detail,
                 foreshadow_from=excluded.foreshadow_from,
                 reveal_chapter=excluded.reveal_chapter,
                 secret_reveal_chapter=excluded.secret_reveal_chapter,
                 foreshadow_hint=excluded.foreshadow_hint,
                 secret_truth=excluded.secret_truth""",
            (loc.loc_id, loc.part_id, loc.name, loc.geo_full,
             json.dumps(loc.connects_to, ensure_ascii=False), loc.controlling_faction,
             json.dumps(loc.notable_items, ensure_ascii=False),
             loc.level, loc.parent, loc.culture_local, loc.summary, loc.detail,
             loc.foreshadow_from, loc.reveal_chapter, loc.secret_reveal_chapter,
             loc.foreshadow_hint, loc.secret_truth),
        )
        self._commit()

    def enrich_location(self, loc_id: str, summary: str, detail: str,
                        culture_local: str, level: str = "", parent: str = "",
                        geo_full: str = "") -> None:
        """Docstring omitted."""
        if geo_full:
            self.conn.execute(
                """UPDATE locations SET summary=?, detail=?, culture_local=?, level=?, parent=?, geo_full=?
                   WHERE loc_id=?""",
                (summary, detail, culture_local, level, parent, geo_full, loc_id),
            )
        else:
            self.conn.execute(
                """UPDATE locations SET summary=?, detail=?, culture_local=?, level=?, parent=?
                   WHERE loc_id=?""",
                (summary, detail, culture_local, level, parent, loc_id),
            )
        self._commit()

    def get_location(self, loc_id: str) -> Location | None:
        r = self.conn.execute("SELECT * FROM locations WHERE loc_id=?", (loc_id,)).fetchone()
        return _row_to_location(r) if r else None

    def list_locations(self, part_id: str | None = None) -> list[Location]:
        if part_id is None:
            rows = self.conn.execute("SELECT * FROM locations").fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM locations WHERE part_id=?", (part_id,)).fetchall()
        return [_row_to_location(r) for r in rows]

    # ---------- 关键场景档案（scene_anchors）----------
    def upsert_scene_anchor(self, anchor: SceneAnchor) -> None:
        self.conn.execute(
            """INSERT INTO scene_anchors
                 (scene_id, name, kind, location_id, canonical_facts, aliases,
                  established_chapter, created_at)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(scene_id) DO UPDATE SET
                 name=excluded.name, kind=excluded.kind, location_id=excluded.location_id,
                 canonical_facts=excluded.canonical_facts, aliases=excluded.aliases,
                 established_chapter=excluded.established_chapter""",
            (
                anchor.scene_id, anchor.name, anchor.kind, anchor.location_id,
                json.dumps(anchor.canonical_facts, ensure_ascii=False),
                json.dumps(anchor.aliases, ensure_ascii=False),
                int(anchor.established_chapter or 0), anchor.created_at,
            ),
        )
        self._commit()

    def get_scene_anchor(self, scene_id: str) -> SceneAnchor | None:
        r = self.conn.execute("SELECT * FROM scene_anchors WHERE scene_id=?", (scene_id,)).fetchone()
        return _row_to_scene_anchor(r) if r else None

    def list_scene_anchors(self) -> list[SceneAnchor]:
        rows = self.conn.execute("SELECT * FROM scene_anchors ORDER BY established_chapter, scene_id").fetchall()
        return [_row_to_scene_anchor(r) for r in rows]

    def delete_scene_anchor(self, scene_id: str) -> None:
        self.conn.execute("DELETE FROM scene_anchors WHERE scene_id=?", (scene_id,))
        self._commit()

    # ---------- world_bible_sections锛埪?2 鍏ㄩ噺瀛樻。锛屼笉鎽樿锛沇1 鍔犱袱绾?summary/detail锛?----------
    def add_bible_section(self, section: str, title: str, body_full: str,
                          source: str = "user", created_at: int = 0, summary: str = "") -> None:
        """逐字保存一节世界圣经原文。"""
        if not (body_full or "").strip():
            return
        self.conn.execute(
            """INSERT INTO world_bible_sections (section, title, body_full, summary, source, created_at)
               VALUES (?,?,?,?,?,?)""",
            (section, title, body_full, summary, source, created_at),
        )
        self._commit()

    def upsert_w1_section(self, section: str, title: str, summary: str, detail: str) -> None:
        """写入每节唯一的 W1 权威条目。"""
        if not (detail or "").strip():
            return
        self.conn.execute(
            "DELETE FROM world_bible_sections WHERE section=? AND source='w1'", (section,)
        )
        self.conn.execute(
            """INSERT INTO world_bible_sections (section, title, body_full, summary, source, created_at)
               VALUES (?,?,?,?,'w1',0)""",
            (section, title, detail, summary),
        )
        self._commit()

    def list_bible_sections(self, section: str | None = None) -> list[dict[str, Any]]:
        if section is None:
            rows = self.conn.execute(
                "SELECT * FROM world_bible_sections ORDER BY id").fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM world_bible_sections WHERE section=? ORDER BY id", (section,)
            ).fetchall()
        return [
            {"id": r["id"], "section": r["section"], "title": r["title"],
             "body_full": r["body_full"], "summary": (r["summary"] if "summary" in r.keys() else ""),
             "source": r["source"], "created_at": r["created_at"]}
            for r in rows
        ]

    def bible_summaries_text(self, sections: list[str] | None = None) -> str:
        rows = [r for r in self.list_bible_sections() if r["source"] == "w1" and (r["summary"] or "").strip()]
        if sections:
            want = set(sections)
            rows = [r for r in rows if r["section"] in want]
        return "\n".join(f"· {r['title'] or r['section']}：{r['summary'].strip()}" for r in rows)

    def bible_sections_text(self, sections: list[str] | None = None, max_chars: int = 4000) -> str:
        rows = self.list_bible_sections()
        if sections:
            want = set(sections)
            rows = [r for r in rows if r["section"] in want]
        w1_sections = {r["section"] for r in rows if r["source"] in ("w1", "w1_deepened")}
        rows = [r for r in rows
                if r["section"] not in w1_sections or r["source"] in ("w1", "w1_deepened")]
        out, used = [], 0
        for r in rows:
            head = f"【{r['section']}{('·' + r['title']) if r['title'] else ''}】\n{r['body_full']}"
            if used + len(head) > max_chars:
                head = head[: max(0, max_chars - used)]
            out.append(head)
            used += len(head)
            if used >= max_chars:
                break
        return "\n\n".join(out)

    # ---------- entities ----------
    def insert_entity(self, e: Entity) -> None:
        self.conn.execute(
            "INSERT INTO entities (entity_id, type, name, attributes, created_tick) VALUES (?,?,?,?,?)",
            (e.entity_id, e.type, e.name, json.dumps(e.attributes, ensure_ascii=False), e.created_tick),
        )
        self._commit()

    def entity_exists(self, entity_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM entities WHERE entity_id=?", (entity_id,)
        ).fetchone()
        return row is not None

    def get_entity(self, entity_id: str) -> Entity | None:
        for e in self.list_entities():
            if e.entity_id == entity_id:
                return e
        return None

    def update_entity_attributes(self, entity_id: str, attrs: dict[str, Any]) -> None:
        """合并更新实体 attributes。"""
        row = self.conn.execute(
            "SELECT attributes FROM entities WHERE entity_id=?", (entity_id,)
        ).fetchone()
        if row is None:
            return
        cur = json.loads(row["attributes"]) if row["attributes"] else {}
        cur.update(attrs)
        self.conn.execute(
            "UPDATE entities SET attributes=? WHERE entity_id=?",
            (json.dumps(cur, ensure_ascii=False), entity_id),
        )
        self._commit()

    def update_entity_name(self, entity_id: str, name: str) -> None:
        """Rename an entity in place so stable IDs and historical references survive."""
        if not str(name or "").strip():
            return
        self.conn.execute(
            "UPDATE entities SET name=? WHERE entity_id=?",
            (str(name).strip(), entity_id),
        )
        self._commit()

    def list_entities(self) -> list[Entity]:
        rows = self.conn.execute("SELECT * FROM entities").fetchall()
        return [
            Entity(
                entity_id=r["entity_id"],
                type=r["type"],
                name=r["name"],
                attributes=json.loads(r["attributes"]),
                created_tick=r["created_tick"],
            )
            for r in rows
        ]

    def append_fact(self, f: Fact) -> None:
        self.conn.execute(
            """INSERT INTO facts
                 (fact_id, fact_type, canonical_content, structured, story_time,
                  location_id, involved_entities, source_event_id)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                f.fact_id,
                f.fact_type,
                f.canonical_content,
                json.dumps(f.structured, ensure_ascii=False),
                f.story_time,
                f.location_id,
                json.dumps(f.involved_entities, ensure_ascii=False),
                f.source_event_id,
            ),
        )
        self._commit()

    def fact_exists(self, fact_id: str) -> bool:
        row = self.conn.execute("SELECT 1 FROM facts WHERE fact_id=?", (fact_id,)).fetchone()
        return row is not None

    def get_fact(self, fact_id: str) -> Fact | None:
        r = self.conn.execute("SELECT * FROM facts WHERE fact_id=?", (fact_id,)).fetchone()
        return _row_to_fact(r) if r else None

    def get_facts_by_event(self, event_id: str) -> list[Fact]:
        rows = self.conn.execute(
            "SELECT * FROM facts WHERE source_event_id=? ORDER BY story_time", (event_id,)
        ).fetchall()
        return [_row_to_fact(r) for r in rows]

    def list_facts(self) -> list[Fact]:
        rows = self.conn.execute("SELECT * FROM facts ORDER BY story_time").fetchall()
        return [_row_to_fact(r) for r in rows]

    # ---------- events锛坅ppend-only锛沝rama_score 涓哄悗缃爣娉紝瑙?搂1.2/搂4.1锛?----------
    def append_event(self, ev: Event) -> None:
        self.conn.execute(
            """INSERT INTO events
                 (event_id, story_time, actors, action_type, payload,
                  location_id, perceivers, beat_id, story_clock)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                ev.event_id,
                ev.story_time,
                json.dumps(ev.actors, ensure_ascii=False),
                ev.action_type,
                json.dumps(ev.payload, ensure_ascii=False),
                ev.location_id,
                json.dumps(ev.perceivers, ensure_ascii=False),
                ev.beat_id,
                ev.story_clock,
            ),
        )
        self._commit()

    def set_events_story_clock(self, event_ids: list[str], story_clock: int | None) -> None:
        """故事时钟：给本章事件回填绝对钟点（不覆盖 story_time tick）。无钟点/无事件时空操作。"""
        if story_clock is None or not event_ids:
            return
        self.conn.executemany(
            "UPDATE events SET story_clock=? WHERE event_id=?",
            [(int(story_clock), eid) for eid in event_ids if eid],
        )
        self._commit()

    def list_events(self) -> list[Event]:
        rows = self.conn.execute("SELECT * FROM events ORDER BY story_time").fetchall()
        return [_row_to_event(r) for r in rows]

    def get_event(self, event_id: str) -> Event | None:
        r = self.conn.execute("SELECT * FROM events WHERE event_id=?", (event_id,)).fetchone()
        return _row_to_event(r) if r else None

    def set_event_drama_score(self, event_id: str, score: float) -> None:
        """后置标注 drama_score。"""
        self.conn.execute(
            "UPDATE events SET drama_score=? WHERE event_id=?", (score, event_id)
        )
        self._commit()

    def get_event_drama_score(self, event_id: str) -> float | None:
        r = self.conn.execute(
            "SELECT drama_score FROM events WHERE event_id=?", (event_id,)
        ).fetchone()
        return r["drama_score"] if r else None

    def set_event_beat(self, event_id: str, beat_id: str) -> None:
        """给事件打上所属章号。"""
        self.conn.execute("UPDATE events SET beat_id=? WHERE event_id=?", (beat_id, event_id))
        self._commit()

    def count_events_for_beat(self, beat_id: str) -> int:
        r = self.conn.execute(
            "SELECT COUNT(*) AS n FROM events WHERE beat_id=?", (beat_id,)
        ).fetchone()
        return int(r["n"]) if r else 0

    def events_for_beat(self, beat_id: str) -> list[Event]:
        rows = self.conn.execute(
            "SELECT * FROM events WHERE beat_id=? ORDER BY story_time", (beat_id,)
        ).fetchall()
        return [_row_to_event(r) for r in rows]

    # ---------- agent_knowledge锛堣处鏈級 ----------
    def insert_knowledge(self, k: KnowledgeItem) -> None:
        self.conn.execute(
            """INSERT INTO agent_knowledge
                 (agent_id, fact_id, version_content, confidence, learned_tick, source_event_id)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(agent_id, fact_id) DO NOTHING""",
            (
                k.agent_id,
                k.fact_id,
                k.version_content,
                k.confidence,
                k.learned_tick,
                k.source_event_id,
            ),
        )
        self._commit()

    def upsert_knowledge(self, k: KnowledgeItem) -> None:
        """覆盖式写入知识条目。"""
        self.conn.execute(
            """INSERT INTO agent_knowledge
                 (agent_id, fact_id, version_content, confidence, learned_tick, source_event_id)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(agent_id, fact_id) DO UPDATE SET
                 version_content=excluded.version_content,
                 confidence=excluded.confidence,
                 learned_tick=excluded.learned_tick,
                 source_event_id=excluded.source_event_id""",
            (k.agent_id, k.fact_id, k.version_content, k.confidence, k.learned_tick, k.source_event_id),
        )
        self._commit()

    def get_knowledge_entry(self, agent_id: str, fact_id: str) -> KnowledgeItem | None:
        r = self.conn.execute(
            "SELECT * FROM agent_knowledge WHERE agent_id=? AND fact_id=?", (agent_id, fact_id)
        ).fetchone()
        if not r:
            return None
        return KnowledgeItem(
            agent_id=r["agent_id"],
            fact_id=r["fact_id"],
            version_content=r["version_content"],
            confidence=r["confidence"],
            learned_tick=r["learned_tick"],
            source_event_id=r["source_event_id"],
        )

    def delete_knowledge(self, agent_id: str, fact_id: str) -> None:
        self.conn.execute(
            "DELETE FROM agent_knowledge WHERE agent_id=? AND fact_id=?", (agent_id, fact_id)
        )
        self._commit()

    def get_agent_ledger(self, agent_id: str) -> list[KnowledgeItem]:
        """只返回该 agent 自己的知识账本。"""
        rows = self.conn.execute(
            "SELECT * FROM agent_knowledge WHERE agent_id=? ORDER BY learned_tick",
            (agent_id,),
        ).fetchall()
        return [
            KnowledgeItem(
                agent_id=r["agent_id"],
                fact_id=r["fact_id"],
                version_content=r["version_content"],
                confidence=r["confidence"],
                learned_tick=r["learned_tick"],
                source_event_id=r["source_event_id"],
            )
            for r in rows
        ]

    def agent_knows_fact(self, agent_id: str, fact_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM agent_knowledge WHERE agent_id=? AND fact_id=?",
            (agent_id, fact_id),
        ).fetchone()
        return row is not None

    def insert_persona(self, p: Persona) -> None:
        self.conn.execute(
            """INSERT INTO persona
                 (agent_id, name, want, "values", fatal_flaw, obstacles, cost_threshold,
                  voice, mannerisms, motif_objects, arc_state, cost_ledger)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(agent_id) DO UPDATE SET
                 name=excluded.name, want=excluded.want, "values"=excluded."values",
                 fatal_flaw=excluded.fatal_flaw, obstacles=excluded.obstacles,
                 cost_threshold=excluded.cost_threshold, voice=excluded.voice,
                 mannerisms=excluded.mannerisms, motif_objects=excluded.motif_objects,
                 arc_state=excluded.arc_state, cost_ledger=excluded.cost_ledger""",
            (
                p.agent_id,
                p.name,
                p.want,
                json.dumps(p.values, ensure_ascii=False),
                p.fatal_flaw,
                json.dumps(p.obstacles, ensure_ascii=False),
                json.dumps(p.cost_threshold, ensure_ascii=False),
                p.voice,
                json.dumps(p.mannerisms, ensure_ascii=False),
                json.dumps(p.motif_objects, ensure_ascii=False),
                json.dumps(p.arc_state, ensure_ascii=False),
                json.dumps(p.cost_ledger, ensure_ascii=False),
            ),
        )
        self._commit()

    def get_persona(self, agent_id: str) -> Persona | None:
        r = self.conn.execute("SELECT * FROM persona WHERE agent_id=?", (agent_id,)).fetchone()
        if not r:
            return None
        return Persona(
            agent_id=r["agent_id"],
            name=r["name"],
            want=r["want"],
            values=json.loads(r["values"]),
            fatal_flaw=r["fatal_flaw"],
            obstacles=json.loads(r["obstacles"]),
            cost_threshold=json.loads(r["cost_threshold"]),
            voice=r["voice"],
            mannerisms=json.loads(r["mannerisms"]),
            motif_objects=json.loads(r["motif_objects"]),
            arc_state=json.loads(r["arc_state"]),
            cost_ledger=json.loads(r["cost_ledger"]),
        )

    def list_personas(self) -> list[Persona]:
        rows = self.conn.execute("SELECT agent_id FROM persona ORDER BY rowid").fetchall()
        return [self.get_persona(r["agent_id"]) for r in rows]  # type: ignore[misc]

    def update_arc_state(self, agent_id: str, arc_state: dict[str, Any]) -> None:
        self.conn.execute(
            "UPDATE persona SET arc_state=? WHERE agent_id=?",
            (json.dumps(arc_state, ensure_ascii=False), agent_id),
        )
        self._commit()

    def append_cost(self, agent_id: str, cost: str) -> None:
        p = self.get_persona(agent_id)
        if not p:
            return
        p.cost_ledger.append(cost)
        self.conn.execute(
            "UPDATE persona SET cost_ledger=? WHERE agent_id=?",
            (json.dumps(p.cost_ledger, ensure_ascii=False), agent_id),
        )
        self._commit()

    # ---------- threads ----------
    def insert_thread(self, t: Thread) -> None:
        self.conn.execute(
            """INSERT INTO threads
                 (thread_id, central_question, involved_agents, priority_weight,
                  current_tension, last_advanced_tick, status)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(thread_id) DO UPDATE SET
                 central_question=excluded.central_question,
                 involved_agents=excluded.involved_agents,
                 priority_weight=excluded.priority_weight,
                 current_tension=excluded.current_tension,
                 last_advanced_tick=excluded.last_advanced_tick,
                 status=excluded.status""",
            (
                t.thread_id,
                t.central_question,
                json.dumps(t.involved_agents, ensure_ascii=False),
                t.priority_weight,
                t.current_tension,
                t.last_advanced_tick,
                t.status,
            ),
        )
        self._commit()

    def list_threads(self) -> list[Thread]:
        rows = self.conn.execute("SELECT * FROM threads").fetchall()
        return [
            Thread(
                thread_id=r["thread_id"],
                central_question=r["central_question"],
                involved_agents=json.loads(r["involved_agents"]),
                priority_weight=r["priority_weight"],
                current_tension=r["current_tension"],
                last_advanced_tick=r["last_advanced_tick"],
                status=r["status"],
            )
            for r in rows
        ]

    def update_thread_tension(self, thread_id: str, tension: float, tick: int) -> None:
        self.conn.execute(
            "UPDATE threads SET current_tension=?, last_advanced_tick=? WHERE thread_id=?",
            (tension, tick, thread_id),
        )
        self._commit()

    # ---------- beats锛堣妭鎷嶏紝搂1.5锛?----------
    def upsert_beat(self, b: "Beat") -> None:
        self.conn.execute(
            """INSERT INTO beats
                 (beat_id, sequence_order, type, goal, threads, target_tension,
                  target_ending_link, status)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(beat_id) DO UPDATE SET
                 sequence_order=excluded.sequence_order, type=excluded.type,
                 goal=excluded.goal, threads=excluded.threads,
                 target_tension=excluded.target_tension,
                 target_ending_link=excluded.target_ending_link, status=excluded.status""",
            (
                b.beat_id,
                b.sequence_order,
                b.type,
                b.goal,
                json.dumps(b.threads, ensure_ascii=False),
                b.target_tension,
                b.target_ending_link,
                b.status,
            ),
        )
        self._commit()

    def list_beats(self) -> list["Beat"]:
        rows = self.conn.execute("SELECT * FROM beats ORDER BY sequence_order").fetchall()
        return [
            Beat(
                beat_id=r["beat_id"],
                sequence_order=r["sequence_order"],
                type=r["type"],
                goal=r["goal"],
                threads=json.loads(r["threads"]),
                target_tension=r["target_tension"],
                target_ending_link=r["target_ending_link"],
                status=r["status"],
            )
            for r in rows
        ]

    # ---------- 璺ㄨ处鏈煡璇紙鍙欎簨钀藉樊锛?----------
    def holders_of_fact(self, fact_id: str) -> list[KnowledgeItem]:
        rows = self.conn.execute(
            "SELECT * FROM agent_knowledge WHERE fact_id=?", (fact_id,)
        ).fetchall()
        return [
            KnowledgeItem(
                agent_id=r["agent_id"],
                fact_id=r["fact_id"],
                version_content=r["version_content"],
                confidence=r["confidence"],
                learned_tick=r["learned_tick"],
                source_event_id=r["source_event_id"],
            )
            for r in rows
        ]

    def find_conflict_pairs(self) -> list[dict[str, Any]]:
        """找出对同一事实持不同版本的角色对。"""
        rows = self.conn.execute("SELECT DISTINCT fact_id FROM agent_knowledge").fetchall()
        conflicts: list[dict[str, Any]] = []
        for r in rows:
            fid = r["fact_id"]
            holders = self.holders_of_fact(fid)
            versions = {h.version_content for h in holders}
            if len(versions) > 1:
                conflicts.append(
                    {
                        "fact_id": fid,
                        "holders": [
                            {"agent_id": h.agent_id, "version": h.version_content}
                            for h in holders
                        ],
                    }
                )
        return conflicts

    def reveal_to_reader(self, rk: ReaderKnowledge) -> None:
        self.conn.execute(
            """INSERT INTO reader_knowledge
                 (fact_id, revealed_version, revealed_discourse_pos, via_pov)
               VALUES (?,?,?,?)
               ON CONFLICT(fact_id) DO NOTHING""",
            (rk.fact_id, rk.revealed_version, rk.revealed_discourse_pos, rk.via_pov),
        )
        self._commit()

    def reader_knows(self, fact_id: str) -> bool:
        r = self.conn.execute(
            "SELECT 1 FROM reader_knowledge WHERE fact_id=?", (fact_id,)
        ).fetchone()
        return r is not None

    def list_reader_knowledge(self) -> list[ReaderKnowledge]:
        rows = self.conn.execute(
            "SELECT * FROM reader_knowledge ORDER BY revealed_discourse_pos"
        ).fetchall()
        return [
            ReaderKnowledge(
                fact_id=r["fact_id"],
                revealed_version=r["revealed_version"],
                revealed_discourse_pos=r["revealed_discourse_pos"],
                via_pov=r["via_pov"],
            )
            for r in rows
        ]

    def mystery_set(self, candidate_fact_ids: list[str] | None = None) -> list[str]:
        """读者还不知道的真相。"""
        candidates = candidate_fact_ids or [f.fact_id for f in self.list_facts()]
        return [fid for fid in candidates if not self.reader_knows(fid)]

    def irony_set(self, pov: str) -> list[str]:
        """读者已知但 POV 角色不知道的事实。"""
        known_by_pov = {k.fact_id for k in self.get_agent_ledger(pov)}
        return [rk.fact_id for rk in self.list_reader_knowledge() if rk.fact_id not in known_by_pov]

    # ---------- scenes锛堝彊杩颁骇鐗╋紝搂1.6锛?----------
    def insert_scene(self, s: Scene) -> None:
        self.conn.execute(
            """INSERT INTO scenes
                 (scene_id, discourse_order, source_events, pov, target_tension,
                  prose_text, newly_revealed)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(scene_id) DO UPDATE SET
                 discourse_order=excluded.discourse_order,
                 source_events=excluded.source_events, pov=excluded.pov,
                 target_tension=excluded.target_tension,
                 prose_text=excluded.prose_text, newly_revealed=excluded.newly_revealed""",
            (
                s.scene_id,
                s.discourse_order,
                json.dumps(s.source_events, ensure_ascii=False),
                s.pov,
                s.target_tension,
                s.prose_text,
                json.dumps(s.newly_revealed, ensure_ascii=False),
            ),
        )
        self._commit()

    def update_scene_prose(self, scene_id: str, prose: str) -> None:
        """审计重渲：只更新某场正文。"""
        self.conn.execute("UPDATE scenes SET prose_text=? WHERE scene_id=?", (prose, scene_id))
        self._commit()

    def list_scenes(self) -> list[Scene]:
        rows = self.conn.execute("SELECT * FROM scenes ORDER BY discourse_order").fetchall()
        return [
            Scene(
                scene_id=r["scene_id"],
                discourse_order=r["discourse_order"],
                source_events=json.loads(r["source_events"]),
                pov=r["pov"],
                target_tension=r["target_tension"],
                prose_text=r["prose_text"],
                newly_revealed=json.loads(r["newly_revealed"]),
            )
            for r in rows
        ]

    def upsert_foreshadow(self, fs: Foreshadow) -> None:
        self.conn.execute(
            """INSERT INTO foreshadows
                 (foreshadow_id, question, linked_fact_id, planted_discourse_pos,
                  must_resolve, target_payoff_beat, status, payoff_discourse_pos)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(foreshadow_id) DO UPDATE SET
                 question=excluded.question, linked_fact_id=excluded.linked_fact_id,
                 planted_discourse_pos=excluded.planted_discourse_pos,
                 must_resolve=excluded.must_resolve,
                 target_payoff_beat=excluded.target_payoff_beat,
                 status=excluded.status, payoff_discourse_pos=excluded.payoff_discourse_pos""",
            (
                fs.foreshadow_id,
                fs.question,
                fs.linked_fact_id,
                fs.planted_discourse_pos,
                1 if fs.must_resolve else 0,
                fs.target_payoff_beat,
                fs.status,
                fs.payoff_discourse_pos,
            ),
        )
        self._commit()

    def list_foreshadows(self) -> list[Foreshadow]:
        rows = self.conn.execute(
            "SELECT * FROM foreshadows ORDER BY planted_discourse_pos"
        ).fetchall()
        return [_row_to_foreshadow(r) for r in rows]

    def get_foreshadow(self, foreshadow_id: str) -> Foreshadow | None:
        r = self.conn.execute(
            "SELECT * FROM foreshadows WHERE foreshadow_id=?", (foreshadow_id,)
        ).fetchone()
        return _row_to_foreshadow(r) if r else None

    def foreshadows_for_fact(self, fact_id: str) -> list[Foreshadow]:
        rows = self.conn.execute(
            "SELECT * FROM foreshadows WHERE linked_fact_id=?", (fact_id,)
        ).fetchall()
        return [_row_to_foreshadow(r) for r in rows]

    # ---------- endings锛堝€欓€夌粨灞€锛屄?.1锛?----------
    def upsert_ending(self, e: Ending) -> None:
        self.conn.execute(
            """INSERT INTO endings
                 (ending_id, summary, theme_expression, required_conditions, active_weight, status)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(ending_id) DO UPDATE SET
                 summary=excluded.summary, theme_expression=excluded.theme_expression,
                 required_conditions=excluded.required_conditions,
                 active_weight=excluded.active_weight, status=excluded.status""",
            (
                e.ending_id,
                e.summary,
                e.theme_expression,
                json.dumps(e.required_conditions, ensure_ascii=False),
                e.active_weight,
                e.status,
            ),
        )
        self._commit()

    def list_endings(self) -> list[Ending]:
        rows = self.conn.execute(
            "SELECT * FROM endings ORDER BY active_weight DESC"
        ).fetchall()
        return [
            Ending(
                ending_id=r["ending_id"],
                summary=r["summary"],
                theme_expression=r["theme_expression"],
                required_conditions=json.loads(r["required_conditions"]),
                active_weight=r["active_weight"],
                status=r["status"],
            )
            for r in rows
        ]


    # ========== 瑙勫垝灞傦紙澶х翰椹卞姩锛涗粎鏂板缓椤圭洰鍐欏叆锛屾棫椤圭洰鐣欑┖锛?==========

    # ---------- parts ----------
    def upsert_part(self, p: Part) -> None:
        self.conn.execute(
            """INSERT INTO parts
                 (part_id, sequence_order, title, goal, region, key_twist,
                  new_crisis_hook, reveal_node_ids, status)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(part_id) DO UPDATE SET
                 sequence_order=excluded.sequence_order, title=excluded.title,
                 goal=excluded.goal, region=excluded.region,
                 key_twist=excluded.key_twist, new_crisis_hook=excluded.new_crisis_hook,
                 reveal_node_ids=excluded.reveal_node_ids, status=excluded.status""",
            (
                p.part_id,
                p.sequence_order,
                p.title,
                p.goal,
                p.region,
                p.key_twist,
                p.new_crisis_hook,
                json.dumps(p.reveal_node_ids, ensure_ascii=False),
                p.status,
            ),
        )
        self._commit()

    def list_parts(self) -> list[Part]:
        rows = self.conn.execute("SELECT * FROM parts ORDER BY sequence_order").fetchall()
        return [_row_to_part(r) for r in rows]

    def get_part(self, part_id: str) -> Part | None:
        r = self.conn.execute("SELECT * FROM parts WHERE part_id=?", (part_id,)).fetchone()
        return _row_to_part(r) if r else None

    def set_part_status(self, part_id: str, status: str) -> None:
        self.conn.execute("UPDATE parts SET status=? WHERE part_id=?", (status, part_id))
        self._commit()

    # ---------- arcs ----------
    def upsert_arc(self, a: Arc) -> None:
        self.conn.execute(
            """INSERT INTO arcs
                 (arc_id, part_id, sequence_order, title, summary, target_chapters,
                  focus_agents, status)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(arc_id) DO UPDATE SET
                 part_id=excluded.part_id, sequence_order=excluded.sequence_order,
                 title=excluded.title, summary=excluded.summary,
                 target_chapters=excluded.target_chapters,
                 focus_agents=excluded.focus_agents, status=excluded.status""",
            (
                a.arc_id,
                a.part_id,
                a.sequence_order,
                a.title,
                a.summary,
                a.target_chapters,
                json.dumps(a.focus_agents, ensure_ascii=False),
                a.status,
            ),
        )
        self._commit()

    def list_arcs(self, part_id: str | None = None) -> list[Arc]:
        if part_id is None:
            rows = self.conn.execute("SELECT * FROM arcs ORDER BY sequence_order").fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM arcs WHERE part_id=? ORDER BY sequence_order", (part_id,)
            ).fetchall()
        return [_row_to_arc(r) for r in rows]

    def get_arc(self, arc_id: str) -> Arc | None:
        r = self.conn.execute("SELECT * FROM arcs WHERE arc_id=?", (arc_id,)).fetchone()
        return _row_to_arc(r) if r else None

    def set_arc_status(self, arc_id: str, status: str) -> None:
        self.conn.execute("UPDATE arcs SET status=? WHERE arc_id=?", (status, arc_id))
        self._commit()

    # ---------- chapter_plans ----------
    def upsert_chapter_plan(self, c: ChapterPlan) -> None:
        self.conn.execute(
            """INSERT INTO chapter_plans
                 (chapter_id, arc_id, sequence_order, title, cast, location_ids,
                   available_items, items_present, items_introduced, items_consumed,
                   beat_goals, reveal_gate, must_happen, required_exit_state, scene_flow,
                   allowed_entity_ids, allowed_fact_ids, forbidden, item_sources,
                   package_version, thread_decisions_json, knowledge_delta,
                   summary, scene_ids, target_scenes, role, target_tension,
                   dramatic_question, resolution_predicate, min_scenes, target_words,
                   ending_hook, hook_type, pov_agent, exit_state, audited, conflict_type,
                   beat_povs, time_hint, status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(chapter_id) DO UPDATE SET
                 arc_id=excluded.arc_id, sequence_order=excluded.sequence_order,
                 title=excluded.title, cast=excluded.cast,
                 location_ids=excluded.location_ids, available_items=excluded.available_items,
                 items_present=excluded.items_present, items_introduced=excluded.items_introduced,
                 items_consumed=excluded.items_consumed,
                 beat_goals=excluded.beat_goals, reveal_gate=excluded.reveal_gate,
                 must_happen=excluded.must_happen,
                 required_exit_state=excluded.required_exit_state,
                 scene_flow=excluded.scene_flow,
                 allowed_entity_ids=excluded.allowed_entity_ids,
                 allowed_fact_ids=excluded.allowed_fact_ids,
                 forbidden=excluded.forbidden,
                 item_sources=excluded.item_sources,
                 package_version=excluded.package_version,
                 thread_decisions_json=excluded.thread_decisions_json,
                 knowledge_delta=excluded.knowledge_delta, summary=excluded.summary,
                 scene_ids=excluded.scene_ids, target_scenes=excluded.target_scenes,
                 role=excluded.role, target_tension=excluded.target_tension,
                 dramatic_question=excluded.dramatic_question,
                 resolution_predicate=excluded.resolution_predicate,
                 min_scenes=excluded.min_scenes, target_words=excluded.target_words,
                 ending_hook=excluded.ending_hook, hook_type=excluded.hook_type,
                 pov_agent=excluded.pov_agent, exit_state=excluded.exit_state,
                 audited=excluded.audited, conflict_type=excluded.conflict_type,
                 beat_povs=excluded.beat_povs, time_hint=excluded.time_hint,
                 status=excluded.status""",
            (
                c.chapter_id,
                c.arc_id,
                c.sequence_order,
                c.title,
                json.dumps(c.cast, ensure_ascii=False),
                json.dumps(c.location_ids, ensure_ascii=False),
                json.dumps(c.available_items, ensure_ascii=False),
                json.dumps(c.items_present, ensure_ascii=False),
                json.dumps(c.items_introduced, ensure_ascii=False),
                json.dumps(c.items_consumed, ensure_ascii=False),
                json.dumps(c.beat_goals, ensure_ascii=False),
                json.dumps(c.reveal_gate, ensure_ascii=False),
                json.dumps(c.must_happen, ensure_ascii=False),
                c.required_exit_state,
                json.dumps(c.scene_flow, ensure_ascii=False),
                json.dumps(c.allowed_entity_ids, ensure_ascii=False),
                json.dumps(c.allowed_fact_ids, ensure_ascii=False),
                json.dumps(c.forbidden, ensure_ascii=False),
                json.dumps(c.item_sources, ensure_ascii=False),
                int(c.package_version or 1),
                json.dumps(c.thread_decisions_json, ensure_ascii=False),
                json.dumps(c.knowledge_delta, ensure_ascii=False),
                c.summary,
                json.dumps(c.scene_ids, ensure_ascii=False),
                c.target_scenes,
                c.role,
                c.target_tension,
                c.dramatic_question,
                c.resolution_predicate,
                c.min_scenes,
                c.target_words,
                c.ending_hook,
                c.hook_type,
                c.pov_agent,
                c.exit_state,
                int(c.audited or 0),
                c.conflict_type,
                json.dumps(c.beat_povs, ensure_ascii=False),
                c.time_hint,
                c.status,
            ),
        )
        self._commit()

    def list_chapter_plans(self, arc_id: str | None = None) -> list[ChapterPlan]:
        if arc_id is None:
            rows = self.conn.execute(
                "SELECT * FROM chapter_plans ORDER BY sequence_order"
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM chapter_plans WHERE arc_id=? ORDER BY sequence_order", (arc_id,)
            ).fetchall()
        return [_row_to_chapter_plan(r) for r in rows]

    def get_chapter_plan(self, chapter_id: str) -> ChapterPlan | None:
        r = self.conn.execute(
            "SELECT * FROM chapter_plans WHERE chapter_id=?", (chapter_id,)
        ).fetchone()
        return _row_to_chapter_plan(r) if r else None

    def delete_chapter_cascade(self, chapter_id: str) -> dict:
        """删除一章及其已写正文（场/事件）→ 删 scenes → 删 events → 删 chapter_plan，返回删除计数。"""
        ev_ids = {e.event_id for e in self.events_for_beat(chapter_id)}
        n_sc = 0
        if ev_ids:
            for s in self.list_scenes():
                if any(eid in ev_ids for eid in s.source_events):
                    self.conn.execute("DELETE FROM scenes WHERE scene_id=?", (s.scene_id,))
                    n_sc += 1
        self.conn.execute("DELETE FROM events WHERE beat_id=?", (chapter_id,))
        self.conn.execute("DELETE FROM chapter_plans WHERE chapter_id=?", (chapter_id,))
        self._commit()
        return {"scenes": n_sc, "events": len(ev_ids)}

    def chapter_is_written(self, chapter_id: str) -> bool:
        """本章是否已写（有正文场，或已收束/已审）——用于"已写完的不能改"闸门。"""
        ch = self.get_chapter_plan(chapter_id)
        if ch is None:
            return False
        if ch.status == "done" or int(ch.audited or 0):
            return True
        ev_ids = {e.event_id for e in self.events_for_beat(chapter_id)}
        if not ev_ids:
            return False
        return any(any(eid in ev_ids for eid in s.source_events) for s in self.list_scenes())

    def active_chapter_plan(self) -> ChapterPlan | None:
        """当前 active 章；无 active 时回落最早 planned。"""
        r = self.conn.execute(
            "SELECT * FROM chapter_plans WHERE status='active' ORDER BY sequence_order LIMIT 1"
        ).fetchone()
        if r:
            return _row_to_chapter_plan(r)
        r = self.conn.execute(
            "SELECT * FROM chapter_plans WHERE status='planned' ORDER BY sequence_order LIMIT 1"
        ).fetchone()
        return _row_to_chapter_plan(r) if r else None

    def set_inventory(self, item: InventoryItem) -> None:
        self.conn.execute(
            """INSERT INTO inventory
                 (object_id, holder_agent_id, status, acquired_chapter, note)
               VALUES (?,?,?,?,?)
               ON CONFLICT(object_id) DO UPDATE SET
                 holder_agent_id=excluded.holder_agent_id, status=excluded.status,
                 acquired_chapter=excluded.acquired_chapter, note=excluded.note""",
            (item.object_id, item.holder_agent_id, item.status, item.acquired_chapter, item.note),
        )
        self._commit()

    def get_inventory_item(self, object_id: str) -> InventoryItem | None:
        r = self.conn.execute(
            "SELECT * FROM inventory WHERE object_id=?", (object_id,)
        ).fetchone()
        return _row_to_inventory(r) if r else None

    def items_held_by(self, agent_id: str) -> list[InventoryItem]:
        rows = self.conn.execute(
            "SELECT * FROM inventory WHERE holder_agent_id=? AND status='held'", (agent_id,)
        ).fetchall()
        return [_row_to_inventory(r) for r in rows]

    def list_inventory(self) -> list[InventoryItem]:
        rows = self.conn.execute("SELECT * FROM inventory").fetchall()
        return [_row_to_inventory(r) for r in rows]

    def transfer_item(self, object_id: str, to_agent: str | None, chapter: int,
                      note: str = "", status: str = "") -> None:
        """转移物品：to_agent=None 表示丢失/消失。status 可指定 lost/consumed/destroyed/sacrificed。"""
        _DESTROY = ("consumed", "destroyed", "sacrificed")
        if status in _DESTROY:
            self.set_inventory(InventoryItem(object_id, None, status, chapter, note))
        elif to_agent is None:
            self.set_inventory(InventoryItem(object_id, None, status or "lost", chapter, note))
        else:
            self.set_inventory(InventoryItem(object_id, to_agent, "held", chapter, note))

    def item_exists(self, object_id: str) -> bool:
        """物品是否仍然存在（未被消耗/销毁/献祭）。未入库存的物品按存在处理。"""
        r = self.conn.execute(
            "SELECT status FROM inventory WHERE object_id=?", (object_id,)
        ).fetchone()
        if r is None:
            return True
        return r["status"] not in ("consumed", "destroyed", "sacrificed")

    def agent_holds(self, agent_id: str, object_id: str) -> bool:
        r = self.conn.execute(
            "SELECT 1 FROM inventory WHERE object_id=? AND holder_agent_id=? AND status='held'",
            (object_id, agent_id),
        ).fetchone()
        return r is not None

    # ---------- character chapter logs ----------
    def insert_character_log(self, log: CharacterChapterLog) -> None:
        """Docstring omitted."""
        row = self.conn.execute(
            "SELECT * FROM character_chapter_logs WHERE agent_id=? AND chapter_seq=?",
            (log.agent_id, log.chapter_seq),
        ).fetchone()
        if row:
            old_items = json.loads(row["items_changed"] or "[]")
            merged_items = list(dict.fromkeys(old_items + list(log.items_changed or [])))
            actions = _merge_text(row["actions"], log.actions)
            psychology = _merge_text(row["psychology"], log.psychology)
            intention = (log.intention or row["intention"] or "").strip()
            self.conn.execute(
                """UPDATE character_chapter_logs
                   SET actions=?, psychology=?, intention=?, items_changed=?
                   WHERE agent_id=? AND chapter_seq=?""",
                (
                    actions,
                    psychology,
                    intention,
                    json.dumps(merged_items, ensure_ascii=False),
                    log.agent_id,
                    log.chapter_seq,
                ),
            )
        else:
            self.conn.execute(
                """INSERT INTO character_chapter_logs
                   (agent_id, chapter_seq, actions, psychology, intention, items_changed)
                   VALUES (?,?,?,?,?,?)""",
                (
                    log.agent_id,
                    int(log.chapter_seq or 0),
                    log.actions,
                    log.psychology,
                    log.intention,
                    json.dumps(log.items_changed or [], ensure_ascii=False),
                ),
            )
        self._commit()

    def get_character_logs(
        self, agent_id: str, last_n: int = 5, before_chapter: int | None = None
    ) -> list[CharacterChapterLog]:
        sql = "SELECT * FROM character_chapter_logs WHERE agent_id=?"
        args: list[Any] = [agent_id]
        if before_chapter is not None:
            sql += " AND chapter_seq < ?"
            args.append(before_chapter)
        sql += " ORDER BY chapter_seq DESC LIMIT ?"
        args.append(max(1, int(last_n or 5)))
        rows = self.conn.execute(sql, args).fetchall()
        return list(reversed([_row_to_character_log(r) for r in rows]))

    def get_logs_for_chapter(self, chapter_seq: int) -> list[CharacterChapterLog]:
        rows = self.conn.execute(
            "SELECT * FROM character_chapter_logs WHERE chapter_seq=? ORDER BY agent_id",
            (chapter_seq,),
        ).fetchall()
        return [_row_to_character_log(r) for r in rows]

    # ---------- batch audits ----------
    def upsert_batch_audit(self, audit: BatchAudit) -> None:
        self.conn.execute(
            """INSERT INTO batch_audits (chapter_seq, result_json, summary_json, created_tick)
               VALUES (?,?,?,?)
               ON CONFLICT(chapter_seq) DO UPDATE SET
                 result_json=excluded.result_json,
                 summary_json=excluded.summary_json,
                 created_tick=excluded.created_tick""",
            (
                audit.chapter_seq,
                json.dumps(audit.result_json or {}, ensure_ascii=False),
                json.dumps(audit.summary_json or {}, ensure_ascii=False),
                int(audit.created_tick or 0),
            ),
        )
        self._commit()

    def latest_batch_audit(self, before_chapter: int | None = None) -> BatchAudit | None:
        sql = "SELECT * FROM batch_audits"
        args: list[Any] = []
        if before_chapter is not None:
            sql += " WHERE chapter_seq < ?"
            args.append(before_chapter)
        sql += " ORDER BY chapter_seq DESC LIMIT 1"
        row = self.conn.execute(sql, args).fetchone()
        return _row_to_batch_audit(row) if row else None

    # ---------- reveal_chain锛堟帰绱㈤┍鍔ㄦ彮绀洪摼锛?----------
    def upsert_reveal_node(self, n: RevealNode) -> None:
        self.conn.execute(
            """INSERT INTO reveal_chain
                 (node_id, fact_id, kind, sequence_order, prereq_node_ids, part_id,
                  description, discovered, discovered_chapter)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(node_id) DO UPDATE SET
                 fact_id=excluded.fact_id, kind=excluded.kind,
                 sequence_order=excluded.sequence_order,
                 prereq_node_ids=excluded.prereq_node_ids, part_id=excluded.part_id,
                 description=excluded.description, discovered=excluded.discovered,
                 discovered_chapter=excluded.discovered_chapter""",
            (
                n.node_id,
                n.fact_id,
                n.kind,
                n.sequence_order,
                json.dumps(n.prereq_node_ids, ensure_ascii=False),
                n.part_id,
                n.description,
                1 if n.discovered else 0,
                n.discovered_chapter,
            ),
        )
        self._commit()

    def list_reveal_nodes(self) -> list[RevealNode]:
        rows = self.conn.execute(
            "SELECT * FROM reveal_chain ORDER BY sequence_order"
        ).fetchall()
        return [_row_to_reveal_node(r) for r in rows]

    def get_reveal_node(self, node_id: str) -> RevealNode | None:
        r = self.conn.execute(
            "SELECT * FROM reveal_chain WHERE node_id=?", (node_id,)
        ).fetchone()
        return _row_to_reveal_node(r) if r else None

    def mark_node_discovered(self, node_id: str, chapter: int) -> None:
        self.conn.execute(
            "UPDATE reveal_chain SET discovered=1, discovered_chapter=? WHERE node_id=?",
            (chapter, node_id),
        )
        self._commit()

    def unlockable_nodes(self) -> list[RevealNode]:
        """前置全部完成、自身尚未发现的揭示节点。"""
        nodes = self.list_reveal_nodes()
        done = {n.node_id for n in nodes if n.discovered}
        return [n for n in nodes if not n.discovered and all(p in done for p in n.prereq_node_ids)]

    def get_tone_profile(self) -> ToneProfile:
        r = self.conn.execute("SELECT * FROM tone_profile WHERE id=1").fetchone()
        if not r:
            return ToneProfile()
        return ToneProfile(
            genre=r["genre"],
            primary_effect=r["primary_effect"],
            register=r["register"],
            sentence_rhythm=r["sentence_rhythm"],
            diction_do=json.loads(r["diction_do"]),
            diction_dont=json.loads(r["diction_dont"]),
            device_kit=json.loads(r["device_kit"]),
            pacing=r["pacing"],
            tension_curve_bias=r["tension_curve_bias"],
            reveal_cadence=r["reveal_cadence"],
            complexity=r["complexity"],
            tone_reference=r["tone_reference"],
            confirmed=bool(r["confirmed"]),
            era_logic=json.loads(r["era_logic"]) if ("era_logic" in r.keys() and r["era_logic"]) else {},
        )

    def set_tone_profile(self, p: ToneProfile) -> None:
        cur = self.conn.execute("SELECT confirmed FROM tone_profile WHERE id=1").fetchone()
        if cur and cur["confirmed"]:
            return
        self.conn.execute(
            """INSERT INTO tone_profile
                 (id, genre, primary_effect, register, sentence_rhythm, diction_do, diction_dont,
                  device_kit, pacing, tension_curve_bias, reveal_cadence, complexity,
                  tone_reference, confirmed, era_logic)
               VALUES (1,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 genre=excluded.genre, primary_effect=excluded.primary_effect,
                 register=excluded.register, sentence_rhythm=excluded.sentence_rhythm,
                 diction_do=excluded.diction_do, diction_dont=excluded.diction_dont,
                 device_kit=excluded.device_kit, pacing=excluded.pacing,
                 tension_curve_bias=excluded.tension_curve_bias,
                 reveal_cadence=excluded.reveal_cadence, complexity=excluded.complexity,
                 tone_reference=excluded.tone_reference, confirmed=excluded.confirmed,
                 era_logic=excluded.era_logic""",
            (
                p.genre,
                p.primary_effect,
                p.register,
                p.sentence_rhythm,
                json.dumps(p.diction_do, ensure_ascii=False),
                json.dumps(p.diction_dont, ensure_ascii=False),
                json.dumps(p.device_kit, ensure_ascii=False),
                p.pacing,
                p.tension_curve_bias,
                p.reveal_cadence,
                p.complexity,
                p.tone_reference,
                1 if p.confirmed else 0,
                json.dumps(p.era_logic or {}, ensure_ascii=False),
            ),
        )
        self._commit()

    def confirm_tone_profile(self) -> None:
        self.conn.execute("UPDATE tone_profile SET confirmed=1 WHERE id=1")
        self._commit()

    def get_style_skill(self) -> StyleProfile:
        r = self.conn.execute("SELECT * FROM style_skill WHERE id=1").fetchone()
        if not r:
            return StyleProfile()
        return StyleProfile(
            name=r["name"],
            source=r["source"],
            register=r["register"],
            rhythm=r["rhythm"],
            devices=json.loads(r["devices"]),
            diction_do=json.loads(r["diction_do"]),
            diction_dont=json.loads(r["diction_dont"]),
            motifs=json.loads(r["motifs"]),
            samples=json.loads(r["samples"]),
            metrics=json.loads(r["metrics"]),
            persona_md=r["persona_md"] if "persona_md" in r.keys() else "",
            enabled=bool(r["enabled"]),
        )

    def set_style_skill(self, p: StyleProfile) -> None:
        self.conn.execute(
            """INSERT INTO style_skill
                 (id, name, source, register, rhythm, devices, diction_do, diction_dont,
                  motifs, samples, metrics, persona_md, enabled)
               VALUES (1,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 name=excluded.name, source=excluded.source, register=excluded.register,
                 rhythm=excluded.rhythm, devices=excluded.devices, diction_do=excluded.diction_do,
                 diction_dont=excluded.diction_dont, motifs=excluded.motifs,
                 samples=excluded.samples, metrics=excluded.metrics,
                 persona_md=excluded.persona_md, enabled=excluded.enabled""",
            (
                p.name,
                p.source,
                p.register,
                p.rhythm,
                json.dumps(p.devices, ensure_ascii=False),
                json.dumps(p.diction_do, ensure_ascii=False),
                json.dumps(p.diction_dont, ensure_ascii=False),
                json.dumps(p.motifs, ensure_ascii=False),
                json.dumps(p.samples, ensure_ascii=False),
                json.dumps(p.metrics, ensure_ascii=False),
                p.persona_md or "",
                1 if p.enabled else 0,
            ),
        )
        self._commit()

    def set_style_skill_enabled(self, enabled: bool) -> None:
        self.conn.execute("UPDATE style_skill SET enabled=? WHERE id=1", (1 if enabled else 0,))
        self._commit()

    def delete_style_skill(self) -> None:
        self.conn.execute("DELETE FROM style_skill WHERE id=1")
        self._commit()

    # ---------- S1 Author Writing Sheet ----------
    def save_author_sheet(self, sheet: AuthorWritingSheet) -> int:
        import time
        cur = self.conn.execute(
            """INSERT INTO author_sheets
                 (name, source_genre, plot_json, creativity_json, development_json,
                  language_json, persona_md, n_segments, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (sheet.name, sheet.source_genre,
             json.dumps([{"claim": c.claim, "evidence": c.evidence, "source_chapter": c.source_chapter}
                         for c in sheet.plot], ensure_ascii=False),
             json.dumps([{"claim": c.claim, "evidence": c.evidence, "source_chapter": c.source_chapter}
                         for c in sheet.creativity], ensure_ascii=False),
             json.dumps([{"claim": c.claim, "evidence": c.evidence, "source_chapter": c.source_chapter}
                         for c in sheet.development], ensure_ascii=False),
             json.dumps([{"claim": c.claim, "evidence": c.evidence, "source_chapter": c.source_chapter}
                         for c in sheet.language], ensure_ascii=False),
             sheet.persona_md, sheet.n_segments, int(time.time())),
        )
        sheet_id = cur.lastrowid
        for dim, claims in [("plot", sheet.plot), ("creativity", sheet.creativity),
                            ("development", sheet.development), ("language", sheet.language)]:
            for idx, c in enumerate(claims):
                if c.evidence:
                    self.conn.execute(
                        "INSERT INTO style_evidence (sheet_id, dimension, claim_idx, excerpt, source_chapter) VALUES (?,?,?,?,?)",
                        (sheet_id, dim, idx, c.evidence, c.source_chapter))
        self._commit()
        return sheet_id  # type: ignore[return-value]

    def get_author_sheet(self, sheet_id: int) -> AuthorWritingSheet | None:
        r = self.conn.execute("SELECT * FROM author_sheets WHERE id=?", (sheet_id,)).fetchone()
        if not r:
            return None

        def _parse(data) -> list[StyleClaim]:
            items = json.loads(data) if isinstance(data, str) else data
            return [StyleClaim(claim=x.get("claim", ""), evidence=x.get("evidence", ""),
                               source_chapter=x.get("source_chapter", "")) for x in items]
        return AuthorWritingSheet(
            name=r["name"], source_genre=r["source_genre"],
            plot=_parse(r["plot_json"]), creativity=_parse(r["creativity_json"]),
            development=_parse(r["development_json"]), language=_parse(r["language_json"]),
            persona_md=r["persona_md"], n_segments=r["n_segments"])

    def list_author_sheets(self) -> list[dict]:
        rows = self.conn.execute("SELECT id, name, source_genre, n_segments, created_at FROM author_sheets ORDER BY created_at DESC").fetchall()
        return [{"id": r["id"], "name": r["name"], "sourceGenre": r["source_genre"],
                 "nSegments": r["n_segments"], "createdAt": r["created_at"]} for r in rows]

    def delete_author_sheet(self, sheet_id: int) -> None:
        self.conn.execute("DELETE FROM style_evidence WHERE sheet_id=?", (sheet_id,))
        self.conn.execute("DELETE FROM author_sheets WHERE id=?", (sheet_id,))
        self._commit()

    def get_active_author_sheet(self) -> AuthorWritingSheet | None:
        settings = self.get_writing_settings()
        if settings.style_profile_id:
            sheet = self.get_author_sheet(int(settings.style_profile_id))
            if sheet:
                return sheet
        record = self.get_story_bible_record()
        if record and record.style_profile_id:
            sheet = self.get_author_sheet(int(record.style_profile_id))
            if sheet:
                return sheet
        row = self.conn.execute(
            "SELECT id FROM author_sheets ORDER BY created_at DESC, id DESC LIMIT 1"
        ).fetchone()
        return self.get_author_sheet(int(row["id"])) if row else None

    def style_skill_prompt(self) -> str:
        p = self.get_style_skill()
        if not p.is_set():
            return ""
        title = f"「{p.name}（{p.source}）」" if p.name else "上传的文风"
        lines = [f"[文风模拟] 仿{title}的文风写作："]
        if p.register or p.rhythm:
            lines.append(f"  语域：{p.register or '—'}；句法节奏：{p.rhythm or '—'}。")
        if p.devices:
            lines.append("  多用手法：" + "、".join(p.devices[:8]) + "。")
        if p.diction_do or p.diction_dont:
            lines.append(
                f"  偏好词：{('、'.join(p.diction_do[:8]) or '—')}；"
                f"禁忌词：{('、'.join(p.diction_dont[:8]) or '—')}。"
            )
        if p.motifs:
            lines.append("  可呼应的母题意象：" + "、".join(p.motifs[:8]) + "。")
        if p.samples:
            lines.append("[风格样例·仅供模仿腔调，严禁照搬其人物/地点/情节/具体意象]")
            for i, s in enumerate(p.samples[:2], 1):
                lines.append(f"  {'①②'[i - 1]} {str(s)[:150]}")
        lines.append(
            "【硬约束】只学其腔调、句式、节奏、用词、标点、意象密度；"
            "样例里的任何具体内容都不得出现在你的正文里。"
        )
        return "\n".join(lines)

    def insert_style_segment(self, seg: StyleSegment) -> None:
        self.conn.execute(
            """INSERT INTO style_segments
                 (id, project_id, source_chapter_id, start_offset, end_offset, text,
                  voice_type, character_id, pov_character_id, discourse_type, scene_type,
                  emotion_json, register_type, feature_json, embedding_key,
                  quality_score, annotation_confidence, enabled)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 project_id=excluded.project_id,
                 source_chapter_id=excluded.source_chapter_id,
                 start_offset=excluded.start_offset,
                 end_offset=excluded.end_offset,
                 text=excluded.text,
                 voice_type=excluded.voice_type,
                 character_id=excluded.character_id,
                 pov_character_id=excluded.pov_character_id,
                 discourse_type=excluded.discourse_type,
                 scene_type=excluded.scene_type,
                 emotion_json=excluded.emotion_json,
                 register_type=excluded.register_type,
                 feature_json=excluded.feature_json,
                 embedding_key=excluded.embedding_key,
                 quality_score=excluded.quality_score,
                 annotation_confidence=excluded.annotation_confidence,
                 enabled=excluded.enabled""",
            (
                seg.id,
                seg.project_id,
                seg.source_chapter_id,
                seg.start_offset,
                seg.end_offset,
                seg.text,
                seg.voice_type,
                seg.character_id,
                seg.pov_character_id,
                seg.discourse_type,
                seg.scene_type,
                json.dumps(seg.emotion_json, ensure_ascii=False),
                seg.register_type,
                json.dumps(seg.feature_json, ensure_ascii=False),
                seg.embedding_key,
                seg.quality_score,
                seg.annotation_confidence,
                1 if seg.enabled else 0,
            ),
        )
        self._commit()

    def clear_style_corpus(self) -> None:
        self.conn.execute("DELETE FROM style_negative_samples")
        self.conn.execute("DELETE FROM style_clusters")
        self.conn.execute("DELETE FROM style_segments")
        self._commit()

    def list_style_segments(self, *, discourse_type: str | None = None,
                            enabled_only: bool = True) -> list[StyleSegment]:
        clauses = []
        params: list[Any] = []
        if discourse_type:
            clauses.append("discourse_type=?")
            params.append(discourse_type)
        if enabled_only:
            clauses.append("enabled=1")
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.conn.execute(
            f"SELECT * FROM style_segments{where} ORDER BY source_chapter_id, start_offset, id",
            tuple(params),
        ).fetchall()
        return [_row_to_style_segment(r) for r in rows]

    def get_style_segment(self, segment_id: str) -> StyleSegment | None:
        row = self.conn.execute("SELECT * FROM style_segments WHERE id=?", (segment_id,)).fetchone()
        return _row_to_style_segment(row) if row else None

    def insert_style_cluster(self, cluster: StyleCluster) -> None:
        self.conn.execute(
            """INSERT INTO style_clusters
                 (id, project_id, cluster_type, label, centroid_key,
                  feature_summary_json, representative_segment_ids_json)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 project_id=excluded.project_id,
                 cluster_type=excluded.cluster_type,
                 label=excluded.label,
                 centroid_key=excluded.centroid_key,
                 feature_summary_json=excluded.feature_summary_json,
                 representative_segment_ids_json=excluded.representative_segment_ids_json""",
            (
                cluster.id,
                cluster.project_id,
                cluster.cluster_type,
                cluster.label,
                cluster.centroid_key,
                json.dumps(cluster.feature_summary_json, ensure_ascii=False),
                json.dumps(cluster.representative_segment_ids_json, ensure_ascii=False),
            ),
        )
        self._commit()

    def list_style_clusters(self, cluster_type: str | None = None) -> list[StyleCluster]:
        if cluster_type is None:
            rows = self.conn.execute("SELECT * FROM style_clusters ORDER BY cluster_type, label, id").fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM style_clusters WHERE cluster_type=? ORDER BY label, id",
                (cluster_type,),
            ).fetchall()
        return [_row_to_style_cluster(r) for r in rows]

    def insert_style_negative_sample(self, sample: StyleNegativeSample) -> None:
        self.conn.execute(
            """INSERT INTO style_negative_samples
                 (id, project_id, text, failure_types_json, related_source_segment_ids_json,
                  score_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 project_id=excluded.project_id,
                 text=excluded.text,
                 failure_types_json=excluded.failure_types_json,
                 related_source_segment_ids_json=excluded.related_source_segment_ids_json,
                 score_json=excluded.score_json,
                 created_at=excluded.created_at""",
            (
                sample.id,
                sample.project_id,
                sample.text,
                json.dumps(sample.failure_types_json, ensure_ascii=False),
                json.dumps(sample.related_source_segment_ids_json, ensure_ascii=False),
                json.dumps(sample.score_json, ensure_ascii=False),
                sample.created_at,
            ),
        )
        self._commit()

    def list_style_negative_samples(self, limit: int = 20) -> list[StyleNegativeSample]:
        rows = self.conn.execute(
            "SELECT * FROM style_negative_samples ORDER BY created_at DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [_row_to_style_negative_sample(r) for r in rows]

    def clear_author_experience(self) -> None:
        self.conn.execute("DELETE FROM author_life_models")
        self.conn.execute("DELETE FROM author_experience_fragments")
        self.conn.execute("DELETE FROM author_experience_sources")
        self._commit()

    def insert_author_experience_source(self, source: AuthorExperienceSource) -> None:
        self.conn.execute(
            """INSERT INTO author_experience_sources
                 (source_id, project_id, label, source_type, path, content_hash, enabled, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(source_id) DO UPDATE SET
                 project_id=excluded.project_id,
                 label=excluded.label,
                 source_type=excluded.source_type,
                 path=excluded.path,
                 content_hash=excluded.content_hash,
                 enabled=excluded.enabled,
                 created_at=excluded.created_at""",
            (
                source.source_id,
                source.project_id,
                source.label,
                source.source_type,
                source.path,
                source.content_hash,
                1 if source.enabled else 0,
                source.created_at,
            ),
        )
        self._commit()

    def list_author_experience_sources(self, *, enabled_only: bool = True) -> list[AuthorExperienceSource]:
        where = " WHERE enabled=1" if enabled_only else ""
        rows = self.conn.execute(
            f"SELECT * FROM author_experience_sources{where} ORDER BY created_at DESC, source_id DESC"
        ).fetchall()
        return [_row_to_author_experience_source(r) for r in rows]

    def insert_author_experience_fragment(self, fragment: AuthorExperienceFragment) -> None:
        self.conn.execute(
            """INSERT INTO author_experience_fragments
                 (fragment_id, project_id, source_id, fragment_index, title_hint, text,
                  tags_json, emotion_json, self_schema_json, confidence)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(fragment_id) DO UPDATE SET
                 project_id=excluded.project_id,
                 source_id=excluded.source_id,
                 fragment_index=excluded.fragment_index,
                 title_hint=excluded.title_hint,
                 text=excluded.text,
                 tags_json=excluded.tags_json,
                 emotion_json=excluded.emotion_json,
                 self_schema_json=excluded.self_schema_json,
                 confidence=excluded.confidence""",
            (
                fragment.fragment_id,
                fragment.project_id,
                fragment.source_id,
                fragment.fragment_index,
                fragment.title_hint,
                fragment.text,
                json.dumps(fragment.tags_json, ensure_ascii=False),
                json.dumps(fragment.emotion_json, ensure_ascii=False),
                json.dumps(fragment.self_schema_json, ensure_ascii=False),
                fragment.confidence,
            ),
        )
        self._commit()

    def list_author_experience_fragments(self, source_id: str | None = None) -> list[AuthorExperienceFragment]:
        if source_id:
            rows = self.conn.execute(
                """SELECT * FROM author_experience_fragments
                   WHERE source_id=? ORDER BY fragment_index, fragment_id""",
                (source_id,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM author_experience_fragments ORDER BY source_id, fragment_index, fragment_id"
            ).fetchall()
        return [_row_to_author_experience_fragment(r) for r in rows]

    def upsert_author_life_model(self, model: AuthorLifeModel) -> None:
        self.conn.execute(
            """INSERT INTO author_life_models
                 (model_id, project_id, source_ids_json, source_label, summary, core_wound_json,
                  defense_patterns_json, desire_vectors_json, relationship_model_json,
                  narrative_engines_json, prose_rules_json, worldview_json, evidence_json,
                  confidence_json, persona_prompt, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(model_id) DO UPDATE SET
                 project_id=excluded.project_id,
                 source_ids_json=excluded.source_ids_json,
                 source_label=excluded.source_label,
                 summary=excluded.summary,
                 core_wound_json=excluded.core_wound_json,
                 defense_patterns_json=excluded.defense_patterns_json,
                 desire_vectors_json=excluded.desire_vectors_json,
                 relationship_model_json=excluded.relationship_model_json,
                 narrative_engines_json=excluded.narrative_engines_json,
                 prose_rules_json=excluded.prose_rules_json,
                 worldview_json=excluded.worldview_json,
                 evidence_json=excluded.evidence_json,
                 confidence_json=excluded.confidence_json,
                 persona_prompt=excluded.persona_prompt,
                 created_at=excluded.created_at""",
            (
                model.model_id,
                model.project_id,
                json.dumps(model.source_ids_json, ensure_ascii=False),
                model.source_label,
                model.summary,
                json.dumps(model.core_wound_json, ensure_ascii=False),
                json.dumps(model.defense_patterns_json, ensure_ascii=False),
                json.dumps(model.desire_vectors_json, ensure_ascii=False),
                json.dumps(model.relationship_model_json, ensure_ascii=False),
                json.dumps(model.narrative_engines_json, ensure_ascii=False),
                json.dumps(model.prose_rules_json, ensure_ascii=False),
                json.dumps(model.worldview_json, ensure_ascii=False),
                json.dumps(model.evidence_json, ensure_ascii=False),
                json.dumps(model.confidence_json, ensure_ascii=False),
                model.persona_prompt,
                model.created_at,
            ),
        )
        self._commit()

    def get_author_life_model(self, model_id: str) -> AuthorLifeModel | None:
        row = self.conn.execute("SELECT * FROM author_life_models WHERE model_id=?", (model_id,)).fetchone()
        return _row_to_author_life_model(row) if row else None

    def latest_author_life_model(self) -> AuthorLifeModel | None:
        row = self.conn.execute(
            "SELECT * FROM author_life_models ORDER BY created_at DESC, model_id DESC LIMIT 1"
        ).fetchone()
        return _row_to_author_life_model(row) if row else None

    def style_corpus_summary(self) -> dict[str, Any]:
        segments = self.list_style_segments()
        clusters = self.list_style_clusters()
        negatives = self.list_style_negative_samples(limit=100)
        life_model = self.latest_author_life_model()
        discourse: dict[str, int] = {}
        voices: dict[str, int] = {}
        scenes: dict[str, int] = {}
        character_voices: dict[str, int] = {}
        registers: dict[str, int] = {}
        for seg in segments:
            discourse[seg.discourse_type] = discourse.get(seg.discourse_type, 0) + 1
            voices[seg.voice_type] = voices.get(seg.voice_type, 0) + 1
            scenes[seg.scene_type] = scenes.get(seg.scene_type, 0) + 1
            registers[seg.register_type] = registers.get(seg.register_type, 0) + 1
            if seg.character_id and seg.voice_type == "character":
                character_voices[seg.character_id] = character_voices.get(seg.character_id, 0) + 1
        return {
            "segmentCount": len(segments),
            "clusterCount": len(clusters),
            "negativeSampleCount": len(negatives),
            "disabledSegmentCount": len([seg for seg in segments if not seg.enabled]),
            "discourseCoverage": discourse,
            "voiceCoverage": voices,
            "sceneCoverage": scenes,
            "registerCoverage": registers,
            "characterVoiceCoverage": character_voices,
            "lowConfidenceSegments": [
                {
                    "id": seg.id,
                    "sourceChapterId": seg.source_chapter_id,
                    "discourseType": seg.discourse_type,
                    "voiceType": seg.voice_type,
                    "confidence": seg.annotation_confidence,
                    "text": seg.text[:120],
                }
                for seg in segments
                if seg.annotation_confidence < 0.55
            ][:8],
            "clusters": [
                {
                    "id": cluster.id,
                    "label": cluster.label,
                    "clusterType": cluster.cluster_type,
                    "representativeSegmentIds": cluster.representative_segment_ids_json[:6],
                }
                for cluster in clusters[:12]
            ],
            "experienceSourceCount": len(self.list_author_experience_sources(enabled_only=False)),
            "experienceFragmentCount": len(self.list_author_experience_fragments()),
            "lifeModel": {
                "id": life_model.model_id,
                "summary": life_model.summary,
                "sourceLabel": life_model.source_label,
                "coreWound": life_model.core_wound_json,
                "defensePatterns": life_model.defense_patterns_json[:4],
                "desireVectors": life_model.desire_vectors_json[:4],
                "relationshipModel": life_model.relationship_model_json,
                "proseRules": life_model.prose_rules_json,
                "confidence": life_model.confidence_json,
            } if life_model else None,
        }

    def tone_profile_prompt(self) -> str:
        p = self.get_tone_profile()
        if not p.is_set():
            return ""
        lines = ["【文风契约 · 全程强制遵守，不得漂移】"]
        if p.genre or p.primary_effect:
            lines.append(f"类型：{p.genre or '未定'}；主效果：{p.primary_effect or '未定'}。")
        if p.register or p.sentence_rhythm:
            lines.append(f"语域与节奏：{p.register or '未定'}，{p.sentence_rhythm or '未定'}。")
        if p.diction_do:
            lines.append("鼓励：" + "、".join(p.diction_do[:8]) + "。")
        if p.diction_dont:
            lines.append("禁忌（出现即判不合格）：" + "、".join(p.diction_dont[:8]) + "。")
        if p.device_kit:
            lines.append("惯用手法：" + "、".join(p.device_kit[:8]) + "。")
        if p.tone_reference:
            lines.append(f"定调样例：{p.tone_reference[:200]}")
        lines.append(
            "【名字与正文语言一致】中文正文一律用中文名，不得混入拉丁字母人名。"
        )
        return "\n".join(lines)

    def _ensure_style_anchor(self) -> None:
        self.conn.execute("INSERT OR IGNORE INTO style_anchor (id) VALUES (1)")

    def get_style_anchor(self) -> dict[str, Any]:
        r = self.conn.execute("SELECT * FROM style_anchor WHERE id=1").fetchone()
        if not r:
            return {"tone_sample": "", "motif_lexicon": [], "banned_words": []}
        return {
            "tone_sample": r["tone_sample"],
            "motif_lexicon": json.loads(r["motif_lexicon"]),
            "banned_words": json.loads(r["banned_words"]),
        }

    def set_tone_sample(self, text: str) -> None:
        self._ensure_style_anchor()
        cur = self.conn.execute("SELECT tone_sample FROM style_anchor WHERE id=1").fetchone()
        if cur and cur["tone_sample"]:
            return
        self.conn.execute("UPDATE style_anchor SET tone_sample=? WHERE id=1", (text[:400],))
        self._commit()

    def add_motifs(self, words: list[str]) -> None:
        self._ensure_style_anchor()
        a = self.get_style_anchor()
        merged = list(dict.fromkeys([*a["motif_lexicon"], *[w for w in words if w]]))
        self.conn.execute(
            "UPDATE style_anchor SET motif_lexicon=? WHERE id=1",
            (json.dumps(merged, ensure_ascii=False),),
        )
        self._commit()

    def add_banned_words(self, words: list[str]) -> None:
        self._ensure_style_anchor()
        a = self.get_style_anchor()
        merged = list(dict.fromkeys([*a["banned_words"], *[w for w in words if w]]))
        self.conn.execute(
            "UPDATE style_anchor SET banned_words=? WHERE id=1",
            (json.dumps(merged, ensure_ascii=False),),
        )
        self._commit()

    def style_anchor_prompt(self) -> str:
        a = self.get_style_anchor()
        if not (a["motif_lexicon"] or a["banned_words"]):
            return ""
        parts = ["[嗓音一致性]"]
        if a["motif_lexicon"]:
            parts.append("已用核心意象（可少量呼应，不要重复堆砌）：" + "、".join(a["motif_lexicon"][:12]))
        if a["banned_words"]:
            parts.append("统一禁用词（不要使用）：" + "、".join(a["banned_words"][:12]))
        return "\n".join(parts)

    def bump_emotion(self, agent_id: str, emotion: str, intensity: float,
                     cause: str, tick: int, decay: float = 0.25) -> None:
        self.conn.execute(
            """INSERT INTO emotional_state (agent_id, emotion, intensity, cause, decay, updated_tick)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(agent_id) DO UPDATE SET
                 emotion=excluded.emotion, intensity=excluded.intensity,
                 cause=excluded.cause, decay=excluded.decay, updated_tick=excluded.updated_tick""",
            (agent_id, emotion, max(0.0, min(1.0, intensity)), cause, decay, tick),
        )
        self._commit()

    def get_emotion(self, agent_id: str) -> "EmotionalState | None":
        r = self.conn.execute(
            "SELECT * FROM emotional_state WHERE agent_id=?", (agent_id,)
        ).fetchone()
        if not r:
            return None
        return EmotionalState(
            agent_id=r["agent_id"], emotion=r["emotion"], intensity=r["intensity"],
            cause=r["cause"], decay=r["decay"], updated_tick=r["updated_tick"],
        )

    def emotional_residue_text(self, agent_id: str) -> str:
        e = self.get_emotion(agent_id)
        if not e or e.intensity < 0.15 or not e.emotion:
            return ""
        because = f"（因{e.cause}）" if e.cause else ""
        return f"你心里还压着一股{e.emotion}{because}，没那么快散。"

    def decay_emotions(self) -> None:
        self.conn.execute("UPDATE emotional_state SET intensity = intensity - decay")
        self.conn.execute("DELETE FROM emotional_state WHERE intensity <= 0.05")
        self._commit()

    # ---------- factions锛圵3 鍔垮姏涓€绛夊疄浣擄級 ----------
    def upsert_faction(self, f: Faction) -> None:
        self.conn.execute(
            """INSERT INTO factions
                 (faction_id, name, ideology, goals, methods, territory, structure,
                  key_members, history, relations, secret, summary, detail, source, created_at,
                  foreshadow_from, reveal_chapter, secret_reveal_chapter, foreshadow_hint, secret_truth)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(faction_id) DO UPDATE SET
                 name=excluded.name, ideology=excluded.ideology, goals=excluded.goals,
                 methods=excluded.methods, territory=excluded.territory,
                 structure=excluded.structure, key_members=excluded.key_members,
                 history=excluded.history, relations=excluded.relations,
                 secret=excluded.secret, summary=excluded.summary, detail=excluded.detail,
                 source=excluded.source,
                 foreshadow_from=excluded.foreshadow_from,
                 reveal_chapter=excluded.reveal_chapter,
                 secret_reveal_chapter=excluded.secret_reveal_chapter,
                 foreshadow_hint=excluded.foreshadow_hint,
                 secret_truth=excluded.secret_truth""",
            (f.faction_id, f.name, f.ideology, f.goals, f.methods,
             json.dumps(f.territory, ensure_ascii=False), f.structure,
             json.dumps(f.key_members, ensure_ascii=False), f.history,
             json.dumps(f.relations, ensure_ascii=False), f.secret,
             f.summary, f.detail, f.source, f.created_at, f.foreshadow_from,
             f.reveal_chapter, f.secret_reveal_chapter, f.foreshadow_hint, f.secret_truth),
        )
        self._commit()

    def get_faction(self, faction_id: str) -> Faction | None:
        r = self.conn.execute("SELECT * FROM factions WHERE faction_id=?", (faction_id,)).fetchone()
        return _row_to_faction(r) if r else None

    def list_factions(self) -> list[Faction]:
        rows = self.conn.execute("SELECT * FROM factions ORDER BY faction_id").fetchall()
        return [_row_to_faction(r) for r in rows]

    def faction_summaries_text(self) -> str:
        rows = [f for f in self.list_factions() if (f.summary or "").strip()]
        return "\n".join(f"· {f.name}：{f.summary.strip()}" for f in rows)

    def upsert_edge(self, e: GraphEdge) -> None:
        self.conn.execute(
            """INSERT INTO graph_edges (src, rel, dst, meta, since_chapter, until_chapter,
                                        intensity, last_active_chapter)
               VALUES (?,?,?,?,?,?,?,?)""",
            (e.src, e.rel, e.dst, json.dumps(e.meta, ensure_ascii=False),
             e.since_chapter, e.until_chapter, e.intensity, e.last_active_chapter),
        )
        self._commit()

    def get_edge(self, src: str, rel: str, dst: str) -> GraphEdge | None:
        r = self.conn.execute(
            "SELECT * FROM graph_edges WHERE src=? AND rel=? AND dst=?",
            (src, rel, dst)).fetchone()
        return _row_to_edge(r) if r else None

    def list_edges(self, *, src: str | None = None, dst: str | None = None,
                   rel: str | None = None) -> list[GraphEdge]:
        sql = "SELECT * FROM graph_edges WHERE 1=1"
        args: list[Any] = []
        if src:
            sql += " AND src=?"
            args.append(src)
        if dst:
            sql += " AND dst=?"
            args.append(dst)
        if rel:
            sql += " AND rel=?"
            args.append(rel)
        sql += " ORDER BY id"
        return [_row_to_edge(r) for r in self.conn.execute(sql, args).fetchall()]

    def bump_edge_attention(self, src: str, rel: str, dst: str, chapter: int,
                            delta: float = 0.15, meta_patch: dict | None = None) -> None:
        cur = self.get_edge(src, rel, dst)
        if cur is None:
            new_meta = dict(meta_patch or {})
            self.upsert_edge(GraphEdge(
                src=src, rel=rel, dst=dst, meta=new_meta,
                since_chapter=chapter, intensity=min(1.0, 0.5 + delta),
                last_active_chapter=chapter,
            ))
            return
        new_intensity = max(0.0, min(1.0, cur.intensity + delta))
        merged_meta = {**cur.meta, **(meta_patch or {})}
        self.conn.execute(
            """UPDATE graph_edges SET intensity=?, last_active_chapter=?, meta=?
               WHERE src=? AND rel=? AND dst=?""",
            (new_intensity, chapter, json.dumps(merged_meta, ensure_ascii=False), src, rel, dst),
        )
        self._commit()

    def decay_edges(self, current_chapter: int, half_life: int = 6,
                    rels: tuple[str, ...] = ("related_to", "knows")) -> int:
        rows = self.conn.execute(
            f"SELECT id, intensity, last_active_chapter FROM graph_edges WHERE rel IN ({','.join('?'*len(rels))})",
            rels,
        ).fetchall()
        n = 0
        for r in rows:
            gap = max(0, current_chapter - r["last_active_chapter"])
            if gap == 0:
                continue
            factor = 0.5 ** (gap / max(1, half_life))
            new_i = max(0.0, r["intensity"] * factor)
            self.conn.execute("UPDATE graph_edges SET intensity=? WHERE id=?", (new_i, r["id"]))
            n += 1
        self._commit()
        return n

    def attention_ranked_neighbors(self, seed: str, *, limit: int = 12,
                                   rels: tuple[str, ...] | None = None) -> list[GraphEdge]:
        sql = "SELECT * FROM graph_edges WHERE (src=? OR dst=?)"
        args: list[Any] = [seed, seed]
        if rels:
            sql += f" AND rel IN ({','.join('?'*len(rels))})"
            args += list(rels)
        sql += " ORDER BY intensity DESC, last_active_chapter DESC LIMIT ?"
        args.append(limit)
        return [_row_to_edge(r) for r in self.conn.execute(sql, args).fetchall()]

    # ---------- character_cards锛埪? 閫夎灞傝韩浠藉崱 + W4 涓夌淮搴︼級 ----------
    def add_card(self, c: CharacterCard) -> None:
        self.conn.execute(
            """INSERT INTO character_cards
                 (card_id, agent_id, tier, slot_key, name, one_liner, voice_register,
                  defining_trait, core_desire, verbal_habits, key_relation, backstory,
                  fatal_flaw, motif_objects, relationship_map, arc,
                  appearance, social_role, psychology, created_at, foreshadow_from,
                  reveal_chapter, secret_reveal_chapter, foreshadow_hint, secret_truth)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(card_id) DO UPDATE SET
                 agent_id=excluded.agent_id, tier=excluded.tier, slot_key=excluded.slot_key,
                 name=excluded.name, one_liner=excluded.one_liner,
                 voice_register=excluded.voice_register, defining_trait=excluded.defining_trait,
                 core_desire=excluded.core_desire, verbal_habits=excluded.verbal_habits,
                 key_relation=excluded.key_relation, backstory=excluded.backstory,
                 fatal_flaw=excluded.fatal_flaw, motif_objects=excluded.motif_objects,
                 relationship_map=excluded.relationship_map, arc=excluded.arc,
                 appearance=excluded.appearance, social_role=excluded.social_role,
                 psychology=excluded.psychology,
                 foreshadow_from=excluded.foreshadow_from,
                 reveal_chapter=excluded.reveal_chapter,
                 secret_reveal_chapter=excluded.secret_reveal_chapter,
                 foreshadow_hint=excluded.foreshadow_hint,
                 secret_truth=excluded.secret_truth""",
            (
                c.card_id, c.agent_id, c.tier, c.slot_key, c.name, c.one_liner, c.voice_register,
                c.defining_trait, c.core_desire, c.verbal_habits, c.key_relation, c.backstory,
                c.fatal_flaw, json.dumps(c.motif_objects, ensure_ascii=False),
                json.dumps(c.relationship_map, ensure_ascii=False), c.arc,
                c.appearance, c.social_role, c.psychology, c.created_at,
                c.foreshadow_from, c.reveal_chapter, c.secret_reveal_chapter,
                c.foreshadow_hint, c.secret_truth,
            ),
        )
        self._commit()

    def get_card_by_slot(self, slot_key: str) -> CharacterCard | None:
        r = self.conn.execute(
            "SELECT * FROM character_cards WHERE slot_key=?", (slot_key,)
        ).fetchone()
        return _row_to_card(r) if r else None

    def get_card_for_agent(self, agent_id: str) -> CharacterCard | None:
        r = self.conn.execute(
            "SELECT * FROM character_cards WHERE agent_id=? LIMIT 1", (agent_id,)
        ).fetchone()
        return _row_to_card(r) if r else None

    def list_cards(self) -> list[CharacterCard]:
        rows = self.conn.execute("SELECT * FROM character_cards").fetchall()
        return [_row_to_card(r) for r in rows]

    # ---------- naming profiles ----------
    def upsert_naming_profile(self, profile: NamingProfile) -> None:
        payload = {
            "scope": profile.scope,
            "label": profile.label,
            "genre": profile.genre,
            "culture_source": profile.culture_source,
            "phonology_style": profile.phonology_style,
            "primary_length_min": profile.primary_length_min,
            "primary_length_max": profile.primary_length_max,
            "allow_surname": profile.allow_surname,
            "allow_compound_given_name": profile.allow_compound_given_name,
            "allow_middle_dot": profile.allow_middle_dot,
            "allow_hyphen": profile.allow_hyphen,
            "allow_space": profile.allow_space,
            "nickname_rules": profile.nickname_rules,
            "honorific_rules": profile.honorific_rules,
            "faction_variance_policy": profile.faction_variance_policy,
            "rare_structure_quota": profile.rare_structure_quota,
            "motif_token_budget": profile.motif_token_budget,
            "banned_tokens": profile.banned_tokens,
            "danger_tokens": profile.danger_tokens,
            "stopwords_for_primary": profile.stopwords_for_primary,
            "version": profile.version,
        }
        self.conn.execute(
            """INSERT INTO naming_profiles
                 (profile_id, scope, label, profile_json, active_version, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(profile_id) DO UPDATE SET
                 scope=excluded.scope, label=excluded.label, profile_json=excluded.profile_json,
                 active_version=excluded.active_version, updated_at=excluded.updated_at""",
            (
                profile.profile_id,
                profile.scope,
                profile.label,
                json.dumps(payload, ensure_ascii=False),
                profile.version,
                "",
                "",
            ),
        )
        self._commit()

    def get_naming_profile(self, profile_id: str | None = None) -> NamingProfile | None:
        if profile_id:
            row = self.conn.execute("SELECT * FROM naming_profiles WHERE profile_id=?", (profile_id,)).fetchone()
        else:
            row = self.conn.execute("SELECT * FROM naming_profiles ORDER BY rowid LIMIT 1").fetchone()
        return _row_to_naming_profile(row) if row else None

    def upsert_culture_naming_style(self, style: CultureNamingStyle) -> None:
        payload = {
            "parent_style_id": style.parent_style_id,
            "surname_pool": style.surname_pool,
            "given_name_pool": style.given_name_pool,
            "title_pool": style.title_pool,
            "morphology_templates": style.morphology_templates,
            "disallowed_templates": style.disallowed_templates,
            "nickname_patterns": style.nickname_patterns,
            "honorific_patterns": style.honorific_patterns,
            "enemy_label_patterns": style.enemy_label_patterns,
            "symbol_policy": style.symbol_policy,
        }
        self.conn.execute(
            """INSERT INTO culture_naming_styles
                 (style_id, profile_id, culture_id, culture_name, style_json, fingerprint_json, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(style_id) DO UPDATE SET
                 profile_id=excluded.profile_id, culture_id=excluded.culture_id, culture_name=excluded.culture_name,
                 style_json=excluded.style_json, fingerprint_json=excluded.fingerprint_json,
                 updated_at=excluded.updated_at""",
            (
                style.style_id,
                style.profile_id,
                style.culture_id,
                style.culture_name,
                json.dumps(payload, ensure_ascii=False),
                json.dumps(style.style_fingerprint, ensure_ascii=False),
                "",
                "",
            ),
        )
        self._commit()

    def list_culture_naming_styles(self, profile_id: str | None = None) -> list[CultureNamingStyle]:
        if profile_id:
            rows = self.conn.execute(
                "SELECT * FROM culture_naming_styles WHERE profile_id=? ORDER BY style_id", (profile_id,)
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM culture_naming_styles ORDER BY style_id").fetchall()
        return [_row_to_culture_naming_style(r) for r in rows]

    def get_culture_naming_style(self, style_id: str) -> CultureNamingStyle | None:
        row = self.conn.execute("SELECT * FROM culture_naming_styles WHERE style_id=?", (style_id,)).fetchone()
        return _row_to_culture_naming_style(row) if row else None

    def upsert_character_name(self, record: CharacterNameRecord) -> None:
        self.conn.execute(
            """INSERT INTO character_names
                 (agent_id, profile_id, culture_style_id, primary_name, short_name, nickname,
                  honorific, public_alias, self_ref, enemy_label, display_name_locked,
                  normalized_name, name_parts_json, source, status, replaced_by_agent_id,
                  audit_flags_json, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(agent_id) DO UPDATE SET
                 profile_id=excluded.profile_id, culture_style_id=excluded.culture_style_id,
                 primary_name=excluded.primary_name, short_name=excluded.short_name,
                 nickname=excluded.nickname, honorific=excluded.honorific,
                 public_alias=excluded.public_alias, self_ref=excluded.self_ref,
                 enemy_label=excluded.enemy_label, display_name_locked=excluded.display_name_locked,
                 normalized_name=excluded.normalized_name, name_parts_json=excluded.name_parts_json,
                 source=excluded.source, status=excluded.status,
                 replaced_by_agent_id=excluded.replaced_by_agent_id,
                 audit_flags_json=excluded.audit_flags_json, updated_at=excluded.updated_at""",
            (
                record.agent_id,
                record.profile_id,
                record.culture_style_id,
                record.primary_name,
                record.short_name,
                record.nickname,
                record.honorific,
                record.public_alias,
                record.self_ref,
                record.enemy_label,
                record.display_name_locked,
                record.primary_name_normalized,
                json.dumps(record.name_parts_json, ensure_ascii=False),
                record.source,
                record.status,
                record.replaced_by_agent_id,
                json.dumps(record.audit_flags, ensure_ascii=False),
                record.created_at,
                record.updated_at,
            ),
        )
        self._commit()

    def get_character_name(self, agent_id: str) -> CharacterNameRecord | None:
        row = self.conn.execute("SELECT * FROM character_names WHERE agent_id=?", (agent_id,)).fetchone()
        return _row_to_character_name(row) if row else None

    def list_character_names(self) -> list[CharacterNameRecord]:
        rows = self.conn.execute("SELECT * FROM character_names ORDER BY agent_id").fetchall()
        return [_row_to_character_name(r) for r in rows]

    def get_character_display_name(self, agent_id: str, fallback: str = "") -> str:
        record = self.get_character_name(agent_id)
        if record and record.display_name_locked:
            return record.display_name_locked
        entity = self.get_entity(agent_id)
        return entity.name if entity and entity.name else fallback

    def append_character_name_history(
        self,
        agent_id: str,
        old_primary_name: str,
        new_primary_name: str,
        reason: str,
        migration_batch_id: str = "",
    ) -> None:
        self.conn.execute(
            """INSERT INTO character_name_history
                 (agent_id, old_primary_name, new_primary_name, reason, migration_batch_id, created_at)
               VALUES (?, ?, ?, ?, ?, '')""",
            (agent_id, old_primary_name, new_primary_name, reason, migration_batch_id),
        )
        self._commit()

    def list_character_name_history(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM character_name_history ORDER BY id"
        ).fetchall()
        return [dict(row) for row in rows]

    # ---------- llm_logs锛圠LM 瀵硅瘽鏃ュ織锛?----------
    def list_llm_logs(self, limit: int = 200, caller: str | None = None) -> list[dict]:
        sql = "SELECT * FROM llm_logs"
        args: list = []
        if caller:
            sql += " WHERE caller=?"
            args.append(caller)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        try:
            rows = self.conn.execute(sql, args).fetchall()
        except Exception:
            return []
        return [dict(r) for r in rows]

    def get_llm_log(self, log_id: int) -> dict | None:
        try:
            r = self.conn.execute("SELECT * FROM llm_logs WHERE id=?", (log_id,)).fetchone()
        except Exception:
            return None
        return dict(r) if r else None

    def llm_log_stats(self) -> dict:
        try:
            rows = self.conn.execute(
                "SELECT caller, COUNT(*) as n, SUM(elapsed_ms) as total_ms "
                "FROM llm_logs GROUP BY caller ORDER BY n DESC"
            ).fetchall()
        except Exception:
            return {"callers": []}
        return {"callers": [dict(r) for r in rows]}

    # ---------- unified chapter pipeline ----------
    def get_project_meta(self) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM project_meta WHERE id=1").fetchone()
        if row is None:
            self.conn.execute("INSERT OR IGNORE INTO project_meta (id) VALUES (1)")
            self._commit()
            row = self.conn.execute("SELECT * FROM project_meta WHERE id=1").fetchone()
        return dict(row)

    def set_project_meta(self, *, project_type: str | None = None,
                         project_status: str | None = None,
                         analysis_status: str | None = None,
                         source_text_hash: str | None = None,
                         continuation_hint: str | None = None,
                         series_id: str | None = None,
                         source_book_title: str | None = None,
                         current_book_title: str | None = None,
                         book_index: int | None = None,
                         write_mode: str | None = None,
                         chapter_start_no: int | None = None,
                          latest_source_chapter_no: int | None = None,
                          continuation_ready: bool | None = None,
                          continuation_phase: str | None = None,
                          time_position: str | None = None,
                          protagonist_strategy: str | None = None,
                          inherit_unresolved_threads: bool | None = None,
                          experience_layer_enabled: bool | None = None,
                          experience_layer_mode: str | None = None,
                          experience_source_path: str | None = None,
                          experience_style_level: str | None = None,
                          active_life_model_id: str | None = None) -> None:
        cur = self.get_project_meta()
        self.conn.execute(
            """INSERT INTO project_meta (
                   id, project_type, project_status, analysis_status,
                   source_text_hash, continuation_hint, series_id,
                   source_book_title, current_book_title, book_index,
                   write_mode, chapter_start_no, latest_source_chapter_no,
                   continuation_ready, continuation_phase, time_position,
                   protagonist_strategy, inherit_unresolved_threads,
                   experience_layer_enabled, experience_layer_mode, experience_source_path,
                   experience_style_level, active_life_model_id
               )
               VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 project_type=excluded.project_type,
                 project_status=excluded.project_status,
                 analysis_status=excluded.analysis_status,
                 source_text_hash=excluded.source_text_hash,
                 continuation_hint=excluded.continuation_hint,
                 series_id=excluded.series_id,
                 source_book_title=excluded.source_book_title,
                 current_book_title=excluded.current_book_title,
                 book_index=excluded.book_index,
                 write_mode=excluded.write_mode,
                 chapter_start_no=excluded.chapter_start_no,
                 latest_source_chapter_no=excluded.latest_source_chapter_no,
                 continuation_ready=excluded.continuation_ready,
                 continuation_phase=excluded.continuation_phase,
                 time_position=excluded.time_position,
                 protagonist_strategy=excluded.protagonist_strategy,
                 inherit_unresolved_threads=excluded.inherit_unresolved_threads,
                 experience_layer_enabled=excluded.experience_layer_enabled,
                 experience_layer_mode=excluded.experience_layer_mode,
                 experience_source_path=excluded.experience_source_path,
                 experience_style_level=excluded.experience_style_level,
                 active_life_model_id=excluded.active_life_model_id""",
            (
                project_type or cur["project_type"],
                project_status or cur["project_status"],
                analysis_status or cur["analysis_status"],
                source_text_hash if source_text_hash is not None else cur.get("source_text_hash", ""),
                continuation_hint if continuation_hint is not None else cur.get("continuation_hint", ""),
                series_id if series_id is not None else cur.get("series_id", ""),
                source_book_title if source_book_title is not None else cur.get("source_book_title", ""),
                current_book_title if current_book_title is not None else cur.get("current_book_title", ""),
                int(book_index if book_index is not None else cur.get("book_index", 1) or 1),
                write_mode if write_mode is not None else cur.get("write_mode", ""),
                int(chapter_start_no if chapter_start_no is not None else cur.get("chapter_start_no", 1) or 1),
                int(latest_source_chapter_no if latest_source_chapter_no is not None else cur.get("latest_source_chapter_no", 0) or 0),
                1 if (continuation_ready if continuation_ready is not None else bool(cur.get("continuation_ready", 0))) else 0,
                continuation_phase if continuation_phase is not None else cur.get("continuation_phase", ""),
                time_position if time_position is not None else cur.get("time_position", ""),
                protagonist_strategy if protagonist_strategy is not None else cur.get("protagonist_strategy", ""),
                1 if (inherit_unresolved_threads if inherit_unresolved_threads is not None else bool(cur.get("inherit_unresolved_threads", 1))) else 0,
                1 if (experience_layer_enabled if experience_layer_enabled is not None else bool(cur.get("experience_layer_enabled", 0))) else 0,
                experience_layer_mode if experience_layer_mode is not None else cur.get("experience_layer_mode", "off"),
                experience_source_path if experience_source_path is not None else cur.get("experience_source_path", ""),
                experience_style_level if experience_style_level is not None else cur.get("experience_style_level", "none"),
                active_life_model_id if active_life_model_id is not None else cur.get("active_life_model_id", ""),
            ),
        )
        self._commit()

    def get_story_timeline(self) -> list[dict[str, Any]]:
        """故事时钟时间线（轻量 JSON）：[{chapter_no, end_clock, end_clock_text, deadlines:[...]}]。"""
        row = self.conn.execute("SELECT timeline_json FROM project_meta WHERE id=1").fetchone()
        if row is None:
            self.conn.execute("INSERT OR IGNORE INTO project_meta (id) VALUES (1)")
            self._commit()
            return []
        try:
            data = json.loads(row["timeline_json"] or "[]")
        except Exception:
            return []
        return data if isinstance(data, list) else []

    def set_story_timeline(self, timeline: list[dict[str, Any]]) -> None:
        self.conn.execute("INSERT OR IGNORE INTO project_meta (id) VALUES (1)")
        self.conn.execute(
            "UPDATE project_meta SET timeline_json=? WHERE id=1",
            (json.dumps(timeline or [], ensure_ascii=False),),
        )
        self._commit()

    def upsert_timeline_entry(self, entry: dict[str, Any]) -> None:
        """按 chapter_no 落入/覆盖一章的时间线条目，并保持按章号有序。"""
        chapter_no = int(entry.get("chapter_no", 0) or 0)
        if chapter_no <= 0:
            return
        timeline = [r for r in self.get_story_timeline()
                    if int(r.get("chapter_no", 0) or 0) != chapter_no]
        timeline.append(entry)
        timeline.sort(key=lambda r: int(r.get("chapter_no", 0) or 0))
        self.set_story_timeline(timeline)

    def get_build_checkpoints(self) -> dict[str, Any]:
        row = self.conn.execute("SELECT build_checkpoints_json FROM project_meta WHERE id=1").fetchone()
        if row is None:
            self.conn.execute("INSERT OR IGNORE INTO project_meta (id) VALUES (1)")
            self._commit()
            return {}
        try:
            data = json.loads(row["build_checkpoints_json"] or "{}")
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def set_build_checkpoints(self, checkpoints: dict[str, Any]) -> None:
        self.conn.execute(
            "UPDATE project_meta SET build_checkpoints_json=? WHERE id=1",
            (json.dumps(checkpoints or {}, ensure_ascii=False),),
        )
        self._commit()

    def mark_build_checkpoint(self, stage: str, status: str, *,
                              error: str = "", meta: dict[str, Any] | None = None) -> None:
        checkpoints = self.get_build_checkpoints()
        checkpoints[stage] = {
            "status": status,
            "updatedAt": datetime.now(timezone.utc).isoformat(),
            "error": error or "",
            "meta": meta or {},
        }
        self.set_build_checkpoints(checkpoints)

    def build_checkpoint_status(self, stage: str) -> str:
        item = self.get_build_checkpoints().get(stage)
        if isinstance(item, dict):
            return str(item.get("status") or "")
        if isinstance(item, str):
            return item
        return ""

    def get_continuation_meta(self) -> ContinuationMeta:
        cur = self.get_project_meta()
        return ContinuationMeta(
            source_text_hash=cur.get("source_text_hash", "") or "",
            continuation_hint=cur.get("continuation_hint", "") or "",
            series_id=cur.get("series_id", "") or "",
            source_book_title=cur.get("source_book_title", "") or "",
            current_book_title=cur.get("current_book_title", "") or "",
            book_index=int(cur.get("book_index", 1) or 1),
            write_mode=cur.get("write_mode", "") or "",
            chapter_start_no=int(cur.get("chapter_start_no", 1) or 1),
            latest_source_chapter_no=int(cur.get("latest_source_chapter_no", 0) or 0),
            continuation_ready=bool(cur.get("continuation_ready", 0)),
            continuation_phase=cur.get("continuation_phase", "") or "",
            time_position=cur.get("time_position", "") or "",
            protagonist_strategy=cur.get("protagonist_strategy", "") or "",
            inherit_unresolved_threads=bool(cur.get("inherit_unresolved_threads", 1)),
            experience_layer_enabled=bool(cur.get("experience_layer_enabled", 0)),
            experience_layer_mode=cur.get("experience_layer_mode", "off") or "off",
            experience_source_path=cur.get("experience_source_path", "") or "",
            experience_style_level=cur.get("experience_style_level", "none") or "none",
            active_life_model_id=cur.get("active_life_model_id", "") or "",
        )

    def set_continuation_meta(self, meta: ContinuationMeta) -> None:
        self.set_project_meta(
            source_text_hash=meta.source_text_hash,
            continuation_hint=meta.continuation_hint,
            series_id=meta.series_id,
            source_book_title=meta.source_book_title,
            current_book_title=meta.current_book_title,
            book_index=meta.book_index,
            write_mode=meta.write_mode,
            chapter_start_no=meta.chapter_start_no,
            latest_source_chapter_no=meta.latest_source_chapter_no,
            continuation_ready=meta.continuation_ready,
            continuation_phase=meta.continuation_phase,
            time_position=meta.time_position,
            protagonist_strategy=meta.protagonist_strategy,
            inherit_unresolved_threads=meta.inherit_unresolved_threads,
            experience_layer_enabled=meta.experience_layer_enabled,
            experience_layer_mode=meta.experience_layer_mode,
            experience_source_path=meta.experience_source_path,
            experience_style_level=meta.experience_style_level,
            active_life_model_id=meta.active_life_model_id,
        )

    def upsert_story_bible_record(self, rec: StoryBibleRecord) -> None:
        self.conn.execute(
            """INSERT INTO story_bible_v2
                 (id, project_id, source_type, title_style_json, world_config_json,
                  characters_json, locations_json, factions_json, items_json,
                  relationships_json, timeline_json, open_threads_json,
                  last_state_json, narrative_constraints_json,
                  style_profile_id, updated_at)
               VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 project_id=excluded.project_id,
                 source_type=excluded.source_type,
                 title_style_json=excluded.title_style_json,
                 world_config_json=excluded.world_config_json,
                 characters_json=excluded.characters_json,
                 locations_json=excluded.locations_json,
                 factions_json=excluded.factions_json,
                 items_json=excluded.items_json,
                 relationships_json=excluded.relationships_json,
                 timeline_json=excluded.timeline_json,
                 open_threads_json=excluded.open_threads_json,
                 last_state_json=excluded.last_state_json,
                 narrative_constraints_json=excluded.narrative_constraints_json,
                 style_profile_id=excluded.style_profile_id,
                 updated_at=excluded.updated_at""",
            (
                rec.project_id,
                rec.source_type,
                json.dumps(rec.title_style_json, ensure_ascii=False),
                json.dumps(rec.world_config_json, ensure_ascii=False),
                json.dumps(rec.characters_json, ensure_ascii=False),
                json.dumps(rec.locations_json, ensure_ascii=False),
                json.dumps(rec.factions_json, ensure_ascii=False),
                json.dumps(rec.items_json, ensure_ascii=False),
                json.dumps(rec.relationships_json, ensure_ascii=False),
                json.dumps(rec.timeline_json, ensure_ascii=False),
                json.dumps(rec.open_threads_json, ensure_ascii=False),
                json.dumps(rec.last_state_json, ensure_ascii=False),
                json.dumps(rec.narrative_constraints_json, ensure_ascii=False),
                rec.style_profile_id,
                rec.updated_at,
            ),
        )
        self._commit()

    def get_story_bible_record(self) -> StoryBibleRecord | None:
        row = self.conn.execute("SELECT * FROM story_bible_v2 WHERE id=1").fetchone()
        return _row_to_story_bible_record(row) if row else None

    def set_writing_settings(self, rec: WritingSettings) -> None:
        self.conn.execute(
            """INSERT INTO writing_settings
                 (id, project_id, target_words, min_words, max_words, outline_first,
                  auto_chapter_count, require_human_acceptance, style_profile_id)
               VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 project_id=excluded.project_id,
                 target_words=excluded.target_words,
                 min_words=excluded.min_words,
                 max_words=excluded.max_words,
                 outline_first=excluded.outline_first,
                 auto_chapter_count=excluded.auto_chapter_count,
                 require_human_acceptance=excluded.require_human_acceptance,
                 style_profile_id=excluded.style_profile_id""",
            (
                rec.project_id,
                rec.target_words,
                rec.min_words,
                rec.max_words,
                1 if rec.outline_first else 0,
                rec.auto_chapter_count,
                1 if rec.require_human_acceptance else 0,
                rec.style_profile_id,
            ),
        )
        self._commit()

    def get_writing_settings(self) -> WritingSettings:
        row = self.conn.execute("SELECT * FROM writing_settings WHERE id=1").fetchone()
        return _row_to_writing_settings(row) if row else WritingSettings()

    def insert_source_document(self, doc: SourceDocument) -> int:
        cur = self.conn.execute(
            """INSERT INTO source_documents (project_id, filename, format, raw_text, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (doc.project_id, doc.filename, doc.format, doc.raw_text, doc.created_at),
        )
        self._commit()
        return int(cur.lastrowid)

    def clear_source_material(self) -> None:
        self.conn.execute("DELETE FROM style_negative_samples")
        self.conn.execute("DELETE FROM style_clusters")
        self.conn.execute("DELETE FROM style_segments")
        self.conn.execute("DELETE FROM source_chunks")
        self.conn.execute("DELETE FROM source_chapters")
        self.conn.execute("DELETE FROM source_documents")
        self._commit()

    def list_source_documents(self) -> list[SourceDocument]:
        rows = self.conn.execute("SELECT * FROM source_documents ORDER BY id").fetchall()
        return [_row_to_source_document(r) for r in rows]

    def insert_source_chapter(self, ch: SourceChapter) -> int:
        cur = self.conn.execute(
            """INSERT INTO source_chapters
                 (project_id, source_document_id, chapter_no, title, text, word_count, summary, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (ch.project_id, ch.source_document_id, ch.chapter_no, ch.title, ch.text,
             ch.word_count, ch.summary, ch.created_at),
        )
        self._commit()
        return int(cur.lastrowid)

    def update_source_chapter(self, chapter_id: int, *, title: str | None = None,
                              text: str | None = None, summary: str | None = None) -> None:
        row = self.conn.execute("SELECT * FROM source_chapters WHERE id=?", (chapter_id,)).fetchone()
        if row is None:
            return
        self.conn.execute(
            """UPDATE source_chapters
               SET title=?, text=?, word_count=?, summary=?
               WHERE id=?""",
            (
                title if title is not None else row["title"],
                text if text is not None else row["text"],
                len((text if text is not None else row["text"]).strip()),
                summary if summary is not None else row["summary"],
                chapter_id,
            ),
        )
        self._commit()

    def list_source_chapters(self) -> list[SourceChapter]:
        rows = self.conn.execute("SELECT * FROM source_chapters ORDER BY chapter_no, id").fetchall()
        return [_row_to_source_chapter(r) for r in rows]

    # ===== 瀹屽叏钂搁锛氭瘡绔犱簨浠?/ 浜虹墿蹇収 / 璁惧畾 codex / 鍓ф儏涓荤嚎 / 浼忕瑪 =====
    def clear_distillation_artifacts(self) -> None:
        self.conn.execute("DELETE FROM source_events")
        self.conn.execute("DELETE FROM character_state_snapshots")
        self.conn.execute("DELETE FROM settings_codex")
        self.conn.execute("DELETE FROM story_arcs")
        self.conn.execute("DELETE FROM foreshadow_setups")
        self._commit()

    def insert_source_event(self, *, event_id: str, chapter_no: int, seq: int, summary: str,
                            participants: list[str], location: str, time_marker: str, kind: str,
                            causes_from: list[str], effects: str, created_at: str) -> None:
        self.conn.execute(
            """INSERT INTO source_events
                 (event_id, project_id, chapter_no, seq, summary, participants_json, location,
                  time_marker, kind, causes_from_json, effects, created_at)
               VALUES (?, '', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event_id,
                chapter_no,
                seq,
                summary,
                json.dumps(participants, ensure_ascii=False),
                location,
                time_marker,
                kind,
                json.dumps(causes_from, ensure_ascii=False),
                effects,
                created_at,
            ),
        )
        self._commit()

    def list_source_events(self, chapter_no: int | None = None) -> list[dict[str, Any]]:
        if chapter_no is None:
            rows = self.conn.execute("SELECT * FROM source_events ORDER BY chapter_no, seq").fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM source_events WHERE chapter_no=? ORDER BY seq", (chapter_no,)).fetchall()
        return [{
            "event_id": r["event_id"], "chapter_no": r["chapter_no"], "seq": r["seq"],
            "summary": r["summary"], "participants": json.loads(r["participants_json"] or "[]"),
            "location": r["location"], "time_marker": r["time_marker"], "kind": r["kind"],
            "causes_from": json.loads(r["causes_from_json"] or "[]"), "effects": r["effects"],
        } for r in rows]

    def insert_character_snapshot(self, *, chapter_no: int, character_name: str,
                                  snapshot: dict[str, Any], changed_fields: list[str]) -> None:
        self.conn.execute(
            """INSERT INTO character_state_snapshots
                 (project_id, chapter_no, character_name, snapshot_json, changed_fields_json)
               VALUES ('', ?, ?, ?, ?)""",
            (chapter_no, character_name, json.dumps(snapshot, ensure_ascii=False),
             json.dumps(changed_fields, ensure_ascii=False)),
        )
        self._commit()

    def list_character_snapshots(self, character_name: str | None = None) -> list[dict[str, Any]]:
        if character_name is None:
            rows = self.conn.execute(
                "SELECT * FROM character_state_snapshots ORDER BY chapter_no, character_name").fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM character_state_snapshots WHERE character_name=? ORDER BY chapter_no",
                (character_name,)).fetchall()
        return [{
            "chapter_no": r["chapter_no"], "character_name": r["character_name"],
            "snapshot": json.loads(r["snapshot_json"] or "{}"),
            "changed_fields": json.loads(r["changed_fields_json"] or "[]"),
        } for r in rows]

    def latest_snapshot_before(self, character_name: str, chapter_no: int) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT snapshot_json FROM character_state_snapshots WHERE character_name=? AND chapter_no<? "
            "ORDER BY chapter_no DESC LIMIT 1", (character_name, chapter_no)).fetchone()
        return json.loads(row["snapshot_json"] or "{}") if row else None

    def upsert_codex(self, *, codex_id: str, name: str, type_: str, kind: str, summary: str,
                     evidence_chapter: int, evidence_excerpt: str) -> None:
        existing = self.conn.execute("SELECT first_appeared FROM settings_codex WHERE codex_id=?",
                                     (codex_id,)).fetchone()
        first = existing["first_appeared"] if existing else evidence_chapter
        self.conn.execute(
            """INSERT OR REPLACE INTO settings_codex
                 (codex_id, project_id, name, type, kind, summary, evidence_chapter,
                  evidence_excerpt, first_appeared, last_updated)
               VALUES (?, '', ?, ?, ?, ?, ?, ?, ?, ?)""",
            (codex_id, name, type_, kind, summary, evidence_chapter, evidence_excerpt,
             first, evidence_chapter),
        )
        self._commit()

    def list_codex(self) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM settings_codex ORDER BY first_appeared, name").fetchall()
        return [{
            "codex_id": r["codex_id"], "name": r["name"], "type": r["type"], "kind": r["kind"],
            "summary": r["summary"], "evidence_chapter": r["evidence_chapter"],
            "evidence_excerpt": r["evidence_excerpt"], "first_appeared": r["first_appeared"],
        } for r in rows]

    def upsert_story_arc(self, *, arc_id: str, name: str, theme: str, key_events: list[str],
                         turning_points: list[str], journey_summary: str, resolution_status: str) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO story_arcs
                 (arc_id, project_id, name, theme, key_events_json, turning_points_json,
                  journey_summary, resolution_status)
               VALUES (?, '', ?, ?, ?, ?, ?, ?)""",
            (arc_id, name, theme, json.dumps(key_events, ensure_ascii=False),
             json.dumps(turning_points, ensure_ascii=False), journey_summary, resolution_status),
        )
        self._commit()

    def list_story_arcs(self) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM story_arcs ORDER BY arc_id").fetchall()
        return [{
            "arc_id": r["arc_id"], "name": r["name"], "theme": r["theme"],
            "key_events": json.loads(r["key_events_json"] or "[]"),
            "turning_points": json.loads(r["turning_points_json"] or "[]"),
            "journey_summary": r["journey_summary"], "resolution_status": r["resolution_status"],
        } for r in rows]

    def insert_foreshadow(self, *, setup_id: str, chapter_no: int, excerpt: str, what_planted: str,
                          why_suspect: str, salience: float) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO foreshadow_setups
                 (setup_id, project_id, chapter_no, excerpt, what_planted, why_suspect, salience, status)
               VALUES (?, '', ?, ?, ?, ?, ?, 'pending')""",
            (setup_id, chapter_no, excerpt, what_planted, why_suspect, float(salience)),
        )
        self._commit()

    def update_foreshadow_pairing(self, *, setup_id: str, status: str, payoff_event_id: str = "",
                                  payoff_chapter: int = 0, confidence: float = 0.0, reason: str = "") -> None:
        self.conn.execute(
            """UPDATE foreshadow_setups SET status=?, payoff_event_id=?, payoff_chapter=?,
                 confidence=?, reason=? WHERE setup_id=?""",
            (status, payoff_event_id, payoff_chapter, float(confidence), reason, setup_id),
        )
        self._commit()

    def list_foreshadows(self, status: str | None = None) -> list[dict[str, Any]]:
        if status is None:
            rows = self.conn.execute("SELECT * FROM foreshadow_setups ORDER BY chapter_no, setup_id").fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM foreshadow_setups WHERE status=? ORDER BY chapter_no", (status,)).fetchall()
        return [{
            "setup_id": r["setup_id"], "chapter_no": r["chapter_no"], "excerpt": r["excerpt"],
            "what_planted": r["what_planted"], "why_suspect": r["why_suspect"], "salience": r["salience"],
            "status": r["status"], "payoff_event_id": r["payoff_event_id"],
            "payoff_chapter": r["payoff_chapter"], "confidence": r["confidence"], "reason": r["reason"],
        } for r in rows]

    def upsert_continuation_job(self, job: ContinuationJobRecord) -> None:
        self.conn.execute(
            """INSERT INTO continuation_jobs
                 (id, project_id, phase, progress, total, status, error, config_json, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 project_id=excluded.project_id,
                 phase=excluded.phase,
                 progress=excluded.progress,
                 total=excluded.total,
                 status=excluded.status,
                 error=excluded.error,
                 config_json=excluded.config_json,
                 created_at=excluded.created_at,
                 updated_at=excluded.updated_at""",
            (
                job.id,
                job.project_id,
                job.phase,
                job.progress,
                job.total,
                job.status,
                job.error,
                json.dumps(job.config_json, ensure_ascii=False),
                job.created_at,
                job.updated_at,
            ),
        )
        self._commit()

    def get_continuation_job(self, job_id: str) -> ContinuationJobRecord | None:
        row = self.conn.execute("SELECT * FROM continuation_jobs WHERE id=?", (job_id,)).fetchone()
        return _row_to_continuation_job(row) if row else None

    def latest_continuation_job(self) -> ContinuationJobRecord | None:
        row = self.conn.execute(
            "SELECT * FROM continuation_jobs ORDER BY updated_at DESC, created_at DESC, id DESC LIMIT 1"
        ).fetchone()
        return _row_to_continuation_job(row) if row else None

    def list_continuation_jobs(self) -> list[ContinuationJobRecord]:
        rows = self.conn.execute(
            "SELECT * FROM continuation_jobs ORDER BY updated_at DESC, created_at DESC, id DESC"
        ).fetchall()
        return [_row_to_continuation_job(r) for r in rows]

    def insert_source_chunk(self, chunk: SourceChunk) -> int:
        cur = self.conn.execute(
            """INSERT INTO source_chunks
                 (project_id, chapter_id, chunk_no, text, summary, embedding_key)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (chunk.project_id, chunk.chapter_id, chunk.chunk_no, chunk.text, chunk.summary, chunk.embedding_key),
        )
        self._commit()
        return int(cur.lastrowid)

    def list_source_chunks(self, chapter_id: int | None = None) -> list[SourceChunk]:
        if chapter_id is None:
            rows = self.conn.execute("SELECT * FROM source_chunks ORDER BY chapter_id, chunk_no, id").fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM source_chunks WHERE chapter_id=? ORDER BY chunk_no, id", (chapter_id,)
            ).fetchall()
        return [_row_to_source_chunk(r) for r in rows]

    def create_chapter_draft(self, draft: ChapterDraftRecord) -> int:
        cur = self.conn.execute(
            """INSERT INTO chapter_drafts
                 (project_id, chapter_no, title, outline, prose, guidance, target_words,
                  mode, status, context_snapshot_json, candidate_group_id,
                  style_packet_json, score_breakdown_json, retrieved_segment_ids_json,
                  revision_history_json, created_at, accepted_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                draft.project_id,
                draft.chapter_no,
                draft.title,
                draft.outline,
                draft.prose,
                draft.guidance,
                draft.target_words,
                draft.mode,
                draft.status,
                json.dumps(draft.context_snapshot_json, ensure_ascii=False),
                draft.candidate_group_id,
                json.dumps(draft.style_packet_json, ensure_ascii=False),
                json.dumps(draft.score_breakdown_json, ensure_ascii=False),
                json.dumps(draft.retrieved_segment_ids_json, ensure_ascii=False),
                json.dumps(draft.revision_history_json, ensure_ascii=False),
                draft.created_at,
                draft.accepted_at,
            ),
        )
        self._commit()
        return int(cur.lastrowid)

    def update_chapter_draft_status(self, draft_id: int, status: str, accepted_at: str = "") -> None:
        self.conn.execute(
            "UPDATE chapter_drafts SET status=?, accepted_at=? WHERE id=?",
            (status, accepted_at, draft_id),
        )
        self._commit()

    def update_chapter_draft_snapshot(self, draft_id: int, snapshot: dict[str, Any]) -> None:
        self.conn.execute(
            "UPDATE chapter_drafts SET context_snapshot_json=? WHERE id=?",
            (json.dumps(snapshot or {}, ensure_ascii=False), draft_id),
        )
        self._commit()

    def get_chapter_draft(self, draft_id: int) -> ChapterDraftRecord | None:
        row = self.conn.execute("SELECT * FROM chapter_drafts WHERE id=?", (draft_id,)).fetchone()
        return _row_to_chapter_draft_record(row) if row else None

    def list_chapter_drafts(self, status: str | None = None) -> list[ChapterDraftRecord]:
        if status is None:
            rows = self.conn.execute("SELECT * FROM chapter_drafts ORDER BY id DESC").fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM chapter_drafts WHERE status=? ORDER BY id DESC", (status,)
            ).fetchall()
        return [_row_to_chapter_draft_record(r) for r in rows]

    def list_visible_chapter_drafts(self) -> list[ChapterDraftRecord]:
        """Hide obsolete blocked/rejected history once that chapter is accepted."""
        rows = self.conn.execute(
            """
            SELECT draft.*
              FROM chapter_drafts AS draft
             WHERE NOT (
                 draft.status IN ('blocked', 'rejected', 'rejected_invalid_scope')
                 AND EXISTS (
                     SELECT 1
                       FROM accepted_chapters AS accepted
                      WHERE accepted.chapter_no = draft.chapter_no
                 )
             )
             ORDER BY draft.id DESC
            """
        ).fetchall()
        return [_row_to_chapter_draft_record(r) for r in rows]

    def insert_accepted_chapter(self, chapter: AcceptedChapterRecord) -> int:
        cur = self.conn.execute(
            """INSERT INTO accepted_chapters
                 (project_id, draft_id, chapter_no, title, prose, summary, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (chapter.project_id, chapter.draft_id, chapter.chapter_no,
             chapter.title, chapter.prose, chapter.summary, chapter.created_at),
        )
        self._commit()
        return int(cur.lastrowid)

    def list_accepted_chapters(self) -> list[AcceptedChapterRecord]:
        rows = self.conn.execute("SELECT * FROM accepted_chapters ORDER BY chapter_no, id").fetchall()
        return [_row_to_accepted_chapter_record(r) for r in rows]

    def latest_accepted_chapter(self) -> AcceptedChapterRecord | None:
        row = self.conn.execute(
            "SELECT * FROM accepted_chapters ORDER BY chapter_no DESC, id DESC LIMIT 1"
        ).fetchone()
        return _row_to_accepted_chapter_record(row) if row else None


# ---------- row 鈫?dataclass 杈呭姪 ----------
def _row_to_card(r) -> CharacterCard:
    return CharacterCard(
        card_id=r["card_id"], agent_id=r["agent_id"], tier=r["tier"], slot_key=r["slot_key"],
        name=r["name"], one_liner=r["one_liner"], voice_register=r["voice_register"],
        defining_trait=r["defining_trait"], core_desire=r["core_desire"],
        verbal_habits=r["verbal_habits"], key_relation=r["key_relation"], backstory=r["backstory"],
        fatal_flaw=r["fatal_flaw"], motif_objects=json.loads(r["motif_objects"]),
        relationship_map=json.loads(r["relationship_map"]), arc=r["arc"],
        appearance=(r["appearance"] if "appearance" in r.keys() else ""),
        social_role=(r["social_role"] if "social_role" in r.keys() else ""),
        psychology=(r["psychology"] if "psychology" in r.keys() else ""),
        created_at=r["created_at"],
        foreshadow_from=(r["foreshadow_from"] if "foreshadow_from" in r.keys() else 0),
        reveal_chapter=(r["reveal_chapter"] if "reveal_chapter" in r.keys() else 0),
        secret_reveal_chapter=(
            r["secret_reveal_chapter"] if "secret_reveal_chapter" in r.keys() else 0
        ),
        foreshadow_hint=(r["foreshadow_hint"] if "foreshadow_hint" in r.keys() else ""),
        secret_truth=(r["secret_truth"] if "secret_truth" in r.keys() else ""),
    )


def _row_to_part(r) -> Part:
    return Part(
        part_id=r["part_id"],
        sequence_order=r["sequence_order"],
        title=r["title"],
        goal=r["goal"],
        region=r["region"],
        key_twist=(r["key_twist"] if "key_twist" in r.keys() else ""),
        new_crisis_hook=(r["new_crisis_hook"] if "new_crisis_hook" in r.keys() else ""),
        reveal_node_ids=json.loads(r["reveal_node_ids"]),
        status=r["status"],
    )


def _row_to_arc(r) -> Arc:
    return Arc(
        arc_id=r["arc_id"],
        part_id=r["part_id"],
        sequence_order=r["sequence_order"],
        title=r["title"],
        summary=r["summary"],
        target_chapters=r["target_chapters"],
        focus_agents=json.loads(r["focus_agents"]),
        status=r["status"],
    )


def _row_to_chapter_plan(r) -> ChapterPlan:
    return ChapterPlan(
        chapter_id=r["chapter_id"],
        arc_id=r["arc_id"],
        sequence_order=r["sequence_order"],
        title=r["title"],
        cast=json.loads(r["cast"]),
        location_ids=json.loads(r["location_ids"]),
        available_items=json.loads(r["available_items"]),
        items_present=json.loads(r["items_present"]),
        items_introduced=json.loads(r["items_introduced"]),
        items_consumed=json.loads(r["items_consumed"]),
        beat_goals=json.loads(r["beat_goals"]),
        beat_povs=(json.loads(r["beat_povs"]) if "beat_povs" in r.keys() and r["beat_povs"] else []),
        reveal_gate=json.loads(r["reveal_gate"]),
        must_happen=(json.loads(r["must_happen"]) if "must_happen" in r.keys() and r["must_happen"] else []),
        required_exit_state=(r["required_exit_state"] if "required_exit_state" in r.keys() else ""),
        scene_flow=(json.loads(r["scene_flow"]) if "scene_flow" in r.keys() and r["scene_flow"] else []),
        allowed_entity_ids=(json.loads(r["allowed_entity_ids"]) if "allowed_entity_ids" in r.keys() and r["allowed_entity_ids"] else []),
        allowed_fact_ids=(json.loads(r["allowed_fact_ids"]) if "allowed_fact_ids" in r.keys() and r["allowed_fact_ids"] else []),
        forbidden=(json.loads(r["forbidden"]) if "forbidden" in r.keys() and r["forbidden"] else []),
        item_sources=(json.loads(r["item_sources"]) if "item_sources" in r.keys() and r["item_sources"] else {}),
        package_version=(r["package_version"] if "package_version" in r.keys() else 1),
        thread_decisions_json=(json.loads(r["thread_decisions_json"]) if "thread_decisions_json" in r.keys() and r["thread_decisions_json"] else []),
        knowledge_delta=json.loads(r["knowledge_delta"]),
        summary=r["summary"],
        scene_ids=json.loads(r["scene_ids"]),
        target_scenes=r["target_scenes"],
        role=r["role"],
        target_tension=r["target_tension"],
        dramatic_question=r["dramatic_question"],
        resolution_predicate=r["resolution_predicate"],
        min_scenes=r["min_scenes"],
        target_words=r["target_words"],
        ending_hook=r["ending_hook"],
        hook_type=r["hook_type"],
        pov_agent=(r["pov_agent"] if "pov_agent" in r.keys() else ""),
        exit_state=(r["exit_state"] if "exit_state" in r.keys() else ""),
        audited=(r["audited"] if "audited" in r.keys() else 0),
        conflict_type=(r["conflict_type"] if "conflict_type" in r.keys() else ""),
        time_hint=(r["time_hint"] if "time_hint" in r.keys() and r["time_hint"] else ""),
        status=r["status"],
    )


def _merge_text(old: str, new: str) -> str:
    old = (old or "").strip()
    new = (new or "").strip()
    if not old:
        return new
    if not new or new in old:
        return old
    return f"{old}；{new}"


def _row_to_character_log(r) -> CharacterChapterLog:
    return CharacterChapterLog(
        agent_id=r["agent_id"],
        chapter_seq=r["chapter_seq"],
        actions=r["actions"],
        psychology=r["psychology"],
        intention=r["intention"],
        items_changed=json.loads(r["items_changed"] or "[]"),
    )


def _row_to_scene_anchor(r) -> SceneAnchor:
    return SceneAnchor(
        scene_id=r["scene_id"],
        name=r["name"],
        kind=r["kind"],
        location_id=r["location_id"],
        canonical_facts=json.loads(r["canonical_facts"] or "[]"),
        aliases=json.loads(r["aliases"] or "[]"),
        established_chapter=r["established_chapter"],
        created_at=r["created_at"],
    )


def _row_to_batch_audit(r) -> BatchAudit:
    return BatchAudit(
        chapter_seq=r["chapter_seq"],
        result_json=json.loads(r["result_json"] or "{}"),
        summary_json=json.loads(r["summary_json"] or "{}"),
        created_tick=r["created_tick"],
    )


def _row_to_location(r) -> Location:
    keys = r.keys() if hasattr(r, "keys") else []
    return Location(
        loc_id=r["loc_id"],
        part_id=r["part_id"],
        name=r["name"],
        geo_full=r["geo_full"],
        connects_to=json.loads(r["connects_to"]),
        controlling_faction=r["controlling_faction"],
        notable_items=json.loads(r["notable_items"]),
        level=r["level"] if "level" in keys else "",
        parent=r["parent"] if "parent" in keys else "",
        culture_local=r["culture_local"] if "culture_local" in keys else "",
        summary=r["summary"] if "summary" in keys else "",
        detail=r["detail"] if "detail" in keys else "",
        foreshadow_from=r["foreshadow_from"] if "foreshadow_from" in keys else 0,
        reveal_chapter=r["reveal_chapter"] if "reveal_chapter" in keys else 0,
        secret_reveal_chapter=(
            r["secret_reveal_chapter"] if "secret_reveal_chapter" in keys else 0
        ),
        foreshadow_hint=r["foreshadow_hint"] if "foreshadow_hint" in keys else "",
        secret_truth=r["secret_truth"] if "secret_truth" in keys else "",
    )


def _row_to_edge(r) -> GraphEdge:
    return GraphEdge(
        src=r["src"], rel=r["rel"], dst=r["dst"],
        meta=json.loads(r["meta"]),
        since_chapter=r["since_chapter"],
        until_chapter=r["until_chapter"],
        intensity=r["intensity"],
        last_active_chapter=r["last_active_chapter"],
    )


def _row_to_faction(r) -> Faction:
    keys = r.keys() if hasattr(r, "keys") else []
    return Faction(
        faction_id=r["faction_id"],
        name=r["name"],
        ideology=r["ideology"],
        goals=r["goals"],
        methods=r["methods"],
        territory=json.loads(r["territory"]),
        structure=r["structure"],
        key_members=json.loads(r["key_members"]),
        history=r["history"],
        relations=json.loads(r["relations"]),
        secret=r["secret"],
        summary=r["summary"],
        detail=r["detail"],
        source=r["source"],
        created_at=r["created_at"],
        foreshadow_from=r["foreshadow_from"] if "foreshadow_from" in keys else 0,
        reveal_chapter=r["reveal_chapter"] if "reveal_chapter" in keys else 0,
        secret_reveal_chapter=(
            r["secret_reveal_chapter"] if "secret_reveal_chapter" in keys else 0
        ),
        foreshadow_hint=r["foreshadow_hint"] if "foreshadow_hint" in keys else "",
        secret_truth=r["secret_truth"] if "secret_truth" in keys else "",
    )


def _row_to_inventory(r) -> InventoryItem:
    return InventoryItem(
        object_id=r["object_id"],
        holder_agent_id=r["holder_agent_id"],
        status=r["status"],
        acquired_chapter=r["acquired_chapter"],
        note=r["note"],
    )


def _row_to_reveal_node(r) -> RevealNode:
    return RevealNode(
        node_id=r["node_id"],
        fact_id=r["fact_id"],
        kind=r["kind"],
        sequence_order=r["sequence_order"],
        prereq_node_ids=json.loads(r["prereq_node_ids"]),
        part_id=r["part_id"],
        description=r["description"],
        discovered=bool(r["discovered"]),
        discovered_chapter=r["discovered_chapter"],
    )


def _row_to_foreshadow(r) -> Foreshadow:
    return Foreshadow(
        foreshadow_id=r["foreshadow_id"],
        question=r["question"],
        linked_fact_id=r["linked_fact_id"],
        planted_discourse_pos=r["planted_discourse_pos"],
        must_resolve=bool(r["must_resolve"]),
        target_payoff_beat=r["target_payoff_beat"],
        status=r["status"],
        payoff_discourse_pos=r["payoff_discourse_pos"],
    )


def _row_to_fact(r) -> Fact:
    return Fact(
        fact_id=r["fact_id"],
        fact_type=r["fact_type"],
        canonical_content=r["canonical_content"],
        structured=json.loads(r["structured"]),
        story_time=r["story_time"],
        location_id=r["location_id"],
        involved_entities=json.loads(r["involved_entities"]),
        source_event_id=r["source_event_id"],
    )


def _row_to_event(r) -> Event:
    return Event(
        event_id=r["event_id"],
        story_time=r["story_time"],
        actors=json.loads(r["actors"]),
        action_type=r["action_type"],
        payload=json.loads(r["payload"]),
        location_id=r["location_id"],
        perceivers=json.loads(r["perceivers"]),
        beat_id=r["beat_id"],
        story_clock=(r["story_clock"] if "story_clock" in r.keys() and r["story_clock"] is not None else None),
    )


def _row_to_source_document(r) -> SourceDocument:
    return SourceDocument(
        id=r["id"],
        project_id=r["project_id"],
        filename=r["filename"],
        format=r["format"],
        raw_text=r["raw_text"],
        created_at=r["created_at"],
    )


def _row_to_source_chapter(r) -> SourceChapter:
    return SourceChapter(
        id=r["id"],
        project_id=r["project_id"],
        source_document_id=r["source_document_id"],
        chapter_no=r["chapter_no"],
        title=r["title"],
        text=r["text"],
        word_count=r["word_count"],
        summary=r["summary"],
        created_at=r["created_at"],
    )


def _row_to_source_chunk(r) -> SourceChunk:
    return SourceChunk(
        id=r["id"],
        project_id=r["project_id"],
        chapter_id=r["chapter_id"],
        chunk_no=r["chunk_no"],
        text=r["text"],
        summary=r["summary"],
        embedding_key=r["embedding_key"],
    )


def _row_to_style_segment(r) -> StyleSegment:
    return StyleSegment(
        id=r["id"],
        project_id=r["project_id"],
        source_chapter_id=r["source_chapter_id"],
        start_offset=r["start_offset"],
        end_offset=r["end_offset"],
        text=r["text"],
        voice_type=r["voice_type"],
        character_id=r["character_id"],
        pov_character_id=r["pov_character_id"],
        discourse_type=r["discourse_type"],
        scene_type=r["scene_type"],
        emotion_json=json.loads(r["emotion_json"] or "[]"),
        register_type=r["register_type"],
        feature_json=json.loads(r["feature_json"] or "{}"),
        embedding_key=r["embedding_key"],
        quality_score=r["quality_score"],
        annotation_confidence=r["annotation_confidence"],
        enabled=bool(r["enabled"]),
    )


def _row_to_style_cluster(r) -> StyleCluster:
    return StyleCluster(
        id=r["id"],
        project_id=r["project_id"],
        cluster_type=r["cluster_type"],
        label=r["label"],
        centroid_key=r["centroid_key"],
        feature_summary_json=json.loads(r["feature_summary_json"] or "{}"),
        representative_segment_ids_json=json.loads(r["representative_segment_ids_json"] or "[]"),
    )


def _row_to_style_negative_sample(r) -> StyleNegativeSample:
    return StyleNegativeSample(
        id=r["id"],
        project_id=r["project_id"],
        text=r["text"],
        failure_types_json=json.loads(r["failure_types_json"] or "[]"),
        related_source_segment_ids_json=json.loads(r["related_source_segment_ids_json"] or "[]"),
        score_json=json.loads(r["score_json"] or "{}"),
        created_at=r["created_at"],
    )


def _row_to_author_experience_source(r) -> AuthorExperienceSource:
    return AuthorExperienceSource(
        source_id=r["source_id"],
        project_id=r["project_id"],
        label=r["label"],
        source_type=r["source_type"],
        path=r["path"],
        content_hash=r["content_hash"],
        enabled=bool(r["enabled"]),
        created_at=r["created_at"],
    )


def _row_to_author_experience_fragment(r) -> AuthorExperienceFragment:
    return AuthorExperienceFragment(
        fragment_id=r["fragment_id"],
        project_id=r["project_id"],
        source_id=r["source_id"],
        fragment_index=r["fragment_index"],
        title_hint=r["title_hint"],
        text=r["text"],
        tags_json=json.loads(r["tags_json"] or "[]"),
        emotion_json=json.loads(r["emotion_json"] or "[]"),
        self_schema_json=json.loads(r["self_schema_json"] or "{}"),
        confidence=float(r["confidence"] or 0.0),
    )


def _row_to_author_life_model(r) -> AuthorLifeModel:
    return AuthorLifeModel(
        model_id=r["model_id"],
        project_id=r["project_id"],
        source_ids_json=json.loads(r["source_ids_json"] or "[]"),
        source_label=r["source_label"],
        summary=r["summary"],
        core_wound_json=json.loads(r["core_wound_json"] or "{}"),
        defense_patterns_json=json.loads(r["defense_patterns_json"] or "[]"),
        desire_vectors_json=json.loads(r["desire_vectors_json"] or "[]"),
        relationship_model_json=json.loads(r["relationship_model_json"] or "{}"),
        narrative_engines_json=json.loads(r["narrative_engines_json"] or "[]"),
        prose_rules_json=json.loads(r["prose_rules_json"] or "{}"),
        worldview_json=json.loads(r["worldview_json"] or "{}"),
        evidence_json=json.loads(r["evidence_json"] or "[]"),
        confidence_json=json.loads(r["confidence_json"] or "{}"),
        persona_prompt=r["persona_prompt"],
        created_at=r["created_at"],
    )


def _row_to_naming_profile(r) -> NamingProfile:
    payload = json.loads(r["profile_json"] or "{}")
    return NamingProfile(
        profile_id=r["profile_id"],
        scope=payload.get("scope", r["scope"]),
        label=payload.get("label", r["label"]),
        genre=payload.get("genre", ""),
        culture_source=payload.get("culture_source", "zh"),
        phonology_style=payload.get("phonology_style", "clean_han"),
        primary_length_min=payload.get("primary_length_min", 2),
        primary_length_max=payload.get("primary_length_max", 4),
        allow_surname=bool(payload.get("allow_surname", True)),
        allow_compound_given_name=bool(payload.get("allow_compound_given_name", False)),
        allow_middle_dot=bool(payload.get("allow_middle_dot", False)),
        allow_hyphen=bool(payload.get("allow_hyphen", False)),
        allow_space=bool(payload.get("allow_space", False)),
        nickname_rules=payload.get("nickname_rules", {}) or {},
        honorific_rules=payload.get("honorific_rules", {}) or {},
        faction_variance_policy=payload.get("faction_variance_policy", {}) or {},
        rare_structure_quota=payload.get("rare_structure_quota", {}) or {},
        motif_token_budget=payload.get("motif_token_budget", {}) or {},
        banned_tokens=payload.get("banned_tokens", []) or [],
        danger_tokens=payload.get("danger_tokens", []) or [],
        stopwords_for_primary=payload.get("stopwords_for_primary", []) or [],
        version=r["active_version"],
    )


def _row_to_culture_naming_style(r) -> CultureNamingStyle:
    payload = json.loads(r["style_json"] or "{}")
    return CultureNamingStyle(
        style_id=r["style_id"],
        profile_id=r["profile_id"],
        culture_id=r["culture_id"],
        culture_name=r["culture_name"],
        parent_style_id=payload.get("parent_style_id"),
        surname_pool=payload.get("surname_pool", []) or [],
        given_name_pool=payload.get("given_name_pool", []) or [],
        title_pool=payload.get("title_pool", []) or [],
        morphology_templates=payload.get("morphology_templates", []) or [],
        disallowed_templates=payload.get("disallowed_templates", []) or [],
        nickname_patterns=payload.get("nickname_patterns", []) or [],
        honorific_patterns=payload.get("honorific_patterns", []) or [],
        enemy_label_patterns=payload.get("enemy_label_patterns", []) or [],
        symbol_policy=payload.get("symbol_policy", {}) or {},
        style_fingerprint=json.loads(r["fingerprint_json"] or "{}"),
    )


def _row_to_character_name(r) -> CharacterNameRecord:
    return CharacterNameRecord(
        agent_id=r["agent_id"],
        profile_id=r["profile_id"],
        culture_style_id=r["culture_style_id"],
        primary_name=r["primary_name"],
        short_name=r["short_name"],
        nickname=r["nickname"],
        honorific=r["honorific"],
        public_alias=r["public_alias"],
        self_ref=r["self_ref"],
        enemy_label=r["enemy_label"],
        display_name_locked=r["display_name_locked"],
        primary_name_normalized=r["normalized_name"],
        name_parts_json=json.loads(r["name_parts_json"] or "{}"),
        source=r["source"],
        status=r["status"],
        replaced_by_agent_id=r["replaced_by_agent_id"],
        audit_flags=json.loads(r["audit_flags_json"] or "[]"),
        created_at=r["created_at"],
        updated_at=r["updated_at"],
    )


def _row_to_story_bible_record(r) -> StoryBibleRecord:
    return StoryBibleRecord(
        project_id=r["project_id"],
        source_type=r["source_type"],
        title_style_json=json.loads(r["title_style_json"] or "{}"),
        world_config_json=json.loads(r["world_config_json"] or "{}"),
        characters_json=json.loads(r["characters_json"] or "[]"),
        locations_json=json.loads(r["locations_json"] or "[]"),
        factions_json=json.loads(r["factions_json"] or "[]"),
        items_json=json.loads(r["items_json"] or "[]"),
        relationships_json=json.loads(r["relationships_json"] or "[]"),
        timeline_json=json.loads(r["timeline_json"] or "[]"),
        open_threads_json=json.loads(r["open_threads_json"] or "[]"),
        last_state_json=json.loads(r["last_state_json"] or "{}"),
        narrative_constraints_json=json.loads(r["narrative_constraints_json"] or "{}"),
        style_profile_id=r["style_profile_id"],
        updated_at=r["updated_at"],
    )


def _row_to_continuation_job(r) -> ContinuationJobRecord:
    return ContinuationJobRecord(
        id=r["id"],
        project_id=r["project_id"],
        phase=r["phase"],
        progress=r["progress"],
        total=r["total"],
        status=r["status"],
        error=r["error"],
        config_json=json.loads(r["config_json"] or "{}"),
        created_at=r["created_at"],
        updated_at=r["updated_at"],
    )


def _row_to_writing_settings(r) -> WritingSettings:
    return WritingSettings(
        project_id=r["project_id"],
        target_words=r["target_words"],
        min_words=r["min_words"],
        max_words=r["max_words"],
        outline_first=bool(r["outline_first"]),
        auto_chapter_count=r["auto_chapter_count"],
        require_human_acceptance=bool(r["require_human_acceptance"]) if "require_human_acceptance" in r.keys() else True,
        style_profile_id=r["style_profile_id"],
    )


def _row_to_chapter_draft_record(r) -> ChapterDraftRecord:
    return ChapterDraftRecord(
        id=r["id"],
        project_id=r["project_id"],
        chapter_no=r["chapter_no"],
        title=r["title"],
        outline=r["outline"],
        prose=r["prose"],
        guidance=r["guidance"],
        target_words=r["target_words"],
        mode=r["mode"],
        status=r["status"],
        context_snapshot_json=json.loads(r["context_snapshot_json"] or "{}"),
        candidate_group_id=(r["candidate_group_id"] if "candidate_group_id" in r.keys() else ""),
        style_packet_json=json.loads(r["style_packet_json"] or "{}") if "style_packet_json" in r.keys() else {},
        score_breakdown_json=json.loads(r["score_breakdown_json"] or "{}") if "score_breakdown_json" in r.keys() else {},
        retrieved_segment_ids_json=json.loads(r["retrieved_segment_ids_json"] or "[]") if "retrieved_segment_ids_json" in r.keys() else [],
        revision_history_json=json.loads(r["revision_history_json"] or "[]") if "revision_history_json" in r.keys() else [],
        created_at=r["created_at"],
        accepted_at=r["accepted_at"],
    )


def _row_to_accepted_chapter_record(r) -> AcceptedChapterRecord:
    return AcceptedChapterRecord(
        id=r["id"],
        project_id=r["project_id"],
        draft_id=r["draft_id"],
        chapter_no=r["chapter_no"],
        title=r["title"],
        prose=r["prose"],
        summary=r["summary"],
        created_at=r["created_at"],
    )
