"""仓储层：对世界状态库的读写。

Append-only 落实点：facts / events 只提供 append_*（INSERT），**不**提供 update/delete。
这是设计文档 §0 原则 2（唯一真相源 + 不可变历史）的代码级保证。
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from .models import (
    Arc,
    BatchAudit,
    Beat,
    ChapterPlan,
    CharacterCard,
    CharacterChapterLog,
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
    StyleProfile,
    Thread,
    ToneProfile,
)


class Repository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

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
        self.conn.commit()

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

    # ---------- locations（§12.3 地点一等实体） ----------
    def upsert_location(self, loc: Location) -> None:
        self.conn.execute(
            """INSERT INTO locations
                 (loc_id, part_id, name, geo_full, connects_to, controlling_faction, notable_items,
                  level, parent, culture_local, summary, detail)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(loc_id) DO UPDATE SET
                 part_id=excluded.part_id, name=excluded.name, geo_full=excluded.geo_full,
                 connects_to=excluded.connects_to,
                 controlling_faction=excluded.controlling_faction,
                 notable_items=excluded.notable_items,
                 level=excluded.level, parent=excluded.parent,
                 culture_local=excluded.culture_local,
                 summary=excluded.summary, detail=excluded.detail""",
            (loc.loc_id, loc.part_id, loc.name, loc.geo_full,
             json.dumps(loc.connects_to, ensure_ascii=False), loc.controlling_faction,
             json.dumps(loc.notable_items, ensure_ascii=False),
             loc.level, loc.parent, loc.culture_local, loc.summary, loc.detail),
        )
        self.conn.commit()

    def enrich_location(self, loc_id: str, summary: str, detail: str,
                        culture_local: str, level: str = "", parent: str = "",
                        geo_full: str = "") -> None:
        """W2：丰富化地点（只更新 W2 字段，geo_full 非空时也一并更新）。"""
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
        self.conn.commit()

    def get_location(self, loc_id: str) -> Location | None:
        r = self.conn.execute("SELECT * FROM locations WHERE loc_id=?", (loc_id,)).fetchone()
        return _row_to_location(r) if r else None

    def list_locations(self, part_id: str | None = None) -> list[Location]:
        if part_id is None:
            rows = self.conn.execute("SELECT * FROM locations").fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM locations WHERE part_id=?", (part_id,)).fetchall()
        return [_row_to_location(r) for r in rows]

    # ---------- world_bible_sections（§12 全量存档，不摘要；W1 加两级 summary/detail） ----------
    def add_bible_section(self, section: str, title: str, body_full: str,
                          source: str = "user", created_at: int = 0, summary: str = "") -> None:
        """逐字保存一节世界圣经原文（永不覆写，重复 section 允许多条）。空 body 不入库。
        W1：可选 summary（仅 source='w1' 行填，作常驻注入的一两句速览）。"""
        if not (body_full or "").strip():
            return
        self.conn.execute(
            """INSERT INTO world_bible_sections (section, title, body_full, summary, source, created_at)
               VALUES (?,?,?,?,?,?)""",
            (section, title, body_full, summary, source, created_at),
        )
        self.conn.commit()

    def upsert_w1_section(self, section: str, title: str, summary: str, detail: str) -> None:
        """W1 权威两级条目：每节唯一一条 source='w1' 行（summary+detail 全文）。
        先删本节旧 w1 行再插 → 修订/重跑替换不堆积；不动 user/llm_expanded 原始档案。"""
        if not (detail or "").strip():
            return
        self.conn.execute(
            "DELETE FROM world_bible_sections WHERE section=? AND source='w1'", (section,))
        self.conn.execute(
            """INSERT INTO world_bible_sections (section, title, body_full, summary, source, created_at)
               VALUES (?,?,?,?,'w1',0)""",
            (section, title, detail, summary),
        )
        self.conn.commit()

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
        """W1 常驻注入：拼接各节 source='w1' 行的 summary（全世界观速览，token 极省）。
        无 w1 行时返回空串（旧项目/未跑 build_world_skill 时退化为不注入）。"""
        rows = [r for r in self.list_bible_sections()
                if r["source"] == "w1" and (r["summary"] or "").strip()]
        if sections:
            want = set(sections)
            rows = [r for r in rows if r["section"] in want]
        return "\n".join(f"· {r['title'] or r['section']}：{r['summary'].strip()}" for r in rows)

    def bible_sections_text(self, sections: list[str] | None = None, max_chars: int = 4000) -> str:
        """§12 检索而非概括：取相关分节的**全文**拼成提示词上下文（不摘要）。
        sections 给定时只取这些节；否则取全部。总量裁到 max_chars 防爆 token。
        W1：某节若有权威 w1 detail，则该节**只取 w1(+w1_deepened) 行**（厚且自洽），
        丢弃同节的 user/llm_expanded 薄原文，避免厚薄混杂重复。无 w1 行的节维持原行为。"""
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
        self.conn.commit()

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
        """合并更新实体 attributes（用于固化道具设定 canon_detail 等）。"""
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
        self.conn.commit()

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

    # ---------- facts（append-only） ----------
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
        self.conn.commit()

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

    # ---------- events（append-only；drama_score 为后置标注，见 §1.2/§4.1） ----------
    def append_event(self, ev: Event) -> None:
        self.conn.execute(
            """INSERT INTO events
                 (event_id, story_time, actors, action_type, payload,
                  location_id, perceivers, beat_id)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                ev.event_id,
                ev.story_time,
                json.dumps(ev.actors, ensure_ascii=False),
                ev.action_type,
                json.dumps(ev.payload, ensure_ascii=False),
                ev.location_id,
                json.dumps(ev.perceivers, ensure_ascii=False),
                ev.beat_id,
            ),
        )
        self.conn.commit()

    def list_events(self) -> list[Event]:
        rows = self.conn.execute("SELECT * FROM events ORDER BY story_time").fetchall()
        return [_row_to_event(r) for r in rows]

    def get_event(self, event_id: str) -> Event | None:
        r = self.conn.execute("SELECT * FROM events WHERE event_id=?", (event_id,)).fetchone()
        return _row_to_event(r) if r else None

    def set_event_drama_score(self, event_id: str, score: float) -> None:
        """后置标注 drama_score（§4.1"可后置计算"）。这是派生标注，非改写历史事实。"""
        self.conn.execute(
            "UPDATE events SET drama_score=? WHERE event_id=?", (score, event_id)
        )
        self.conn.commit()

    def get_event_drama_score(self, event_id: str) -> float | None:
        r = self.conn.execute(
            "SELECT drama_score FROM events WHERE event_id=?", (event_id,)
        ).fetchone()
        return r["drama_score"] if r else None

    def set_event_beat(self, event_id: str, beat_id: str) -> None:
        """给事件打上所属章号（派生标注，规划层用来按章归集事件，非改写历史内容）。"""
        self.conn.execute("UPDATE events SET beat_id=? WHERE event_id=?", (beat_id, event_id))
        self.conn.commit()

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

    # ---------- agent_knowledge（账本） ----------
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
        self.conn.commit()

    def upsert_knowledge(self, k: KnowledgeItem) -> None:
        """覆盖式写入（供记忆巩固 UPDATE 用）。account_knowledge 是可变信念态，非不可变真相。"""
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
        self.conn.commit()

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
        self.conn.commit()

    def get_agent_ledger(self, agent_id: str) -> list[KnowledgeItem]:
        """隔离的核心：只返回该 agent 自己的账本条目。"""
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

    # ---------- persona ----------
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
        self.conn.commit()

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
        # 按插入顺序（= 种子草稿顺序）返回，保证 personas[0]=主角 等假设稳定
        rows = self.conn.execute("SELECT agent_id FROM persona ORDER BY rowid").fetchall()
        return [self.get_persona(r["agent_id"]) for r in rows]  # type: ignore[misc]

    # persona 的 arc_state / cost_ledger 是「状态」而非历史事实，可变（区别于 facts 的不可变）
    def update_arc_state(self, agent_id: str, arc_state: dict[str, Any]) -> None:
        self.conn.execute(
            "UPDATE persona SET arc_state=? WHERE agent_id=?",
            (json.dumps(arc_state, ensure_ascii=False), agent_id),
        )
        self.conn.commit()

    def append_cost(self, agent_id: str, cost: str) -> None:
        p = self.get_persona(agent_id)
        if not p:
            return
        p.cost_ledger.append(cost)
        self.conn.execute(
            "UPDATE persona SET cost_ledger=? WHERE agent_id=?",
            (json.dumps(p.cost_ledger, ensure_ascii=False), agent_id),
        )
        self.conn.commit()

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
        self.conn.commit()

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
        self.conn.commit()

    # ---------- beats（节拍，§1.5） ----------
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
        self.conn.commit()

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

    # ---------- 跨账本查询（叙事落差） ----------
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
        """§1.3 conflict_pairs：两个角色对同一 fact 持不同 version → 人物冲突的种子。"""
        rows = self.conn.execute(
            "SELECT DISTINCT fact_id FROM agent_knowledge"
        ).fetchall()
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

    # ---------- reader_knowledge（读者账本，§1.3） ----------
    def reveal_to_reader(self, rk: ReaderKnowledge) -> None:
        self.conn.execute(
            """INSERT INTO reader_knowledge
                 (fact_id, revealed_version, revealed_discourse_pos, via_pov)
               VALUES (?,?,?,?)
               ON CONFLICT(fact_id) DO NOTHING""",  # 已揭示则不重复（首次揭示为准）
            (rk.fact_id, rk.revealed_version, rk.revealed_discourse_pos, rk.via_pov),
        )
        self.conn.commit()

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
        """读者还不知道的真相（§1.3）→ 悬念/谜题。默认在全部 facts 上算。"""
        candidates = candidate_fact_ids or [f.fact_id for f in self.list_facts()]
        return [fid for fid in candidates if not self.reader_knows(fid)]

    def irony_set(self, pov: str) -> list[str]:
        """读者已知但 POV 角色不知道（§1.3）→ 戏剧反讽。"""
        known_by_pov = {k.fact_id for k in self.get_agent_ledger(pov)}
        return [rk.fact_id for rk in self.list_reader_knowledge() if rk.fact_id not in known_by_pov]

    # ---------- scenes（叙述产物，§1.6） ----------
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
        self.conn.commit()

    def update_scene_prose(self, scene_id: str, prose: str) -> None:
        """审计重渲：只更新某场正文（保留 scene_id/discourse_order/揭示，不破坏阅读顺序与读者账本）。"""
        self.conn.execute("UPDATE scenes SET prose_text=? WHERE scene_id=?", (prose, scene_id))
        self.conn.commit()

    def list_scenes(self) -> list[Scene]:
        rows = self.conn.execute(
            "SELECT * FROM scenes ORDER BY discourse_order"
        ).fetchall()
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

    # ---------- foreshadows（伏笔台账，§1.5） ----------
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
        self.conn.commit()

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

    # ---------- endings（候选结局，§1.1） ----------
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
        self.conn.commit()

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


    # ========== 规划层（大纲驱动；仅新建项目写入，旧项目留空） ==========

    # ---------- parts ----------
    def upsert_part(self, p: Part) -> None:
        self.conn.execute(
            """INSERT INTO parts
                 (part_id, sequence_order, title, goal, region, reveal_node_ids, status)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(part_id) DO UPDATE SET
                 sequence_order=excluded.sequence_order, title=excluded.title,
                 goal=excluded.goal, region=excluded.region,
                 reveal_node_ids=excluded.reveal_node_ids, status=excluded.status""",
            (
                p.part_id,
                p.sequence_order,
                p.title,
                p.goal,
                p.region,
                json.dumps(p.reveal_node_ids, ensure_ascii=False),
                p.status,
            ),
        )
        self.conn.commit()

    def list_parts(self) -> list[Part]:
        rows = self.conn.execute("SELECT * FROM parts ORDER BY sequence_order").fetchall()
        return [_row_to_part(r) for r in rows]

    def get_part(self, part_id: str) -> Part | None:
        r = self.conn.execute("SELECT * FROM parts WHERE part_id=?", (part_id,)).fetchone()
        return _row_to_part(r) if r else None

    def set_part_status(self, part_id: str, status: str) -> None:
        self.conn.execute("UPDATE parts SET status=? WHERE part_id=?", (status, part_id))
        self.conn.commit()

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
        self.conn.commit()

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
        self.conn.commit()

    # ---------- chapter_plans ----------
    def upsert_chapter_plan(self, c: ChapterPlan) -> None:
        self.conn.execute(
            """INSERT INTO chapter_plans
                 (chapter_id, arc_id, sequence_order, title, cast, location_ids,
                  available_items, items_present, items_introduced, items_consumed,
                  beat_goals, reveal_gate, knowledge_delta,
                  summary, scene_ids, target_scenes, role, target_tension,
                  dramatic_question, resolution_predicate, min_scenes, target_words,
                  ending_hook, hook_type, pov_agent, exit_state, audited, conflict_type,
                  beat_povs, status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(chapter_id) DO UPDATE SET
                 arc_id=excluded.arc_id, sequence_order=excluded.sequence_order,
                 title=excluded.title, cast=excluded.cast,
                 location_ids=excluded.location_ids, available_items=excluded.available_items,
                 items_present=excluded.items_present, items_introduced=excluded.items_introduced,
                 items_consumed=excluded.items_consumed,
                 beat_goals=excluded.beat_goals, reveal_gate=excluded.reveal_gate,
                 knowledge_delta=excluded.knowledge_delta, summary=excluded.summary,
                 scene_ids=excluded.scene_ids, target_scenes=excluded.target_scenes,
                 role=excluded.role, target_tension=excluded.target_tension,
                 dramatic_question=excluded.dramatic_question,
                 resolution_predicate=excluded.resolution_predicate,
                 min_scenes=excluded.min_scenes, target_words=excluded.target_words,
                 ending_hook=excluded.ending_hook, hook_type=excluded.hook_type,
                 pov_agent=excluded.pov_agent, exit_state=excluded.exit_state,
                 audited=excluded.audited, conflict_type=excluded.conflict_type,
                 beat_povs=excluded.beat_povs, status=excluded.status""",
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
                c.status,
            ),
        )
        self.conn.commit()

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
        """删除一章及其已写正文（场/事件）。用于"不满意就删，删后大纲可改/重写"。
        删 scenes（source_events 属本章的）→ 删 events（beat_id=本章）→ 删 chapter_plan。
        返回删除计数。注意：删中间已写章会在正文留下空档（用户自担），调用方可选择级联后续章。"""
        ev_ids = {e.event_id for e in self.events_for_beat(chapter_id)}
        n_sc = 0
        if ev_ids:
            for s in self.list_scenes():
                if any(eid in ev_ids for eid in s.source_events):
                    self.conn.execute("DELETE FROM scenes WHERE scene_id=?", (s.scene_id,))
                    n_sc += 1
        self.conn.execute("DELETE FROM events WHERE beat_id=?", (chapter_id,))
        self.conn.execute("DELETE FROM chapter_plans WHERE chapter_id=?", (chapter_id,))
        self.conn.commit()
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
        """当前 active 章；无 active 时取最早一个 planned（待开工的下一章）。"""
        r = self.conn.execute(
            "SELECT * FROM chapter_plans WHERE status='active' ORDER BY sequence_order LIMIT 1"
        ).fetchone()
        if r:
            return _row_to_chapter_plan(r)
        r = self.conn.execute(
            "SELECT * FROM chapter_plans WHERE status='planned' ORDER BY sequence_order LIMIT 1"
        ).fetchone()
        return _row_to_chapter_plan(r) if r else None

    # ---------- inventory（物品归属，可转移/丢失） ----------
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
        self.conn.commit()

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
        """物品是否仍然存在（未被消耗/销毁/献祭）。"""
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
        """Upsert a chapter-level character log, appending fields for repeated scene beats."""
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
        self.conn.commit()

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
        self.conn.commit()

    def latest_batch_audit(self, before_chapter: int | None = None) -> BatchAudit | None:
        sql = "SELECT * FROM batch_audits"
        args: list[Any] = []
        if before_chapter is not None:
            sql += " WHERE chapter_seq < ?"
            args.append(before_chapter)
        sql += " ORDER BY chapter_seq DESC LIMIT 1"
        row = self.conn.execute(sql, args).fetchone()
        return _row_to_batch_audit(row) if row else None

    # ---------- reveal_chain（探索驱动揭示链） ----------
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
        self.conn.commit()

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
        self.conn.commit()

    def unlockable_nodes(self) -> list[RevealNode]:
        """前置全部已 discovered、自身尚未 discovered 的节点 → 当前可被主角"撞到"的下一步。"""
        nodes = self.list_reveal_nodes()
        done = {n.node_id for n in nodes if n.discovered}
        return [
            n for n in nodes
            if not n.discovered and all(p in done for p in n.prereq_node_ids)
        ]

    # ---------- tone_profile（§16 文风契约 / 闸门⓪） ----------
    def get_tone_profile(self) -> ToneProfile:
        r = self.conn.execute("SELECT * FROM tone_profile WHERE id=1").fetchone()
        if not r:
            return ToneProfile()
        return ToneProfile(
            genre=r["genre"], primary_effect=r["primary_effect"], register=r["register"],
            sentence_rhythm=r["sentence_rhythm"],
            diction_do=json.loads(r["diction_do"]), diction_dont=json.loads(r["diction_dont"]),
            device_kit=json.loads(r["device_kit"]), pacing=r["pacing"],
            tension_curve_bias=r["tension_curve_bias"], reveal_cadence=r["reveal_cadence"],
            complexity=r["complexity"], tone_reference=r["tone_reference"],
            confirmed=bool(r["confirmed"]),
            era_logic=json.loads(r["era_logic"]) if ("era_logic" in r.keys() and r["era_logic"]) else {},
        )

    def set_tone_profile(self, p: ToneProfile) -> None:
        """写入/覆盖文风契约。确认后（confirmed=1）拒绝再写，保证全程只读、不漂移。"""
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
            (p.genre, p.primary_effect, p.register, p.sentence_rhythm,
             json.dumps(p.diction_do, ensure_ascii=False),
             json.dumps(p.diction_dont, ensure_ascii=False),
             json.dumps(p.device_kit, ensure_ascii=False), p.pacing,
             p.tension_curve_bias, p.reveal_cadence, p.complexity,
             p.tone_reference, 1 if p.confirmed else 0,
             json.dumps(p.era_logic or {}, ensure_ascii=False)),
        )
        self.conn.commit()

    def confirm_tone_profile(self) -> None:
        """用户确认基调 → 之后只读。"""
        self.conn.execute("UPDATE tone_profile SET confirmed=1 WHERE id=1")
        self.conn.commit()

    # ---------- B0 文风模拟（style_skill，单行表） ----------
    def get_style_skill(self) -> StyleProfile:
        r = self.conn.execute("SELECT * FROM style_skill WHERE id=1").fetchone()
        if not r:
            return StyleProfile()
        return StyleProfile(
            name=r["name"], source=r["source"], register=r["register"], rhythm=r["rhythm"],
            devices=json.loads(r["devices"]), diction_do=json.loads(r["diction_do"]),
            diction_dont=json.loads(r["diction_dont"]), motifs=json.loads(r["motifs"]),
            samples=json.loads(r["samples"]), metrics=json.loads(r["metrics"]),
            enabled=bool(r["enabled"]),
        )

    def set_style_skill(self, p: StyleProfile) -> None:
        self.conn.execute(
            """INSERT INTO style_skill
                 (id, name, source, register, rhythm, devices, diction_do, diction_dont,
                  motifs, samples, metrics, enabled)
               VALUES (1,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 name=excluded.name, source=excluded.source, register=excluded.register,
                 rhythm=excluded.rhythm, devices=excluded.devices, diction_do=excluded.diction_do,
                 diction_dont=excluded.diction_dont, motifs=excluded.motifs,
                 samples=excluded.samples, metrics=excluded.metrics, enabled=excluded.enabled""",
            (p.name, p.source, p.register, p.rhythm,
             json.dumps(p.devices, ensure_ascii=False),
             json.dumps(p.diction_do, ensure_ascii=False),
             json.dumps(p.diction_dont, ensure_ascii=False),
             json.dumps(p.motifs, ensure_ascii=False),
             json.dumps(p.samples, ensure_ascii=False),
             json.dumps(p.metrics, ensure_ascii=False),
             1 if p.enabled else 0),
        )
        self.conn.commit()

    def set_style_skill_enabled(self, enabled: bool) -> None:
        self.conn.execute("UPDATE style_skill SET enabled=? WHERE id=1", (1 if enabled else 0,))
        self.conn.commit()

    def delete_style_skill(self) -> None:
        """删除文风模拟 → 回落到 tone_profile 基线。"""
        self.conn.execute("DELETE FROM style_skill WHERE id=1")
        self.conn.commit()

    def style_skill_prompt(self) -> str:
        """B0 安全注入块：仿某文风写作，但只学腔调、严禁照搬样例的具体内容。
        无启用的 style_skill 则空（回落 tone_profile 基线）。"""
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
            lines.append(f"  偏好词：{('、'.join(p.diction_do[:8]) or '—')}；"
                         f"禁忌词：{('、'.join(p.diction_dont[:8]) or '—')}。")
        if p.motifs:
            lines.append("  可呼应的母题意象：" + "、".join(p.motifs[:8]) + "。")
        if p.samples:
            lines.append("[风格样例·仅供模仿腔调，严禁照搬其人物/地点/情节/具体意象]")
            for i, s in enumerate(p.samples[:2], 1):
                lines.append(f"  {'①②'[i - 1]} {str(s)[:150]}")
        lines.append(
            "【硬约束】只学其**腔调、句式、节奏、用词、标点、意象密度**；"
            "样例里的任何具体内容（人名/地名/物件/情节）都**不得**出现在你的正文里——"
            "那是别的故事，你写的是本作的这一拍。")
        return "\n".join(lines)

    def tone_profile_prompt(self) -> str:
        """§16.5 文风前置块：统一插入所有生成提示词最前面。无契约则空。"""
        p = self.get_tone_profile()
        if not p.is_set():
            return ""
        lines = ["【文风契约 · 全程强制遵守，不得漂移】"]
        if p.genre or p.primary_effect:
            lines.append(f"类型：{p.genre or '未定'}；本书每一场都必须交付的主效果：{p.primary_effect or '未定'}。")
        if p.register or p.sentence_rhythm:
            lines.append(f"语域与节奏：{p.register or '未定'}，{p.sentence_rhythm or '未定'}。")
        if p.diction_do:
            lines.append("鼓励：" + "、".join(p.diction_do[:8]) + "。")
        if p.diction_dont:
            lines.append("禁忌（出现即判不合格）：" + "、".join(p.diction_dont[:8]) + "。")
        if p.device_kit:
            lines.append("本类型惯用手法（优先调用）：" + "、".join(p.device_kit[:8]) + "。")
        if p.tone_reference:
            lines.append(f"定调样例（向它的腔调对齐）：{p.tone_reference[:200]}")
        lines.append(
            "【名字与正文语言一致】人名、地名、专有名词的书写必须与正文语言一致："
            "用中文写作时一律用中文名（外来名取音译，如『约翰』『艾琳』），"
            "**不得出现拉丁字母拼写的名字（如 John、Elena）**；用英文写作时才用英文原名。"
        )
        # B0.6 时代隔离墙（主题8）：防止现代对齐把前现代/奇幻角色写成"21 世纪现代人"。
        el = p.era_logic or {}
        if el.get("enabled"):
            seg = ["【时代/语境隔离墙 · 绝对遵守】本作的世界观不是现代社会，角色的认知与逻辑必须落在其时代里。"]
            mi, rl, sl = el.get("moral_index"), el.get("religiosity"), el.get("science_level")
            knobs = []
            if mi is not None:
                knobs.append(f"道德指数 {mi}（越低越残酷、人命越轻）")
            if rl is not None:
                knobs.append(f"宗教/超自然狂热度 {rl}")
            if sl is not None:
                knobs.append(f"科学认知度 {sl}（越低越不懂因果/卫生/心理）")
            if knobs:
                seg.append("时代基调：" + "；".join(knobs) + "。")
            bw = el.get("banned_modern_words") or []
            if bw:
                seg.append("**绝对禁用的现代词汇/概念**（出现即出戏）：" + "、".join(bw[:20])
                           + "——也不得用它们的同义改写或现代心理学/管理学/人权话术。")
            fa = (el.get("forced_attribution") or "").strip()
            if fa:
                seg.append(f"**强制时代逻辑**：{fa}")
            else:
                seg.append("**强制时代逻辑**：角色面对灾难/异象的第一本能是归因于神罚、诅咒、女巫、血统不纯、"
                           "命运等当时代的解释，而不是寻找科学/心理学因果。")
            lines.append("\n".join(seg))
        lines.append("——以下你产出的任何内容，都必须落在这个基调里。")
        return "\n".join(lines)

    # ---------- style_anchor（§4.3 叙述嗓音连续性） ----------
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
        """定调：仅在尚未设定时写入（首场成稿即锁定参考语气）。"""
        self._ensure_style_anchor()
        cur = self.conn.execute("SELECT tone_sample FROM style_anchor WHERE id=1").fetchone()
        if cur and cur["tone_sample"]:
            return
        self.conn.execute("UPDATE style_anchor SET tone_sample=? WHERE id=1", (text[:400],))
        self.conn.commit()

    def add_motifs(self, words: list[str]) -> None:
        self._ensure_style_anchor()
        a = self.get_style_anchor()
        merged = list(dict.fromkeys([*a["motif_lexicon"], *[w for w in words if w]]))
        self.conn.execute(
            "UPDATE style_anchor SET motif_lexicon=? WHERE id=1",
            (json.dumps(merged, ensure_ascii=False),),
        )
        self.conn.commit()

    def add_banned_words(self, words: list[str]) -> None:
        self._ensure_style_anchor()
        a = self.get_style_anchor()
        merged = list(dict.fromkeys([*a["banned_words"], *[w for w in words if w]]))
        self.conn.execute(
            "UPDATE style_anchor SET banned_words=? WHERE id=1",
            (json.dumps(merged, ensure_ascii=False),),
        )
        self.conn.commit()

    def style_anchor_prompt(self) -> str:
        """给叙述者的嗓音对齐块；无内容则空。

        架构修复：**不再注入首场正文片段（tone_sample）作"定调参考"**——LLM 会把那段里的
        场景/地点/道具/句子整段复刻，导致每场都重演同一画面（"同一拍演 4 次"的真因）。
        语气/语域/句感由 tone_profile（register/sentence_rhythm/diction）抽象约束，无需正文样例。
        这里只保留"已用意象（勿堆砌）"与"统一禁用词"这类**不泄漏具体内容**的对齐项。"""
        a = self.get_style_anchor()
        if not (a["motif_lexicon"] or a["banned_words"]):
            return ""
        parts = ["[嗓音一致性]"]
        if a["motif_lexicon"]:
            parts.append("已用核心意象（可少量呼应，但**不要**把它们当本场画面来重复堆砌）："
                         + "、".join(a["motif_lexicon"][:12]))
        if a["banned_words"]:
            parts.append("统一禁用词（不要使用）：" + "、".join(a["banned_words"][:12]))
        return "\n".join(parts)

    # ---------- emotional_state（§4.2 情绪余温） ----------
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
        self.conn.commit()

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
        """供 agent prompt 的【你刚经历的】注入。强度过低视为已平复。"""
        e = self.get_emotion(agent_id)
        if not e or e.intensity < 0.15 or not e.emotion:
            return ""
        because = f"（因{e.cause}）" if e.cause else ""
        return f"你心里还压着一股{e.emotion}{because}，没那么快散。"

    def decay_emotions(self) -> None:
        """每拍调用：所有情绪按各自 decay 衰减；接近平复则清除。"""
        self.conn.execute("UPDATE emotional_state SET intensity = intensity - decay")
        self.conn.execute("DELETE FROM emotional_state WHERE intensity <= 0.05")
        self.conn.commit()

    # ---------- factions（W3 势力一等实体） ----------
    def upsert_faction(self, f: Faction) -> None:
        self.conn.execute(
            """INSERT INTO factions
                 (faction_id, name, ideology, goals, methods, territory, structure,
                  key_members, history, relations, secret, summary, detail, source, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(faction_id) DO UPDATE SET
                 name=excluded.name, ideology=excluded.ideology, goals=excluded.goals,
                 methods=excluded.methods, territory=excluded.territory,
                 structure=excluded.structure, key_members=excluded.key_members,
                 history=excluded.history, relations=excluded.relations,
                 secret=excluded.secret, summary=excluded.summary, detail=excluded.detail,
                 source=excluded.source""",
            (f.faction_id, f.name, f.ideology, f.goals, f.methods,
             json.dumps(f.territory, ensure_ascii=False), f.structure,
             json.dumps(f.key_members, ensure_ascii=False), f.history,
             json.dumps(f.relations, ensure_ascii=False), f.secret,
             f.summary, f.detail, f.source, f.created_at),
        )
        self.conn.commit()

    def get_faction(self, faction_id: str) -> Faction | None:
        r = self.conn.execute("SELECT * FROM factions WHERE faction_id=?", (faction_id,)).fetchone()
        return _row_to_faction(r) if r else None

    def list_factions(self) -> list[Faction]:
        rows = self.conn.execute("SELECT * FROM factions ORDER BY faction_id").fetchall()
        return [_row_to_faction(r) for r in rows]

    def faction_summaries_text(self) -> str:
        """W3 常驻注入：拼接势力 summary（一两句一节，token 极省）。"""
        rows = [f for f in self.list_factions() if (f.summary or "").strip()]
        return "\n".join(f"· {f.name}：{f.summary.strip()}" for f in rows)

    # ---------- graph_edges（W5 知识图谱：静态边 + FactExtractor 增量 + 注意力时变权重） ----------
    def upsert_edge(self, e: GraphEdge) -> None:
        """三元组 (src, rel, dst) 唯一；重复 upsert 替换（unique on conflict replace）。"""
        self.conn.execute(
            """INSERT INTO graph_edges (src, rel, dst, meta, since_chapter, until_chapter,
                                        intensity, last_active_chapter)
               VALUES (?,?,?,?,?,?,?,?)""",
            (e.src, e.rel, e.dst, json.dumps(e.meta, ensure_ascii=False),
             e.since_chapter, e.until_chapter, e.intensity, e.last_active_chapter),
        )
        self.conn.commit()

    def get_edge(self, src: str, rel: str, dst: str) -> GraphEdge | None:
        r = self.conn.execute(
            "SELECT * FROM graph_edges WHERE src=? AND rel=? AND dst=?",
            (src, rel, dst)).fetchone()
        return _row_to_edge(r) if r else None

    def list_edges(self, *, src: str | None = None, dst: str | None = None,
                   rel: str | None = None) -> list[GraphEdge]:
        sql = "SELECT * FROM graph_edges WHERE 1=1"
        args: list = []
        if src:
            sql += " AND src=?"; args.append(src)
        if dst:
            sql += " AND dst=?"; args.append(dst)
        if rel:
            sql += " AND rel=?"; args.append(rel)
        sql += " ORDER BY id"
        return [_row_to_edge(r) for r in self.conn.execute(sql, args).fetchall()]

    def bump_edge_attention(self, src: str, rel: str, dst: str, chapter: int,
                            delta: float = 0.15, meta_patch: dict | None = None) -> None:
        """剧情活跃 → 升 intensity（clamp 0–1）+ 更新 last_active_chapter。
        若边不存在则新建 intensity=0.5+delta。FactExtractor 每场对参与者两两调用。"""
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
            (new_intensity, chapter, json.dumps(merged_meta, ensure_ascii=False),
             src, rel, dst),
        )
        self.conn.commit()

    def decay_edges(self, current_chapter: int, half_life: int = 6,
                    rels: tuple[str, ...] = ("related_to", "knows")) -> int:
        """人物注意力衰减：久未活跃的边 intensity 按章距半衰减。
        只衰减剧情型快变边（related_to/knows 默认）；静态边（member_of/controls/allied 等）不动。"""
        rows = self.conn.execute(
            f"SELECT id, intensity, last_active_chapter FROM graph_edges WHERE rel IN ({','.join('?'*len(rels))})",
            rels).fetchall()
        n = 0
        for r in rows:
            gap = max(0, current_chapter - r["last_active_chapter"])
            if gap == 0:
                continue
            factor = 0.5 ** (gap / max(1, half_life))
            new_i = max(0.0, r["intensity"] * factor)
            self.conn.execute("UPDATE graph_edges SET intensity=? WHERE id=?", (new_i, r["id"]))
            n += 1
        self.conn.commit()
        return n

    def attention_ranked_neighbors(self, seed: str, *, limit: int = 12,
                                    rels: tuple[str, ...] | None = None) -> list[GraphEdge]:
        """W6 检索基础：按 intensity 降序拉 seed 的邻居边（双向）。"""
        sql = "SELECT * FROM graph_edges WHERE (src=? OR dst=?)"
        args: list = [seed, seed]
        if rels:
            sql += f" AND rel IN ({','.join('?'*len(rels))})"
            args += list(rels)
        sql += " ORDER BY intensity DESC, last_active_chapter DESC LIMIT ?"
        args.append(limit)
        return [_row_to_edge(r) for r in self.conn.execute(sql, args).fetchall()]

    # ---------- character_cards（§1 选角层身份卡 + W4 三维度） ----------
    def add_card(self, c: CharacterCard) -> None:
        self.conn.execute(
            """INSERT INTO character_cards
                 (card_id, agent_id, tier, slot_key, name, one_liner, voice_register,
                  defining_trait, core_desire, verbal_habits, key_relation, backstory,
                  fatal_flaw, motif_objects, relationship_map, arc,
                  appearance, social_role, psychology, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(card_id) DO UPDATE SET
                 agent_id=excluded.agent_id, tier=excluded.tier, slot_key=excluded.slot_key,
                 name=excluded.name, one_liner=excluded.one_liner,
                 voice_register=excluded.voice_register, defining_trait=excluded.defining_trait,
                 core_desire=excluded.core_desire, verbal_habits=excluded.verbal_habits,
                 key_relation=excluded.key_relation, backstory=excluded.backstory,
                 fatal_flaw=excluded.fatal_flaw, motif_objects=excluded.motif_objects,
                 relationship_map=excluded.relationship_map, arc=excluded.arc,
                 appearance=excluded.appearance, social_role=excluded.social_role,
                 psychology=excluded.psychology""",
            (
                c.card_id, c.agent_id, c.tier, c.slot_key, c.name, c.one_liner, c.voice_register,
                c.defining_trait, c.core_desire, c.verbal_habits, c.key_relation, c.backstory,
                c.fatal_flaw, json.dumps(c.motif_objects, ensure_ascii=False),
                json.dumps(c.relationship_map, ensure_ascii=False), c.arc,
                c.appearance, c.social_role, c.psychology, c.created_at,
            ),
        )
        self.conn.commit()

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

    # ---------- llm_logs（LLM 对话日志） ----------
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


# ---------- row → dataclass 辅助 ----------
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
    )


def _row_to_part(r) -> Part:
    return Part(
        part_id=r["part_id"],
        sequence_order=r["sequence_order"],
        title=r["title"],
        goal=r["goal"],
        region=r["region"],
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
    )
