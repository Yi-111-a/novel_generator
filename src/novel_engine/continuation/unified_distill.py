from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from ..llm.base import LLMClient
from ..models import Entity, GraphEdge, SourceChapter, StoryBibleRecord, StyleSegment
from ..repository import Repository


UNIFIED_EXTRACT_SYSTEM = """你是长篇小说叙事信息抽取器。下面输入的是连续的完整章节。
必须逐章阅读，只抽取正文明确出现或可直接推出的信息，不补写、不评价、不生成续写。
一次统一输出以下 JSON：
{
  "coverage":{"chapters_received":[],"chapters_processed":[],"possibly_incomplete":[],"warnings":[]},
  "entities":[{"temp_id":"","type":"character|location|faction|object|ability|institution|historical_event",
    "name":"","aliases":[],"first_seen_chapter":0,
    "evidence":{"chapter":0,"quote":""},"confidence":0.0}],
  "events":[{"chapter":0,"order":0,"summary":"","participants":[],"location":"",
    "time_expression":"","cause":"","result":"","importance":"major|minor",
    "evidence":{"chapter":0,"quote":""}}],
  "state_changes":[{"chapter":0,"order":0,"entity":"","field":"",
    "operation":"replace|add|remove|destroy","old_value":null,"new_value":null,
    "reason_event":"","certainty":"explicit|strongly_implied",
    "evidence":{"chapter":0,"quote":""}}],
  "knowledge_assertions":[{"chapter":0,
    "category":"world_rule|history|stable_trait|identity_secret|causal_explanation|unexplained_anomaly|explicit_question|possible_foreshadow|correction",
    "subject":"","claim":"","certainty":"explicit|strongly_implied",
    "evidence":{"chapter":0,"quote":""},"supersedes":[]}],
  "plot_threads":[{"chapter":0,"kind":"open|advance|resolve","question":"",
    "resolution":"","evidence":{"chapter":0,"quote":""},"confidence":0.0}],
  "style_samples":[{"chapter":0,"type":"narration|dialogue|interior|action|description",
    "speaker":"","reason":"","text":"","features":[]}]
}
要求：
1. coverage 必须列出收到和实际处理的全部章节号。
2. 事件保留章内顺序；状态只写变化，不写静态复述。
3. 每条证据不超过80个中文字，并且必须能在对应章节原文中找到。
4. 每章最多选择5个文风样本，每类最多1个；样本只用于风格分析。
5. 宁可标记不确定，也不要把猜测写成事实。
6. 只输出 JSON。"""


CHARACTER_PROFILE_SYSTEM = """你在为一部小说构建"人物档案"，供后续严格续写使用。
输入是程序从原文确定性抽取的人物证据：稳定性格断言、身份秘密、参与事件、状态演算结果、共现伙伴。
只能依据给定证据归纳，严禁虚构原文中没有的信息；证据不足的字段填空字符串或空数组，并相应降低 confidence。
为输入里的每个人物输出一条档案，JSON：
{
  "profiles":[{
    "id":"",                 // 原样回填输入给定的 id，不要改写
    "name":"",
    "identity":"",           // 身份：种族/血统/职业/阵营/家世
    "aliases":[],
    "role":"",               // 人物定位：主角/导师/对手/挚友/配角/反派…
    "one_liner":"",          // 一句话人物速写
    "appearance":"",         // 外貌：长相/穿着/标志性外形
    "personality":[],        // 稳定性格特质
    "core_desire":"",        // 核心欲望
    "goals":[],              // 目标
    "fears":[],              // 恐惧
    "flaws":[],              // 缺陷/弱点
    "abilities":[],          // 能力/言灵/战斗特征
    "key_experiences":[],    // 重要经历（按时间，含章节）
    "relationships":[{"with":"","relation":"","note":""}],
    "speech_style":"",       // 说话特征/口头禅/语气
    "growth_arc":"",         // 成长弧：从…到…
    "book_end_state":"",     // 书末状态一句话总结
    "tier":"主角|主要|次要|龙套",   // 戏份定位：全书只应有 1 个主角
    "importance":0.0,        // 重要性 0~1，用于排序
    "evidence_chapters":[],  // 证据章节号
    "confidence":0.0
  }]
}
判定 tier 时以"推动主线的戏份"为准：全书围绕谁展开谁就是主角，只能有 1 个。只输出 JSON。"""


PLACE_PROFILE_SYSTEM = """你在为一部小说构建"地点 / 势力档案"，供后续严格续写使用。
输入是程序确定性抽取的证据：相关事件、断言、登场章节。只能依据证据归纳，不足则留空并降低 confidence。
为输入里的每个地点或势力输出一条档案，JSON：
{
  "profiles":[{
    "id":"",                 // 原样回填给定 id
    "name":"",
    "kind":"location|faction",
    "summary":"",            // 一句话定位
    "description":"",        // 详细说明：是什么、有何特征
    "nature":"",             // 性质：学院/社团/官方机构/秘密组织/城市/战舰…
    "role":"",               // 在故事中的作用
    "key_members":[],        // 重要成员（势力）/重要场景人物（地点）
    "related":[],            // 关联的地点或势力
    "importance":0.0,        // 重要性 0~1
    "evidence_chapters":[],
    "confidence":0.0
  }]
}
只输出 JSON。"""


GLOBAL_SYNTHESIS_SYSTEM = """你在整理一部小说的"全局蒸馏"。输入已由程序完成实体归一、事件排序、
状态演算与角色共现统计；不要重新猜测原文、不要续写、不要扩写事件。基于给定证据输出 JSON：
{
  "world_setting":{
    "summary":"",                                                          // 世界观总览（可续写者快速进入）
    "rules":[{"name":"","detail":"","importance":0.0,"chapters":[]}],       // 世界规则/设定：按重要性输出全部要点
    "history":[{"event":"","detail":"","when":"","importance":0.0,"chapters":[]}],  // 关键历史，when=故事内时间(远古/数百年前/主角入学前…)
    "factions_overview":""                                                 // 势力格局概述
  },
  "style_profile":{
    "overall_voice":"",        // 总体声口
    "pov":"",                  // 叙事视角与人称
    "tone":"",                 // 基调
    "sentence_rhythm":"",      // 句式与节奏
    "signature_devices":[],    // 标志性手法（比喻/双声道内心独白/自嘲/命运式短句…）
    "dialogue_style":"",       // 对白特点
    "pacing":"",               // 节奏控制
    "vocabulary":"",           // 用词偏好
    "humor":"",                // 幽默/反讽特征
    "continuation_dos":[],     // 续写应保持
    "continuation_donts":[]    // 续写应避免
  },
  "relationship_graph":[{"src_name":"","dst_name":"","relation":"","sentiment":"正面|负面|复杂|中性","detail":"","chapters":[]}],
  "thread_resolutions":[{"thread_id":"","question":"","status":"open|advanced|resolved","resolved_chapter":0,"resolution":"","evidence":""}],
  "entity_merge_candidates":[{"names":[],"reason":"","confidence":0.0}]
}
world_setting.rules 不要"每章一条"，按你判断的重要性把全部要点抽全并降序排列；history 每条都要给 when（故事内时间）。
对每条情节线程，必须结合后文事件/断言判断是否已有结果：已解决要给出 resolved_chapter 与 resolution；
仅有进展给 advanced；确无下文才保持 open。relationship_graph 必须给语义关系标签（如师徒/挚友/恋慕/血亲/敌对），
不能只罗列共现。merge_candidates 仅在确有迹象同指一人时给出，否则留空。只输出 JSON。"""


@dataclass
class ChapterBlock:
    index: int
    chapters: list[SourceChapter]

    @property
    def chapter_nos(self) -> list[int]:
        return [chapter.chapter_no for chapter in self.chapters]

    @property
    def char_count(self) -> int:
        return sum(len(chapter.text or "") for chapter in self.chapters)

    @property
    def key(self) -> str:
        digest = hashlib.sha1(
            "|".join(f"{chapter.chapter_no}:{chapter.title}:{len(chapter.text)}" for chapter in self.chapters).encode("utf-8")
        ).hexdigest()[:12]
        return f"ud_{self.index:04d}_{digest}"


def build_chapter_blocks(
    chapters: list[SourceChapter],
    *,
    target_chars: int = 40000,
    max_chapters: int = 25,
) -> list[ChapterBlock]:
    target_chars = max(10000, min(120000, int(target_chars or 40000)))
    max_chapters = max(1, min(60, int(max_chapters or 25)))
    groups: list[list[SourceChapter]] = []
    current: list[SourceChapter] = []
    current_chars = 0
    for chapter in chapters:
        chapter_chars = len(chapter.text or "")
        if current and (current_chars + chapter_chars > target_chars or len(current) >= max_chapters):
            groups.append(current)
            current = []
            current_chars = 0
        current.append(chapter)
        current_chars += chapter_chars
    if current:
        groups.append(current)
    return [ChapterBlock(index=index, chapters=group) for index, group in enumerate(groups, 1)]


def extract_unified_blocks(
    repo: Repository,
    llm: LLMClient,
    *,
    target_chars: int = 40000,
    max_chapters: int = 25,
    max_workers: int = 4,
    on_progress: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    blocks = build_chapter_blocks(
        repo.list_source_chapters(),
        target_chars=target_chars,
        max_chapters=max_chapters,
    )
    if not blocks:
        return {"blocks": 0, "calls": 0, "recovered": 0, "warnings": ["no_source"]}
    repo.conn.execute("DELETE FROM distillation_chunks")
    repo.conn.commit()

    def run(block: ChapterBlock) -> tuple[ChapterBlock, dict[str, Any], dict[str, Any], int]:
        if _is_mock(llm):
            extraction = _deterministic_extraction(block)
            _verify_extraction_evidence(block, extraction)
            validation = validate_extraction(block, extraction)
            return block, extraction, validation, 0
        extraction, attempts = _extract_block(llm, block)
        _verify_extraction_evidence(block, extraction)
        validation = validate_extraction(block, extraction)
        if not validation["valid"] and len(block.chapters) > 1:
            midpoint = len(block.chapters) // 2
            left = ChapterBlock(block.index, block.chapters[:midpoint])
            right = ChapterBlock(block.index, block.chapters[midpoint:])
            left_data, left_attempts = _extract_block(llm, left)
            right_data, right_attempts = _extract_block(llm, right)
            _verify_extraction_evidence(left, left_data)
            _verify_extraction_evidence(right, right_data)
            extraction = _merge_extractions([left_data, right_data])
            attempts += left_attempts + right_attempts
            validation = validate_extraction(block, extraction)
            validation["recovered_by_split"] = True
        return block, extraction, validation, attempts

    workers = max(1, min(int(max_workers or 4), len(blocks)))
    results: list[tuple[ChapterBlock, dict[str, Any], dict[str, Any], int]] = []
    if workers == 1:
        for block in blocks:
            results.append(run(block))
            if on_progress:
                on_progress(len(results), len(blocks))
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            for result in executor.map(run, blocks):
                results.append(result)
                if on_progress:
                    on_progress(len(results), len(blocks))

    now = _now()
    warnings: list[str] = []
    calls = 0
    recovered = 0
    needs_review = 0
    for block, extraction, validation, attempts in sorted(results, key=lambda item: item[0].index):
        calls += attempts
        recovered += int(bool(validation.get("recovered_by_split")))
        needs_review += int(not validation["valid"])
        warnings.extend(str(item) for item in validation.get("warnings", []))
        repo.conn.execute(
            """INSERT OR REPLACE INTO distillation_chunks
               (chunk_id, project_id, chunk_index, chapter_nos_json, char_count, input_hash,
                status, attempts, extraction_json, validation_json, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                block.key,
                block.chapters[0].project_id if block.chapters else "",
                block.index,
                json.dumps(block.chapter_nos, ensure_ascii=False),
                block.char_count,
                hashlib.sha256(_chapter_blob(block).encode("utf-8")).hexdigest(),
                "done" if validation["valid"] else "needs_review",
                attempts,
                json.dumps(extraction, ensure_ascii=False),
                json.dumps(validation, ensure_ascii=False),
                now,
                now,
            ),
        )
    repo.conn.commit()
    return {
        "blocks": len(blocks),
        "calls": calls,
        "recovered": recovered,
        "needsReview": needs_review,
        "warnings": sorted(set(warnings))[:50],
        "chapters": sum(len(block.chapters) for block in blocks),
        "characters": sum(block.char_count for block in blocks),
    }


def validate_extraction(block: ChapterBlock, data: dict[str, Any]) -> dict[str, Any]:
    expected = set(block.chapter_nos)
    coverage = data.get("coverage") if isinstance(data, dict) else {}
    processed = {
        _as_int(value)
        for value in ((coverage or {}).get("chapters_processed") or [])
        if _as_int(value) > 0
    }
    warnings: list[str] = []
    if processed != expected:
        warnings.append(f"coverage_mismatch:{sorted(expected - processed)}")
    source_by_chapter = {chapter.chapter_no: chapter.text or "" for chapter in block.chapters}
    invalid_chapters = 0
    evidence_misses = 0
    for key in ("entities", "events", "state_changes", "knowledge_assertions", "plot_threads", "style_samples"):
        for item in _dict_list(data.get(key)):
            chapter_no = _item_chapter(item)
            if chapter_no and chapter_no not in expected:
                invalid_chapters += 1
            evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
            quote = str(evidence.get("quote", "") or item.get("text", "")).strip()
            evidence_chapter = _as_int(evidence.get("chapter") or chapter_no)
            if quote and evidence_chapter in source_by_chapter and evidence.get("verified") is False:
                evidence_misses += 1
    if invalid_chapters:
        warnings.append(f"invalid_chapter_refs:{invalid_chapters}")
    if evidence_misses:
        warnings.append(f"evidence_misses:{evidence_misses}")
    return {
        "valid": processed == expected and invalid_chapters == 0,
        "expected_chapters": sorted(expected),
        "processed_chapters": sorted(processed),
        "evidence_misses": evidence_misses,
        "warnings": warnings,
    }


def reduce_unified_distillation(repo: Repository) -> dict[str, Any]:
    rows = repo.conn.execute(
        "SELECT extraction_json FROM distillation_chunks ORDER BY chunk_index"
    ).fetchall()
    extractions = [json.loads(row["extraction_json"] or "{}") for row in rows]
    repo.clear_distillation_artifacts()
    repo.clear_style_corpus()
    for table in (
        "narrative_entities",
        "narrative_state_changes",
        "narrative_assertions",
        "narrative_threads",
    ):
        repo.conn.execute(f"DELETE FROM {table}")
    repo.conn.execute(
        "DELETE FROM entities WHERE attributes LIKE '%\"source\": \"unified_distillation\"%'"
    )
    repo.conn.commit()

    entity_records, alias_to_id, temp_resolver = _canonicalize_entities(extractions)
    for record in entity_records.values():
        repo.conn.execute(
            """INSERT INTO narrative_entities
               (canonical_id, project_id, type, name, aliases_json, first_seen_chapter,
                evidence_json, confidence) VALUES (?, '', ?, ?, ?, ?, ?, ?)""",
            (
                record["canonical_id"],
                record["type"],
                record["name"],
                json.dumps(record["aliases"], ensure_ascii=False),
                record["first_seen_chapter"],
                json.dumps(record["evidence"], ensure_ascii=False),
                record["confidence"],
            ),
        )
        entity = Entity(
            entity_id=record["canonical_id"],
            type=_engine_entity_type(record["type"]),
            name=record["name"],
            attributes={
                "source": "unified_distillation",
                "aliases": record["aliases"],
                "narrative_type": record["type"],
            },
        )
        repo.conn.execute(
            """INSERT INTO entities (entity_id, type, name, attributes, created_tick)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(entity_id) DO UPDATE SET type=excluded.type, name=excluded.name,
                 attributes=excluded.attributes""",
            (
                entity.entity_id,
                entity.type,
                entity.name,
                json.dumps(entity.attributes, ensure_ascii=False),
                entity.created_tick,
            ),
        )

    events = _collect_with_block(extractions, "events")
    seen_events: set[tuple[int, str]] = set()
    event_count = 0
    for ext_index, item in events:
        chapter = _as_int(item.get("chapter"))
        summary = str(item.get("summary", "")).strip()
        if chapter <= 0 or not summary or (chapter, summary) in seen_events:
            continue
        seen_events.add((chapter, summary))
        event_count += 1
        participant_names: list[str] = []
        for token in _string_list(item.get("participants")):
            _id, name = _resolve_entity_ref(token, ext_index, alias_to_id, temp_resolver, entity_records)
            if name and name not in participant_names:
                participant_names.append(name)
        repo.insert_source_event(
            event_id=_stable_id("uev", chapter, item.get("order"), summary),
            chapter_no=chapter,
            seq=_as_int(item.get("order")) or event_count,
            summary=summary,
            participants=participant_names,
            location=str(item.get("location", "")).strip(),
            time_marker=str(item.get("time_expression", "")).strip(),
            kind=str(item.get("importance", "")).strip() or "event",
            causes_from=[str(item.get("cause", "")).strip()] if str(item.get("cause", "")).strip() else [],
            effects=str(item.get("result", "")).strip(),
            created_at=_now(),
        )

    final_states, state_count = _persist_state_changes(repo, extractions, alias_to_id, temp_resolver, entity_records)
    assertion_count = _persist_assertions(repo, extractions)
    thread_count = _persist_threads(repo, extractions)
    style_count = _persist_style_samples(repo, extractions)
    repo.conn.commit()
    return {
        "entities": len(entity_records),
        "events": event_count,
        "state_changes": state_count,
        "assertions": assertion_count,
        "threads": thread_count,
        "style_samples": style_count,
        "final_states": final_states,
    }


def revalidate_stored_blocks(repo: Repository) -> dict[str, Any]:
    chapters = {chapter.chapter_no: chapter for chapter in repo.list_source_chapters()}
    rows = repo.conn.execute(
        "SELECT chunk_id, chunk_index, chapter_nos_json, extraction_json FROM distillation_chunks ORDER BY chunk_index"
    ).fetchall()
    warnings: list[str] = []
    misses = 0
    for row in rows:
        chapter_nos = json.loads(row["chapter_nos_json"] or "[]")
        block = ChapterBlock(
            index=row["chunk_index"],
            chapters=[chapters[number] for number in chapter_nos if number in chapters],
        )
        extraction = json.loads(row["extraction_json"] or "{}")
        _verify_extraction_evidence(block, extraction)
        validation = validate_extraction(block, extraction)
        misses += int(validation.get("evidence_misses", 0) or 0)
        warnings.extend(str(item) for item in validation.get("warnings", []))
        repo.conn.execute(
            """UPDATE distillation_chunks
               SET extraction_json=?, validation_json=?, status=?, updated_at=?
               WHERE chunk_id=?""",
            (
                json.dumps(extraction, ensure_ascii=False),
                json.dumps(validation, ensure_ascii=False),
                "done" if validation["valid"] else "needs_review",
                _now(),
                row["chunk_id"],
            ),
        )
    repo.conn.commit()
    return {
        "blocks": len(rows),
        "needsReview": sum(
            1 for row in repo.conn.execute("SELECT status FROM distillation_chunks").fetchall()
            if row["status"] != "done"
        ),
        "unverifiedEvidence": misses,
        "warnings": sorted(set(warnings)),
    }


def synthesize_knowledge_package(
    repo: Repository,
    llm: LLMClient,
    *,
    max_input_chars: int = 1200000,
) -> dict[str, Any]:
    base = build_deterministic_package(repo)
    payload = _global_payload(base, max_input_chars=max_input_chars)
    used_fallback = _is_mock(llm)
    synthesized: dict[str, Any] = {}
    profiles: list[dict[str, Any]] = []
    if not used_fallback:
        try:
            synthesized = _parse_json(
                llm.complete_at(
                    GLOBAL_SYNTHESIS_SYSTEM,
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    0.1,
                )
            ) or {}
        except Exception:
            synthesized = {}
            used_fallback = True
        profiles = _profile_all_characters(base, llm)
        place_profiles = _profile_places(base, llm)
    else:
        place_profiles = []
    package = _merge_global_package(base, synthesized, profiles, place_profiles)
    stats = {
        "usedFallback": used_fallback,
        "profiledCharacters": len(profiles),
        "profiledPlaces": len(place_profiles),
        "globalInputChars": len(json.dumps(payload, ensure_ascii=False)),
        "entities": len(base["characters"]) + len(base["locations"]) + len(base["factions"]),
        "events": len(base["chapter_events"]),
        "assertions": len(_list_assertions(repo)),
        "stateChanges": repo.conn.execute("SELECT COUNT(*) AS n FROM narrative_state_changes").fetchone()["n"],
        "threads": len(base["plot_threads"]),
        "styleSamples": len(repo.list_style_segments()),
        "unverifiedEvidence": sum(
            int(json.loads(row["validation_json"] or "{}").get("evidence_misses", 0) or 0)
            for row in repo.conn.execute("SELECT validation_json FROM distillation_chunks").fetchall()
        ),
    }
    repo.conn.execute(
        """INSERT INTO distilled_knowledge_packages (id, project_id, package_json, stats_json, updated_at)
           VALUES (1, '', ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET package_json=excluded.package_json,
             stats_json=excluded.stats_json, updated_at=excluded.updated_at""",
        (json.dumps(package, ensure_ascii=False), json.dumps(stats, ensure_ascii=False), _now()),
    )
    _write_story_bible(repo, package)
    _write_graph_edges(repo, package)
    repo.conn.commit()
    return {"package": package, "stats": stats}


def get_knowledge_package(repo: Repository) -> dict[str, Any]:
    row = repo.conn.execute(
        "SELECT package_json, stats_json, updated_at FROM distilled_knowledge_packages WHERE id=1"
    ).fetchone()
    if not row:
        return {}
    return {
        "package": json.loads(row["package_json"] or "{}"),
        "stats": json.loads(row["stats_json"] or "{}"),
        "updatedAt": row["updated_at"],
    }


def build_deterministic_package(repo: Repository) -> dict[str, Any]:
    entities = [
        {
            "id": row["canonical_id"],
            "type": row["type"],
            "name": row["name"],
            "aliases": json.loads(row["aliases_json"] or "[]"),
            "first_seen_chapter": row["first_seen_chapter"],
            "confidence": row["confidence"],
        }
        for row in repo.conn.execute(
            "SELECT * FROM narrative_entities ORDER BY first_seen_chapter, name"
        ).fetchall()
    ]
    states = _computed_states(repo)
    # 名字/别名 -> canonical_id 索引，用于把事件参与者解析成实体 ID。
    name_to_id: dict[str, str] = {}
    for entity in entities:
        for value in [entity["name"], *entity["aliases"]]:
            name_to_id.setdefault(_normal_name(value), entity["id"])
            name_to_id.setdefault(_merge_key(value), entity["id"])
    assertions = _list_assertions(repo)
    events = repo.list_source_events()
    for event in events:
        event["participant_ids"] = [
            name_to_id[_normal_name(name)]
            for name in event.get("participants", [])
            if _normal_name(name) in name_to_id
        ]
    threads = [
        {
            "thread_id": row["thread_id"],
            "opened_chapter": row["opened_chapter"],
            "kind": row["kind"],
            "question": row["question"],
            "status": row["status"],
            "resolved_chapter": row["resolved_chapter"],
            "resolution": row["resolution"],
            "confidence": row["confidence"],
        }
        for row in repo.conn.execute(
            "SELECT * FROM narrative_threads ORDER BY opened_chapter, thread_id"
        ).fetchall()
    ]
    entity_surface_names = {
        surface
        for entity in entities
        for surface in [entity["name"], *entity["aliases"]]
        if surface
    }
    threads = _link_threads_to_evidence(threads, events, entity_surface_names)
    relationships = _relationship_graph(repo) or _relationship_cooccurrence(repo, events, entities)
    style_segments = repo.list_style_segments()
    feature_counts = Counter(
        feature
        for segment in style_segments
        for feature in _string_list(segment.feature_json.get("features"))
    )
    style_profile = {
        "sample_count": len(style_segments),
        "types": dict(Counter(segment.discourse_type for segment in style_segments)),
        "features": [name for name, _count in feature_counts.most_common(30)],
        "samples": [
            {
                "chapter_id": segment.source_chapter_id,
                "type": segment.discourse_type,
                "text": segment.text[:300],
                "features": segment.feature_json.get("features", []),
            }
            for segment in style_segments[:80]
        ],
    }
    world_assertions = [
        item for item in assertions
        if item["category"] in {"world_rule", "history", "causal_explanation", "correction"}
    ]
    uncertainties = _find_uncertainties(assertions)
    by_type = defaultdict(list)
    for entity in entities:
        by_type[entity["type"]].append(entity)

    def _with_state(entity: dict[str, Any]) -> dict[str, Any]:
        bucket = states.get(entity["id"], {})
        return {
            **entity,
            "final_state": bucket.get("book_end", {}),
            "transient_state": bucket.get("transient", {}),
            "expired_state": bucket.get("expired", []),
        }

    characters = [_with_state(entity) for entity in by_type["character"]]
    book_end_states = {entity_id: bucket.get("book_end", {}) for entity_id, bucket in states.items()}
    timeline = [
        {
            "chapter": event["chapter_no"],
            "order": event["seq"],
            "time_expression": event["time_marker"],
            "summary": event["summary"],
        }
        for event in events
        if event["time_marker"]
    ]
    return {
        "world_setting": {"assertions": world_assertions},
        "characters": characters,
        "locations": by_type["location"],
        "factions": by_type["faction"] + by_type["institution"],
        "chapter_events": events,
        "final_state": book_end_states,
        "state_buckets": states,
        "timeline": timeline,
        "relationship_graph": relationships,
        "plot_threads": threads,
        "style_profile": style_profile,
        "uncertainties": uncertainties,
        "knowledge_assertions": assertions,
        "other_entities": by_type["object"] + by_type["ability"] + by_type["historical_event"],
        "entity_index": {entity["id"]: entity["name"] for entity in entities},
    }


def _extract_block(llm: LLMClient, block: ChapterBlock) -> tuple[dict[str, Any], int]:
    raw = llm.complete_at(
        UNIFIED_EXTRACT_SYSTEM,
        f"本块章节号：{block.chapter_nos}\n\n{_chapter_blob(block)}\n\n只输出 JSON。",
        0.1,
    )
    return _normalize_extraction(_parse_json(raw) or {}), 1


def _chapter_blob(block: ChapterBlock) -> str:
    return "\n\n".join(
        f"<<<CHAPTER no={chapter.chapter_no} title={json.dumps(chapter.title, ensure_ascii=False)}>>>\n"
        f"{chapter.text}\n<<<END_CHAPTER no={chapter.chapter_no}>>>"
        for chapter in block.chapters
    )


def _normalize_extraction(data: dict[str, Any]) -> dict[str, Any]:
    coverage = data.get("coverage") if isinstance(data.get("coverage"), dict) else {}
    return {
        "coverage": {
            "chapters_received": [_as_int(value) for value in coverage.get("chapters_received", []) if _as_int(value) > 0],
            "chapters_processed": [_as_int(value) for value in coverage.get("chapters_processed", []) if _as_int(value) > 0],
            "possibly_incomplete": list(coverage.get("possibly_incomplete", []) or []),
            "warnings": list(coverage.get("warnings", []) or []),
        },
        **{
            key: _dict_list(data.get(key))
            for key in ("entities", "events", "state_changes", "knowledge_assertions", "plot_threads", "style_samples")
        },
    }


def _deterministic_extraction(block: ChapterBlock) -> dict[str, Any]:
    events = []
    samples = []
    for chapter in block.chapters:
        compact = re.sub(r"\s+", " ", chapter.text or "").strip()
        if compact:
            events.append({
                "chapter": chapter.chapter_no,
                "order": 1,
                "summary": compact[:120],
                "participants": [],
                "location": "",
                "time_expression": "",
                "cause": "",
                "result": "",
                "importance": "major",
                "evidence": {"chapter": chapter.chapter_no, "quote": compact[:60]},
            })
            samples.append({
                "chapter": chapter.chapter_no,
                "type": "narration",
                "speaker": "",
                "reason": "本地回退样本",
                "text": compact[:300],
                "features": [],
            })
    return {
        "coverage": {
            "chapters_received": block.chapter_nos,
            "chapters_processed": block.chapter_nos,
            "possibly_incomplete": [],
            "warnings": ["deterministic_fallback"],
        },
        "entities": [],
        "events": events,
        "state_changes": [],
        "knowledge_assertions": [],
        "plot_threads": [],
        "style_samples": samples,
    }


def _merge_extractions(items: list[dict[str, Any]]) -> dict[str, Any]:
    merged = {
        "coverage": {
            "chapters_received": [],
            "chapters_processed": [],
            "possibly_incomplete": [],
            "warnings": [],
        },
        "entities": [],
        "events": [],
        "state_changes": [],
        "knowledge_assertions": [],
        "plot_threads": [],
        "style_samples": [],
    }
    for item in items:
        coverage = item.get("coverage") or {}
        for key in merged["coverage"]:
            merged["coverage"][key].extend(list(coverage.get(key, []) or []))
        for key in ("entities", "events", "state_changes", "knowledge_assertions", "plot_threads", "style_samples"):
            merged[key].extend(_dict_list(item.get(key)))
    for key in ("chapters_received", "chapters_processed"):
        merged["coverage"][key] = sorted(set(_as_int(value) for value in merged["coverage"][key] if _as_int(value) > 0))
    return merged


def _canonicalize_entities(
    extractions: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, str], dict[tuple[int, str], str]]:
    """实体归一：用并查集按"同类型 + 共享归并键/别名"合并同一实体的多次出现。

    返回 (records, alias_to_id, temp_resolver)：
      - records: canonical_id -> 实体记录
      - alias_to_id: 规范化别名 -> canonical_id（用于按名字解析）
      - temp_resolver: (extraction_index, 规范化 temp_id) -> canonical_id（用于解析事件参与者）
    """
    # 1) 收集全部出现，保留块序与 temp_id。
    occurrences: list[dict[str, Any]] = []
    for ext_index, extraction in enumerate(extractions):
        for item in _dict_list(extraction.get("entities")):
            name = str(item.get("name", "")).strip()
            if not name:
                continue
            entity_type = str(item.get("type", "")).strip() or "object"
            aliases = [alias for alias in _string_list(item.get("aliases")) if alias and alias != name]
            occurrences.append({
                "ext_index": ext_index,
                "temp_id": str(item.get("temp_id", "")).strip(),
                "name": name,
                "type": entity_type,
                "aliases": aliases,
                "first_seen_chapter": _as_int(item.get("first_seen_chapter")) or _item_chapter(item),
                "evidence": item.get("evidence") if isinstance(item.get("evidence"), dict) else {},
                "confidence": _as_float(item.get("confidence"), 0.5),
            })

    # 2) 并查集：同类型且共享任一归并键（含敬称剥离）或别名的出现合并。
    parent = list(range(len(occurrences)))

    def find(node: int) -> int:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    key_to_occ: dict[tuple[str, str], int] = {}
    for index, occ in enumerate(occurrences):
        surface = [occ["name"], *occ["aliases"]]
        group = _group_type(occ["type"])  # faction 与 institution 视作同类，避免"卡塞尔学院"重复
        keys = {(group, _merge_key(value)) for value in surface}
        keys |= {(group, _normal_name(value)) for value in surface}
        for key in keys:
            if not key[1]:
                continue
            if key in key_to_occ:
                union(index, key_to_occ[key])
            else:
                key_to_occ[key] = index

    # 2b) 包含式归并：势力/地点里 "前缀+核心" 形式（前缀本身也是实体）并入核心实体，
    #     例如 卡塞尔学院执行部 → 执行部。带"前缀必须是已知实体"的护栏，避免错并。
    norm_to_index: dict[str, int] = {}
    for index, occ in enumerate(occurrences):
        norm_to_index.setdefault(_normal_name(occ["name"]), index)
    for index, occ in enumerate(occurrences):
        group = _group_type(occ["type"])
        if group not in ("faction", "location"):
            continue
        name_norm = _normal_name(occ["name"])
        for other_index, other in enumerate(occurrences):
            if other_index == index or _group_type(other["type"]) != group:
                continue
            core = _normal_name(other["name"])
            if len(core) < 2 or core == name_norm or not name_norm.endswith(core):
                continue
            prefix = name_norm[: -len(core)]
            if len(prefix) >= 2 and prefix in norm_to_index:  # 前缀本身是实体 → 并入核心
                union(index, other_index)

    # 3) 聚合连通分量为 canonical 实体。
    components: dict[int, list[int]] = defaultdict(list)
    for index in range(len(occurrences)):
        components[find(index)].append(index)

    records: dict[str, dict[str, Any]] = {}
    alias_to_id: dict[str, str] = {}
    temp_resolver: dict[tuple[int, str], str] = {}
    for member_indexes in components.values():
        members = [occurrences[i] for i in member_indexes]
        # 规范名：出现频次最高的 name，平局取更长者；优先不含敬称的形式。
        name_counts = Counter(member["name"] for member in members)
        canonical_name = max(
            name_counts,
            key=lambda value: (
                name_counts[value],
                not any(value.endswith(suffix) for suffix in _HONORIFIC_SUFFIXES),
                len(value),
            ),
        )
        entity_type = _group_type(Counter(member["type"] for member in members).most_common(1)[0][0])
        canonical_id = _stable_id("nent", entity_type, canonical_name)
        surfaces: list[str] = []
        for member in members:
            for value in [member["name"], *member["aliases"]]:
                if value and value != canonical_name and value not in surfaces:
                    surfaces.append(value)
        first_seen = min((member["first_seen_chapter"] for member in members if member["first_seen_chapter"] > 0), default=0)
        evidence = next((member["evidence"] for member in members if member["evidence"]), {})
        records[canonical_id] = {
            "canonical_id": canonical_id,
            "type": entity_type,
            "name": canonical_name,
            "aliases": surfaces,
            "first_seen_chapter": first_seen,
            "evidence": evidence,
            "confidence": max(member["confidence"] for member in members),
        }
        for value in [canonical_name, *surfaces]:
            alias_to_id.setdefault(_normal_name(value), canonical_id)
            alias_to_id.setdefault(_merge_key(value), canonical_id)
        for member in members:
            if member["temp_id"]:
                temp_resolver[(member["ext_index"], _normal_name(member["temp_id"]))] = canonical_id
    return records, alias_to_id, temp_resolver


def _resolve_entity_ref(
    token: str,
    ext_index: int,
    alias_to_id: dict[str, str],
    temp_resolver: dict[tuple[int, str], str],
    records: dict[str, dict[str, Any]],
) -> tuple[str, str]:
    """把事件参与者/状态变更里的引用（temp_id 或名字）解析成 (canonical_id, 规范名)。

    解析不到时 canonical_id 返回 ""，名字回退为原始 token（绝不残留 E001 这类临时号）。
    """
    raw = str(token).strip()
    if not raw:
        return "", ""
    canonical_id = ""
    if _is_temp_id(raw):
        canonical_id = temp_resolver.get((ext_index, _normal_name(raw)), "")
    if not canonical_id:
        canonical_id = alias_to_id.get(_normal_name(raw)) or alias_to_id.get(_merge_key(raw), "")
    if canonical_id:
        return canonical_id, records.get(canonical_id, {}).get("name", raw)
    # 残留 temp_id 而无法解析：丢弃临时号，避免污染知识包。
    return "", "" if _is_temp_id(raw) else raw


def _persist_state_changes(
    repo: Repository,
    extractions: list[dict[str, Any]],
    alias_to_id: dict[str, str],
    temp_resolver: dict[tuple[int, str], str],
    records: dict[str, dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], int]:
    states: dict[str, dict[str, Any]] = defaultdict(dict)
    changes = _collect_with_block(extractions, "state_changes")
    per_chapter_states: dict[tuple[int, str], dict[str, Any]] = {}
    count = 0
    for ext_index, item in changes:
        chapter = _as_int(item.get("chapter"))
        entity_ref = str(item.get("entity", "")).strip()
        field = str(item.get("field", "")).strip()
        if chapter <= 0 or not entity_ref or not field:
            continue
        entity_id, entity_name = _resolve_entity_ref(entity_ref, ext_index, alias_to_id, temp_resolver, records)
        if not entity_id:
            if _is_temp_id(entity_ref):
                continue  # 未登记的临时号，跳过避免脏数据
            entity_id = _stable_id("nent", "unknown", entity_ref)
            entity_name = entity_ref
        operation = str(item.get("operation", "replace")).strip() or "replace"
        new_value = item.get("new_value")
        current = states[entity_id].get(field)
        states[entity_id][field] = _apply_change(current, new_value, operation)
        count += 1
        change_id = _stable_id("nchg", chapter, item.get("order"), entity_id, field, count)
        repo.conn.execute(
            """INSERT INTO narrative_state_changes
               (change_id, project_id, chapter_no, seq, entity_id, entity_name, field,
                operation, old_value_json, new_value_json, reason_event, certainty, evidence_json)
               VALUES (?, '', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                change_id,
                chapter,
                _as_int(item.get("order")) or count,
                entity_id,
                records.get(entity_id, {}).get("name", entity_name),
                field,
                operation,
                json.dumps(item.get("old_value"), ensure_ascii=False),
                json.dumps(new_value, ensure_ascii=False),
                str(item.get("reason_event", "")).strip(),
                str(item.get("certainty", "explicit")).strip(),
                json.dumps(item.get("evidence") or {}, ensure_ascii=False),
            ),
        )
        per_chapter_states[(chapter, entity_id)] = dict(states[entity_id])
    for (chapter, entity_id), snapshot in sorted(per_chapter_states.items()):
        name = records.get(entity_id, {}).get("name", entity_id)
        fields = [
            row["field"]
            for row in repo.conn.execute(
                "SELECT field FROM narrative_state_changes WHERE chapter_no=? AND entity_id=? ORDER BY seq",
                (chapter, entity_id),
            ).fetchall()
        ]
        repo.insert_character_snapshot(
            chapter_no=chapter,
            character_name=name,
            snapshot=snapshot,
            changed_fields=fields,
        )
    return {key: dict(value) for key, value in states.items()}, count


def _persist_assertions(repo: Repository, extractions: list[dict[str, Any]]) -> int:
    count = 0
    for item in _collect_sorted(extractions, "knowledge_assertions"):
        chapter = _as_int(item.get("chapter"))
        subject = str(item.get("subject", "")).strip()
        claim = str(item.get("claim", "")).strip()
        category = str(item.get("category", "")).strip()
        if chapter <= 0 or not claim:
            continue
        count += 1
        assertion_id = _stable_id("nast", chapter, category, subject, claim)
        repo.conn.execute(
            """INSERT OR REPLACE INTO narrative_assertions
               (assertion_id, project_id, chapter_no, category, subject, claim,
                certainty, evidence_json, supersedes_json)
               VALUES (?, '', ?, ?, ?, ?, ?, ?, ?)""",
            (
                assertion_id,
                chapter,
                category,
                subject,
                claim,
                str(item.get("certainty", "explicit")).strip(),
                json.dumps(item.get("evidence") or {}, ensure_ascii=False),
                json.dumps(item.get("supersedes") or [], ensure_ascii=False),
            ),
        )
        repo.upsert_codex(
            codex_id=assertion_id,
            name=subject or claim[:30],
            type_=category,
            kind=category,
            summary=claim,
            evidence_chapter=chapter,
            evidence_excerpt=str((item.get("evidence") or {}).get("quote", ""))[:80],
        )
    return count


def _persist_threads(repo: Repository, extractions: list[dict[str, Any]]) -> int:
    records: list[dict[str, Any]] = []
    for item in _collect_sorted(extractions, "plot_threads"):
        question = str(item.get("question", "")).strip()
        if not question:
            continue
        chapter = _as_int(item.get("chapter"))
        kind = str(item.get("kind", "open")).strip()
        match = next(
            (record for record in records if _thread_similarity(question, record["question"]) >= 0.55),
            None,
        )
        if match is None:
            match = {
                "thread_id": _stable_id("nthr", chapter, question),
                "opened_chapter": chapter,
                "kind": kind,
                "question": question,
                "evidence": item.get("evidence") or {},
                "status": "open",
                "resolved_chapter": 0,
                "resolution": "",
                "confidence": _as_float(item.get("confidence"), 0.5),
            }
            records.append(match)
        if kind == "resolve":
            match["status"] = "resolved"
            match["resolved_chapter"] = chapter
            match["resolution"] = str(item.get("resolution", "")).strip()
        elif kind == "advance" and match["status"] != "resolved":
            match["status"] = "advanced"
        match["confidence"] = max(match["confidence"], _as_float(item.get("confidence"), 0.5))
    for record in records:
        repo.conn.execute(
            """INSERT INTO narrative_threads
               (thread_id, project_id, opened_chapter, kind, question, evidence_json,
                status, resolved_chapter, resolution, confidence)
               VALUES (?, '', ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record["thread_id"],
                record["opened_chapter"],
                record["kind"],
                record["question"],
                json.dumps(record["evidence"], ensure_ascii=False),
                record["status"],
                record["resolved_chapter"],
                record["resolution"],
                record["confidence"],
            ),
        )
        repo.insert_foreshadow(
            setup_id=record["thread_id"],
            chapter_no=record["opened_chapter"],
            excerpt=str(record["evidence"].get("quote", ""))[:120],
            what_planted=record["question"],
            why_suspect=record["kind"],
            salience=record["confidence"],
        )
        if record["status"] == "resolved":
            repo.update_foreshadow_pairing(
                setup_id=record["thread_id"],
                status="paid",
                payoff_chapter=record["resolved_chapter"],
                confidence=record["confidence"],
                reason=record["resolution"],
            )
        else:
            repo.update_foreshadow_pairing(
                setup_id=record["thread_id"],
                status="open",
                confidence=record["confidence"],
            )
    return len(records)


def _persist_style_samples(repo: Repository, extractions: list[dict[str, Any]]) -> int:
    chapters = {chapter.chapter_no: chapter for chapter in repo.list_source_chapters()}
    count = 0
    for item in _collect_sorted(extractions, "style_samples"):
        chapter_no = _as_int(item.get("chapter"))
        chapter = chapters.get(chapter_no)
        text = str(item.get("text", "")).strip()
        if chapter is None or not text or text not in chapter.text:
            continue
        start = chapter.text.find(text)
        sample_type = str(item.get("type", "narration")).strip()
        count += 1
        repo.insert_style_segment(StyleSegment(
            id=_stable_id("usty", chapter_no, sample_type, text[:80]),
            project_id=chapter.project_id,
            source_chapter_id=chapter.id,
            start_offset=start,
            end_offset=start + len(text),
            text=text[:500],
            voice_type="character" if sample_type == "dialogue" else "narrator",
            discourse_type=sample_type,
            scene_type="general",
            feature_json={
                "reason": str(item.get("reason", "")).strip(),
                "speaker": str(item.get("speaker", "")).strip(),
                "features": _string_list(item.get("features")),
                "source": "unified_distillation",
            },
            quality_score=0.8,
            annotation_confidence=0.8,
        ))
    return count


# 场景临时态字段：战斗/潜行中的数值生命、装备损耗、即时位置姿态等，不应混入书末持久状态。
# 注意只匹配"数值型"生命/资源字段（生命值/生命剩余/血量），不匹配"生命状态:复活"这类持久状态。
_TRANSIENT_FIELD_RE = re.compile(
    r"(生命值|生命剩余|血量|血条|hp|体力值?|耐力值?|耐久度?|气血值?|灵力值?|法力值?|魔力值?|内力值?|能量值?|"
    r"潜水服|潜水|手套|护目镜|氧气|呼吸|当前|此刻|临时|暂时|姿态|站位|朝向)",
    re.IGNORECASE,
)
# 数值/百分比型取值（如"75%"、"3/5"）几乎必为场景即时量。
_TRANSIENT_VALUE_RE = re.compile(r"(\d+\s*%|\d+\s*/\s*\d+|百分之|剩余\s*\d)")


def _classify_state_scope(field: str, value: Any) -> str:
    """把状态字段分类为 persistent（书末持久）或 transient（场景临时）。"""
    if _TRANSIENT_FIELD_RE.search(field or ""):
        return "transient"
    if isinstance(value, str) and _TRANSIENT_VALUE_RE.search(value):
        return "transient"
    return "persistent"


def _computed_states(repo: Repository) -> dict[str, dict[str, Any]]:
    """按章节顺序应用 add/remove/replace/destroy，演算出每个实体的状态分桶：
    book_end（书末持久）/ transient（场景临时）/ expired（被覆盖或移除的失效值）。"""
    by_entity: dict[str, list[Any]] = defaultdict(list)
    for row in repo.conn.execute(
        "SELECT * FROM narrative_state_changes ORDER BY chapter_no, seq, change_id"
    ).fetchall():
        by_entity[row["entity_id"]].append(row)
    result: dict[str, dict[str, Any]] = {}
    for entity_id, rows in by_entity.items():
        current: dict[str, Any] = {}
        expired: list[dict[str, Any]] = []
        for row in rows:
            field = row["field"]
            operation = row["operation"]
            value = json.loads(row["new_value_json"] or "null")
            previous = current.get(field)
            updated = _apply_change(previous, value, operation)
            if previous not in (None, "", [], {}) and operation in {"replace", "remove", "destroy"} and previous != updated:
                expired.append({"field": field, "value": previous, "until_chapter": row["chapter_no"]})
            current[field] = updated
        book_end: dict[str, Any] = {}
        transient: dict[str, Any] = {}
        for field, value in current.items():
            if value in (None, "", [], {}) or (isinstance(value, dict) and value.get("status") == "destroyed"):
                if isinstance(value, dict) and value.get("status") == "destroyed":
                    expired.append({"field": field, "value": value.get("previous"), "until_chapter": 0})
                continue
            if _classify_state_scope(field, value) == "transient":
                transient[field] = value
            else:
                book_end[field] = value
        result[entity_id] = {
            "book_end": book_end,
            "transient": transient,
            "expired": expired,
            "all": current,
        }
    return result


# 用于从线程问题中剥离的疑问词；保留实体名与问题对象。
_THREAD_STOPWORDS = (
    "能否", "是否", "会不会", "能不能", "可否", "如何", "怎样", "怎么", "是什么",
    "将会", "将如何", "究竟", "到底", "什么", "为何", "为什么",
)
# 表示某条线程已有结果的结局标记。
_RESOLVE_MARKERS = (
    "通过", "完成", "成功", "失败", "确认", "被杀", "死亡", "脱困", "逃生", "逃脱",
    "解决", "揭示", "真相是", "身份是", "原来是", "被射杀", "被狙击", "复活", "获得",
    "被毁", "被困", "牺牲", "答完",
)


# 过于宽泛、几乎出现在所有事件文本里的词，不能用作区分性关键词（否则会误命中无关后文）。
_GENERIC_THREAD_TOKENS = frozenset({
    "适应", "处境", "发展", "关系", "真相", "目的", "身份", "命运", "计划", "行动",
    "内容", "处理", "真实", "出现", "情况", "结果", "影响", "决定", "选择",
})


def _thread_keywords(question: str) -> set[str]:
    text = re.sub(r"[?？]", " ", question or "")
    for word in _THREAD_STOPWORDS:
        text = text.replace(word, " ")
    tokens = set(re.findall(r"[A-Za-z0-9]{2,}", text))
    tokens |= {token for token in re.findall(r"[一-鿿]{2,}", text)}
    # 剔除结局标记词与泛词：它们在事件正文中无处不在，无法唯一指向某条线程。
    return {
        token for token in tokens
        if len(token) >= 2 and token not in _RESOLVE_MARKERS and token not in _GENERIC_THREAD_TOKENS
    }


def _link_threads_to_evidence(
    threads: list[dict[str, Any]],
    events: list[dict[str, Any]],
    entity_names: set[str] | None = None,
) -> list[dict[str, Any]]:
    """确定性伏笔回收：按问题的"区分性关键词"跨章节匹配后文事件。
    命中且事件含结局标记 -> resolved；仅命中 -> advanced。绝不依赖各块临时 thread_id。

    人物名（尤其主角）几乎出现在每条事件里，必须从关键词中剔除，否则会误命中无关后文。"""
    names = entity_names or set()
    doc_freq: Counter = Counter()
    keyword_by_thread: dict[str, set[str]] = {}
    for thread in threads:
        keywords = {kw for kw in _thread_keywords(thread.get("question", "")) if kw not in names}
        keyword_by_thread[thread["thread_id"]] = keywords
        for keyword in keywords:
            doc_freq[keyword] += 1
    ordered_events = sorted(events, key=lambda event: (event.get("chapter_no", 0), event.get("seq", 0)))
    for thread in threads:
        if thread.get("status") == "resolved":
            continue
        # 区分性关键词：在不超过 3 条线程问题中出现，才足以唯一指向某条线程。
        keywords = {kw for kw in keyword_by_thread[thread["thread_id"]] if doc_freq[kw] <= 3}
        if not keywords:
            continue
        last_match = None
        last_resolving = None  # 含结局标记的最后一次命中，优先作为回收依据
        for event in ordered_events:
            if event.get("chapter_no", 0) < thread.get("opened_chapter", 0):
                continue
            blob = f"{event.get('summary', '')} {event.get('effects', '')}"
            if any(keyword in blob for keyword in keywords):
                last_match = event
                if any(marker in blob for marker in _RESOLVE_MARKERS):
                    last_resolving = event
        if last_resolving is not None:
            thread["status"] = "resolved"
            thread["resolved_chapter"] = last_resolving.get("chapter_no", 0)
            thread["resolution"] = (last_resolving.get("effects") or last_resolving.get("summary") or "")[:160]
            thread["resolution_source"] = "deterministic_link"
        elif last_match is not None and thread.get("status") == "open":
            thread["status"] = "advanced"
            thread["resolution_source"] = "deterministic_link"
    return threads


def _relationship_cooccurrence(
    repo: Repository,
    events: list[dict[str, Any]],
    entities: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """事件共现关系底图：同一事件中出现的角色对，按共现次数排序，作为关系图的确定性骨架。
    标签留空，交由 B4 用语义补全（师徒/恋人/敌对等）。"""
    char_ids = {entity["id"] for entity in entities if entity["type"] == "character"}
    id_to_name = {entity["id"]: entity["name"] for entity in entities}
    pair_chapters: dict[tuple[str, str], list[int]] = defaultdict(list)
    for event in events:
        ids = sorted({pid for pid in event.get("participant_ids", []) if pid in char_ids})
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                pair_chapters[(ids[i], ids[j])].append(event.get("chapter_no", 0))
    edges = [
        {
            "src": src,
            "dst": dst,
            "src_name": id_to_name.get(src, src),
            "dst_name": id_to_name.get(dst, dst),
            "relation": "",
            "co_occurrences": len(chapters),
            "chapters": sorted(set(chapters)),
            "source": "co_occurrence",
        }
        for (src, dst), chapters in pair_chapters.items()
    ]
    edges.sort(key=lambda edge: edge["co_occurrences"], reverse=True)
    return edges[:120]


def _list_assertions(repo: Repository) -> list[dict[str, Any]]:
    return [
        {
            "assertion_id": row["assertion_id"],
            "chapter": row["chapter_no"],
            "category": row["category"],
            "subject": row["subject"],
            "claim": row["claim"],
            "certainty": row["certainty"],
            "evidence": json.loads(row["evidence_json"] or "{}"),
            "supersedes": json.loads(row["supersedes_json"] or "[]"),
        }
        for row in repo.conn.execute(
            "SELECT * FROM narrative_assertions ORDER BY chapter_no, assertion_id"
        ).fetchall()
    ]


def _relationship_graph(repo: Repository) -> list[dict[str, Any]]:
    edges: dict[tuple[str, str], dict[str, Any]] = {}
    for row in repo.conn.execute(
        "SELECT * FROM narrative_state_changes ORDER BY chapter_no, seq"
    ).fetchall():
        if "relation" not in row["field"].lower() and "关系" not in row["field"]:
            continue
        value = json.loads(row["new_value_json"] or "null")
        target = ""
        relation = row["field"]
        if isinstance(value, dict):
            target = str(value.get("with") or value.get("target") or "").strip()
            relation = str(value.get("kind") or value.get("relation") or relation).strip()
        elif isinstance(value, str):
            target = value
        if target:
            edges[(row["entity_id"], target)] = {
                "src": row["entity_id"],
                "dst": target,
                "relation": relation,
                "chapter": row["chapter_no"],
            }
    return list(edges.values())


def _find_uncertainties(assertions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_subject: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in assertions:
        by_subject[(item["category"], item["subject"])].append(item)
    out = []
    for item in assertions:
        if (item.get("evidence") or {}).get("verified") is False:
            out.append({
                "category": item["category"],
                "subject": item["subject"],
                "chapter": item["chapter"],
                "claim": item["claim"],
                "reason": "模型给出的短证据无法在原文章节中精确定位",
            })
    for (category, subject), items in by_subject.items():
        claims = {re.sub(r"\s+", "", item["claim"]) for item in items}
        if len(claims) > 1 and any(item["category"] == "correction" or item["supersedes"] for item in items):
            out.append({
                "category": category,
                "subject": subject,
                "versions": [{"chapter": item["chapter"], "claim": item["claim"]} for item in items],
                "reason": "存在修订或互相冲突的知识版本",
            })
    return out


def _verify_extraction_evidence(block: ChapterBlock, data: dict[str, Any]) -> None:
    source_by_chapter = {chapter.chapter_no: chapter.text or "" for chapter in block.chapters}
    for key in ("entities", "events", "state_changes", "knowledge_assertions", "plot_threads"):
        for item in _dict_list(data.get(key)):
            evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
            item["evidence"] = evidence
            quote = str(evidence.get("quote", "")).strip()
            chapter_no = _as_int(evidence.get("chapter") or _item_chapter(item))
            source = source_by_chapter.get(chapter_no, "")
            if not quote or not source:
                evidence["verified"] = False
                continue
            if quote in source:
                evidence["verified"] = True
                continue
            located = _locate_normalized_quote(source, quote)
            if located:
                evidence["quote"] = located[:80]
                evidence["verified"] = True
                evidence["normalized_match"] = True
            else:
                evidence["verified"] = False
    for item in _dict_list(data.get("style_samples")):
        chapter_no = _as_int(item.get("chapter"))
        sample = str(item.get("text", "")).strip()
        source = source_by_chapter.get(chapter_no, "")
        item["evidence_verified"] = bool(sample and sample in source)


def _locate_normalized_quote(source: str, quote: str) -> str:
    normalized_source: list[str] = []
    source_indexes: list[int] = []
    for index, char in enumerate(source):
        if re.match(r"[0-9A-Za-z\u4e00-\u9fff]", char):
            normalized_source.append(char.lower())
            source_indexes.append(index)
    normalized_quote = "".join(
        char.lower() for char in quote
        if re.match(r"[0-9A-Za-z\u4e00-\u9fff]", char)
    )
    if not normalized_quote:
        return ""
    start = "".join(normalized_source).find(normalized_quote)
    if start < 0:
        return ""
    source_start = source_indexes[start]
    source_end = source_indexes[start + len(normalized_quote) - 1] + 1
    return source[source_start:source_end]


def _global_payload(base: dict[str, Any], *, max_input_chars: int) -> dict[str, Any]:
    """全局综合（世界观/文风/关系标签/伏笔裁决）用的聚焦载荷。只发送中间结果，绝不发送原文全文。"""
    assertions = base.get("knowledge_assertions", [])
    events = base.get("chapter_events", [])
    style = base.get("style_profile", {}) or {}
    open_threads = [
        {
            "thread_id": thread.get("thread_id"),
            "opened_chapter": thread.get("opened_chapter"),
            "question": thread.get("question"),
            "status": thread.get("status"),
        }
        for thread in base.get("plot_threads", [])
    ]
    key_events = [
        {
            "chapter": event.get("chapter_no"),
            "summary": event.get("summary"),
            "participants": event.get("participants", []),
            "result": event.get("effects", ""),
        }
        for event in events
        if event.get("kind") == "major" or event.get("seq") == 1
    ]
    cooccurrence = [
        {"a": edge.get("src_name"), "b": edge.get("dst_name"),
         "count": edge.get("co_occurrences"), "chapters": edge.get("chapters", [])}
        for edge in base.get("relationship_graph", [])
        if edge.get("source") == "co_occurrence"
    ][:80]
    merge_seed = _merge_candidate_seed(base.get("characters", []))
    payload = {
        "characters": [
            {"id": char.get("id"), "name": char.get("name"), "aliases": char.get("aliases", [])}
            for char in base.get("characters", [])
        ],
        "cooccurrence": cooccurrence,
        "open_threads": open_threads,
        "key_events": key_events,
        "assertions": [
            {"chapter": item.get("chapter"), "category": item.get("category"),
             "subject": item.get("subject"), "claim": item.get("claim")}
            for item in assertions
        ],
        "style_features": style.get("features", []),
        "style_types": style.get("types", {}),
        "style_samples": [
            {"chapter": sample.get("chapter_id"), "type": sample.get("type"), "text": sample.get("text")}
            for sample in style.get("samples", [])
        ][:48],
        "merge_candidate_seed": merge_seed,
    }
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(serialized) > max_input_chars:
        payload["key_events"] = payload["key_events"][:60]
        payload["style_samples"] = payload["style_samples"][:24]
    return payload


def _merge_candidate_seed(characters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """挑出"可能同指一人"的角色对（同为人物、归并键互为前后缀/包含），交 B4 判断。"""
    seeds: list[dict[str, Any]] = []
    items = [(char.get("name", ""), _merge_key(char.get("name", ""))) for char in characters]
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            ka, kb = items[i][1], items[j][1]
            if not ka or not kb or ka == kb:
                continue
            if ka in kb or kb in ka:
                seeds.append({"names": [items[i][0], items[j][0]]})
    return seeds[:40]


def _profile_all_characters(base: dict[str, Any], llm: LLMClient) -> list[dict[str, Any]]:
    """分批并发为人物生成完整画像；每批携带该人物的断言/事件/状态证据包。"""
    bundles = _character_bundles(base)
    if not bundles:
        return []
    batch_size = 8
    batches = [bundles[i:i + batch_size] for i in range(0, len(bundles), batch_size)]

    def run(batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
        try:
            raw = llm.complete_at(
                CHARACTER_PROFILE_SYSTEM,
                json.dumps({"characters": batch}, ensure_ascii=False, separators=(",", ":")),
                0.2,
            )
            return _dict_list((_parse_json(raw) or {}).get("profiles"))
        except Exception:
            return []

    profiles: list[dict[str, Any]] = []
    workers = max(1, min(4, len(batches)))
    if workers == 1:
        for batch in batches:
            profiles.extend(run(batch))
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            for result in executor.map(run, batches):
                profiles.extend(result)
    return profiles


def _character_bundles(base: dict[str, Any]) -> list[dict[str, Any]]:
    """为每个人物聚合其确定性证据：性格断言、参与事件、状态分桶、共现伙伴。"""
    assertions = base.get("knowledge_assertions", [])
    events = base.get("chapter_events", [])
    edges = base.get("relationship_graph", [])
    by_subject: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in assertions:
        by_subject[_normal_name(item.get("subject", ""))].append(item)
    bundles: list[dict[str, Any]] = []
    for char in base.get("characters", []):
        names = {_normal_name(char.get("name", "")), *(_normal_name(a) for a in char.get("aliases", []))}
        traits = [
            {"chapter": item.get("chapter"), "category": item.get("category"), "claim": item.get("claim")}
            for key in names for item in by_subject.get(key, [])
        ]
        char_events = [
            {"chapter": event.get("chapter_no"), "summary": event.get("summary"),
             "with": [p for p in event.get("participants", []) if p != char.get("name")]}
            for event in events
            if char.get("name") in event.get("participants", [])
        ]
        partners = [
            (edge.get("dst_name") if edge.get("src_name") == char.get("name") else edge.get("src_name"))
            for edge in edges
            if char.get("name") in (edge.get("src_name"), edge.get("dst_name"))
        ]
        bundles.append({
            "id": char.get("id"),
            "name": char.get("name"),
            "aliases": char.get("aliases", []),
            "first_seen_chapter": char.get("first_seen_chapter"),
            "traits": traits[:24],
            "events": char_events[:30],
            "book_end_state": char.get("final_state", {}),
            "transient_state": char.get("transient_state", {}),
            "frequent_partners": partners[:10],
        })
    # 证据多的主要人物排前，保证它们优先获得完整画像。
    bundles.sort(key=lambda bundle: len(bundle["traits"]) + len(bundle["events"]), reverse=True)
    return bundles


def _profile_places(base: dict[str, Any], llm: LLMClient) -> list[dict[str, Any]]:
    """分批并发为重点地点/势力生成档案（与人物画像同构）。"""
    bundles = _place_bundles(base)
    if not bundles:
        return []
    batches = [bundles[i:i + 8] for i in range(0, len(bundles), 8)]

    def run(batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
        try:
            raw = llm.complete_at(
                PLACE_PROFILE_SYSTEM,
                json.dumps({"places": batch}, ensure_ascii=False, separators=(",", ":")),
                0.2,
            )
            return _dict_list((_parse_json(raw) or {}).get("profiles"))
        except Exception:
            return []

    profiles: list[dict[str, Any]] = []
    workers = max(1, min(4, len(batches)))
    if workers == 1:
        for batch in batches:
            profiles.extend(run(batch))
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            for result in executor.map(run, batches):
                profiles.extend(result)
    return profiles


def _place_bundles(base: dict[str, Any]) -> list[dict[str, Any]]:
    """为每个地点/势力聚合证据：相关断言、提及它的事件、登场章节。"""
    assertions = base.get("knowledge_assertions", [])
    events = base.get("chapter_events", [])
    places = [*base.get("locations", []), *base.get("factions", [])]
    bundles: list[dict[str, Any]] = []
    for place in places:
        surfaces = [place.get("name", ""), *place.get("aliases", [])]
        names = {_normal_name(s) for s in surfaces if s}
        related_assertions = [
            {"chapter": a.get("chapter"), "claim": a.get("claim")}
            for a in assertions
            if _normal_name(a.get("subject", "")) in names or any(s and s in str(a.get("claim", "")) for s in surfaces)
        ]
        related_events = [
            {"chapter": e.get("chapter_no"), "summary": e.get("summary")}
            for e in events
            if place.get("name") and (place.get("name") == e.get("location")
                                      or place.get("name") in str(e.get("summary", "")))
        ]
        bundles.append({
            "id": place.get("id"),
            "name": place.get("name"),
            "kind": "faction" if place in base.get("factions", []) else "location",
            "aliases": place.get("aliases", []),
            "first_seen_chapter": place.get("first_seen_chapter"),
            "assertions": related_assertions[:16],
            "events": related_events[:20],
        })
    bundles.sort(key=lambda b: len(b["assertions"]) + len(b["events"]), reverse=True)
    return bundles


AUGMENT_SYSTEM = """你在补全一部小说的实体档案（人物或地点/势力）。输入给出该实体的【现有档案】和它登场的【关键章节原文】。
只依据原文：补全空缺字段、纠正明显错误、把过于简略的字段写充实；不要编造原文没有的信息，不要改写 id/name。
输出与现有档案相同结构的 JSON（顶层即该档案对象，字段名保持一致）。只输出 JSON。"""

# 人物/地点档案"算充实"所需的关键字段，缺得多则判为单薄、需回原文补全。
_CHAR_KEY_FIELDS = ("one_liner", "appearance", "personality", "core_desire", "goals", "abilities", "key_experiences", "growth_arc")
_PLACE_KEY_FIELDS = ("description", "role", "key_members", "nature")


def _find_thin_entities(package: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """挑出"重点但单薄"的实体：主角/主要配角，或重要性高的地点/势力，且关键字段缺失过多。"""
    targets: list[tuple[str, dict[str, Any]]] = []
    for char in package.get("characters", []):
        main = str(char.get("tier", "")) in ("主角", "主要")
        important = main or _as_float(char.get("importance"), 0) >= 0.5
        empties = sum(1 for field in _CHAR_KEY_FIELDS if not char.get(field))
        # 主角/主要配角缺外貌也要回原文补（外貌细节通常只在正文描写里）。
        if important and (empties >= 2 or not char.get("one_liner") or (main and not char.get("appearance"))):
            targets.append(("character", char))
    for place in [*package.get("factions", []), *package.get("locations", [])]:
        important = _as_float(place.get("importance"), 0) >= 0.5
        empties = sum(1 for field in _PLACE_KEY_FIELDS if not place.get(field))
        if important and (empties >= 2 or not place.get("description")):
            targets.append(("place", place))
    targets.sort(key=lambda item: _as_float(item[1].get("importance"), 0), reverse=True)
    return targets


def _key_chapters_for(entity: dict[str, Any], kind: str, events: list[dict[str, Any]]) -> list[int]:
    name = entity.get("name", "")
    surfaces = [name, *entity.get("aliases", [])]
    counter: Counter = Counter()
    for event in events:
        chapter = _as_int(event.get("chapter_no"))
        if not chapter:
            continue
        if kind == "character":
            if name in event.get("participants", []):
                counter[chapter] += 1
        else:
            blob = f"{event.get('location', '')} {event.get('summary', '')}"
            if any(surface and surface in blob for surface in surfaces):
                counter[chapter] += 1
    chapters = [chapter for chapter, _ in counter.most_common(3)]
    if not chapters and _as_int(entity.get("first_seen_chapter")):
        chapters = [_as_int(entity.get("first_seen_chapter"))]
    return sorted(chapters)


def _apply_augment(entity: dict[str, Any], filled: dict[str, Any], key_chapters: list[int]) -> bool:
    """把回读原文得到的补充合并进档案：只填空或用更充实的版本替换，绝不动事实层字段。"""
    preserved = {"id", "name", "type", "first_seen_chapter", "final_state", "transient_state", "expired_state"}
    changed = False
    for field, value in filled.items():
        if field in preserved or value in (None, "", [], {}):
            continue
        current = entity.get(field)
        if current in (None, "", [], {}):
            entity[field] = value
            changed = True
        elif isinstance(value, list) and isinstance(current, list) and len(value) > len(current):
            entity[field] = value
            changed = True
        elif isinstance(value, str) and isinstance(current, str) and len(value) > len(current) + 4:
            entity[field] = value
            changed = True
    if changed:
        entity["augment_chapters"] = sorted(set([*entity.get("augment_chapters", []), *key_chapters]))
        entity["augmented"] = True
    return changed


def review_and_augment(
    repo: Repository,
    llm: LLMClient,
    *,
    max_entities: int = 10,
    char_budget: int = 18000,
) -> dict[str, Any]:
    """后期审查：找出重点但单薄的人物/地点/势力，回到其关键章节原文做针对性补全。"""
    if _is_mock(llm):
        return {"augmented": 0, "reason": "mock"}
    data = get_knowledge_package(repo)
    package = data.get("package") or {}
    if not package:
        return {"augmented": 0, "reason": "no_package"}
    chapters = {chapter.chapter_no: chapter for chapter in repo.list_source_chapters()}
    events = package.get("chapter_events", [])
    targets = _find_thin_entities(package)[:max_entities]

    def run(item: tuple[str, dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any], list[int]]:
        kind, entity = item
        key_chapters = _key_chapters_for(entity, kind, events)
        per = max(2000, char_budget // max(1, len(key_chapters)))
        parts = [
            f"【第{ch}章】\n{chapters[ch].text[:per]}"
            for ch in key_chapters
            if ch in chapters and chapters[ch].text
        ]
        if not parts:
            return entity, {}, key_chapters
        payload = {
            "kind": kind,
            "current_profile": {k: v for k, v in entity.items() if k not in ("augmented", "augment_chapters", "profiled")},
            "key_chapters": key_chapters,
            "source_text": "\n\n".join(parts),
        }
        try:
            filled = _parse_json(llm.complete_at(AUGMENT_SYSTEM, json.dumps(payload, ensure_ascii=False, separators=(",", ":")), 0.2)) or {}
        except Exception:
            filled = {}
        return entity, filled, key_chapters

    augmented = 0
    names: list[str] = []
    workers = max(1, min(4, len(targets)))
    if targets:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            for entity, filled, key_chapters in executor.map(run, targets):
                if filled and _apply_augment(entity, filled, key_chapters):
                    augmented += 1
                    names.append(entity.get("name", ""))

    package["characters"] = _sort_by_importance(package.get("characters", []))
    package["factions"] = _sort_by_importance(package.get("factions", []))
    package["locations"] = _sort_by_importance(package.get("locations", []))
    stats = dict(data.get("stats") or {})
    stats["augmentedEntities"] = augmented
    repo.conn.execute(
        """INSERT INTO distilled_knowledge_packages (id, project_id, package_json, stats_json, updated_at)
           VALUES (1, '', ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET package_json=excluded.package_json,
             stats_json=excluded.stats_json, updated_at=excluded.updated_at""",
        (json.dumps(package, ensure_ascii=False), json.dumps(stats, ensure_ascii=False), _now()),
    )
    _write_story_bible(repo, package)
    _write_graph_edges(repo, package)
    repo.conn.commit()
    return {"augmented": augmented, "targets": names}


def _backfill_profiles(
    base_list: list[dict[str, Any]],
    profiles: list[dict[str, Any]] | None,
    preserved: set[str],
) -> list[dict[str, Any]]:
    """按 id（兜底 name/别名）把模型画像回填到确定性条目，preserved 字段不被覆盖。"""
    if not profiles:
        return [dict(item) for item in base_list]
    index: dict[str, str] = {}
    for item in base_list:
        for token in [item.get("id"), item.get("name"), *item.get("aliases", [])]:
            if token:
                index.setdefault(_normal_name(str(token)), item.get("id"))
    profile_by_id: dict[str, dict[str, Any]] = {}
    for profile in profiles:
        cid = ""
        for token in [profile.get("id"), profile.get("name"), *_string_list(profile.get("aliases"))]:
            cid = index.get(_normal_name(str(token)), "")
            if cid:
                break
        if cid:
            profile_by_id[cid] = profile
    return [
        {
            **item,
            **{k: v for k, v in profile_by_id.get(item.get("id"), {}).items() if k not in preserved},
            "profiled": item.get("id") in profile_by_id,
        }
        for item in base_list
    ]


def _sort_by_importance(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tier_rank = {"主角": 3, "主要": 2, "次要": 1, "龙套": 0}
    return sorted(
        items,
        key=lambda x: (tier_rank.get(str(x.get("tier", "")), 0), _as_float(x.get("importance"), 0.0)),
        reverse=True,
    )


def _merge_global_package(
    base: dict[str, Any],
    synthesized: dict[str, Any],
    profiles: list[dict[str, Any]] | None = None,
    place_profiles: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    result = dict(base)
    # 程序演算结果是事实层，不能被全局总结覆盖；模型只负责归纳世界、人物画像、文风、关系标签、伏笔裁决。
    for key in ("world_setting", "style_profile"):
        value = synthesized.get(key)
        if isinstance(value, dict) and value:
            merged = dict(result.get(key) or {})
            merged.update(value)
            result[key] = merged

    # 人物画像：回填后按重要性 + 主角置顶排序，保留程序演算的状态分桶。
    char_preserved = {"id", "type", "name", "first_seen_chapter", "confidence",
                      "final_state", "transient_state", "expired_state"}
    if profiles:
        result["characters"] = _sort_by_importance(
            _backfill_profiles(base.get("characters", []), profiles, char_preserved)
        )

    # 地点/势力画像：同样回填 + 按重要性排序。
    if place_profiles:
        place_preserved = {"id", "type", "name", "first_seen_chapter", "confidence", "aliases"}
        result["locations"] = _sort_by_importance(
            _backfill_profiles(base.get("locations", []), place_profiles, place_preserved)
        )
        result["factions"] = _sort_by_importance(
            _backfill_profiles(base.get("factions", []), place_profiles, place_preserved)
        )

    # 关系图谱：模型给出语义标签则采用，否则保留共现底图。
    rel = _dict_list(synthesized.get("relationship_graph"))
    if rel:
        result["relationship_graph"] = rel

    # 伏笔跨块裁决：用模型判断回填 status/resolution，按 thread_id 优先、否则按问题相似度。
    # 关键：只允许"升级"(open<advanced<resolved)，绝不让模型把确定性已回收的线程降级回 open。
    resolutions = _dict_list(synthesized.get("thread_resolutions"))
    if resolutions:
        rank = {"open": 0, "advanced": 1, "resolved": 2}
        by_id = {str(item.get("thread_id")): item for item in resolutions if item.get("thread_id")}
        threads = []
        for thread in base.get("plot_threads", []):
            verdict = by_id.get(str(thread.get("thread_id")))
            if verdict is None:
                verdict = next(
                    (item for item in resolutions
                     if _thread_similarity(thread.get("question", ""), item.get("question", "")) >= 0.6),
                    None,
                )
            if verdict and verdict.get("status"):
                current = rank.get(thread.get("status"), 0)
                proposed = rank.get(verdict.get("status"), 0)
                if proposed >= current:
                    thread = {
                        **thread,
                        "status": verdict.get("status"),
                        "resolved_chapter": _as_int(verdict.get("resolved_chapter")) or thread.get("resolved_chapter"),
                        "resolution": verdict.get("resolution") or thread.get("resolution"),
                        "resolution_evidence": verdict.get("evidence", ""),
                    }
                elif verdict.get("resolution") and not thread.get("resolution"):
                    thread = {**thread, "resolution": verdict.get("resolution")}
            threads.append(thread)
        result["plot_threads"] = threads

    # 不确定项：合并程序检出 + 模型提议的实体合并待裁决项。
    merge_candidates = [
        {
            "category": "entity_merge",
            "subject": " / ".join(_string_list(item.get("names"))),
            "claim": f"疑似同一实体：{' / '.join(_string_list(item.get('names')))}",
            "reason": item.get("reason", ""),
            "confidence": _as_float(item.get("confidence"), 0.4),
            "pending_decision": True,
        }
        for item in _dict_list(synthesized.get("entity_merge_candidates"))
        if _string_list(item.get("names"))
    ]
    if merge_candidates:
        result["uncertainties"] = list(result.get("uncertainties") or []) + merge_candidates
    return result


# 蒸馏出的中文语义关系 -> 知识图谱标准关系码（前端按此着色/筛选）。顺序敏感：先判更具体的。
_RELATION_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("情敌", "竞争", "对手", "角逐", "争夺"), "rival"),
    (("仇", "宿敌", "死敌", "对头", "反目", "敌对", "敌人", "仇敌"), "hostile"),
    (("恋", "爱慕", "暗恋", "夫妻", "情侣", "伴侣", "未婚", "恋人", "心上人"), "romance"),
    (("父", "母", "子", "女", "兄", "弟", "姐", "妹", "血亲", "亲属", "家人", "堂", "表亲", "叔", "姨", "舅", "祖", "孙", "契约者", "义兄", "义弟"), "kin"),
    (("师", "徒", "导师", "弟子", "门生", "教官", "监护"), "mentor"),
    (("挚友", "朋友", "好友", "损友", "友", "同伴", "伙伴", "结盟", "盟友", "战友", "知己", "室友"), "allied"),
    (("上司", "下属", "部下", "属下", "领导", "隶属", "成员", "属于", "效力", "麾下"), "member_of"),
    (("同学", "同窗", "相识", "认识", "熟人", "校友"), "knows"),
)


def _classify_relation(label: str) -> str:
    text = label or ""
    for keywords, code in _RELATION_RULES:
        if any(keyword in text for keyword in keywords):
            return code
    return "related_to"


def _write_graph_edges(repo: Repository, package: dict[str, Any]) -> int:
    """把蒸馏出的关系图写入 graph_edges 表，让「检视台」的知识图谱可视化能直接渲染。
    节点沿用已落库的 entities；这里只补关系边（来源标记为 unified_distillation，重写前先清旧）。"""
    name_to_id: dict[str, str] = {}
    for row in repo.conn.execute("SELECT canonical_id, name, aliases_json FROM narrative_entities").fetchall():
        for value in [row["name"], *json.loads(row["aliases_json"] or "[]")]:
            if value:
                name_to_id.setdefault(_normal_name(value), row["canonical_id"])
    repo.conn.execute("DELETE FROM graph_edges WHERE meta LIKE '%unified_distillation%'")
    count = 0
    seen: set[tuple[str, str, str]] = set()
    for edge in package.get("relationship_graph", []):
        src = edge.get("src") or name_to_id.get(_normal_name(str(edge.get("src_name", ""))), "")
        dst = edge.get("dst") or name_to_id.get(_normal_name(str(edge.get("dst_name", ""))), "")
        label = str(edge.get("relation") or "").strip()
        rel = _classify_relation(label)  # 映射到图谱标准关系码（敌对/结盟/恋人/血亲/师徒/竞争…）
        if not src or not dst or src == dst:
            continue
        key = (src, rel, dst)
        if key in seen:
            continue
        seen.add(key)
        chapters = [c for c in (edge.get("chapters") or []) if _as_int(c) > 0]
        repo.upsert_edge(GraphEdge(
            src=src,
            rel=rel,
            dst=dst,
            meta={
                "source": "unified_distillation",
                "sentiment": edge.get("sentiment", ""),
                "relation_label": label,  # 保留原始中文语义关系
                "note": edge.get("detail") or label or "",
            },
            since_chapter=min(chapters) if chapters else 0,
            last_active_chapter=max(chapters) if chapters else 0,
            intensity=min(1.0, 0.4 + 0.1 * len(chapters)) if chapters else 0.5,
        ))
        count += 1
    return count


def _write_story_bible(repo: Repository, package: dict[str, Any]) -> None:
    meta = repo.get_continuation_meta()
    rec = StoryBibleRecord(
        project_id="",
        source_type="continuation",
        title_style_json=package.get("style_profile") or {},
        world_config_json=package.get("world_setting") or {},
        characters_json=list(package.get("characters") or []),
        locations_json=list(package.get("locations") or []),
        factions_json=list(package.get("factions") or []),
        items_json=list(package.get("other_entities") or []),
        relationships_json=list(package.get("relationship_graph") or []),
        timeline_json=list(package.get("timeline") or []),
        open_threads_json=[
            item for item in (package.get("plot_threads") or [])
            if item.get("status") != "resolved"
        ],
        last_state_json=package.get("final_state") or {},
        narrative_constraints_json={
            "uncertainties": package.get("uncertainties") or [],
            "source": "unified_distillation",
            "continuationHint": meta.continuation_hint,
        },
        updated_at=_now(),
    )
    repo.upsert_story_bible_record(rec)


def _apply_change(current: Any, new_value: Any, operation: str) -> Any:
    if operation == "add":
        values = list(current) if isinstance(current, list) else ([] if current in (None, "") else [current])
        additions = new_value if isinstance(new_value, list) else [new_value]
        for value in additions:
            if value not in values:
                values.append(value)
        return values
    if operation in {"remove", "destroy"}:
        if isinstance(current, list):
            removals = new_value if isinstance(new_value, list) else [new_value]
            return [value for value in current if value not in removals]
        return {"status": "destroyed", "previous": current} if operation == "destroy" else None
    return new_value


def _collect_sorted(
    extractions: list[dict[str, Any]],
    key: str,
    *,
    first_seen_key: str = "chapter",
) -> list[dict[str, Any]]:
    items = [item for extraction in extractions for item in _dict_list(extraction.get(key))]
    return sorted(
        items,
        key=lambda item: (
            _as_int(item.get(first_seen_key)) or _item_chapter(item),
            _as_int(item.get("order")),
        ),
    )


def _collect_with_block(
    extractions: list[dict[str, Any]],
    key: str,
    *,
    first_seen_key: str = "chapter",
) -> list[tuple[int, dict[str, Any]]]:
    """同 _collect_sorted，但保留每条所属的块序号（用于按块解析 temp_id）。"""
    items = [
        (ext_index, item)
        for ext_index, extraction in enumerate(extractions)
        for item in _dict_list(extraction.get(key))
    ]
    return sorted(
        items,
        key=lambda pair: (
            _as_int(pair[1].get(first_seen_key)) or _item_chapter(pair[1]),
            _as_int(pair[1].get("order")),
        ),
    )


def _item_chapter(item: dict[str, Any]) -> int:
    evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
    return _as_int(item.get("chapter") or item.get("first_seen_chapter") or evidence.get("chapter"))


def _group_type(value: str) -> str:
    """归并用的粗粒度类型：把同一实体在不同块被标成的近义类型收敛，避免重复实体。"""
    return {
        "institution": "faction",
        "organization": "faction",
        "ability": "object",
        "item": "object",
    }.get(value, value)


def _engine_entity_type(value: str) -> str:
    return {
        "institution": "faction",
        "ability": "object",
        "historical_event": "event",
    }.get(value, value if value in {"character", "location", "faction", "object"} else "object")


def _thread_similarity(a: str, b: str) -> float:
    left = {char for char in a if "\u4e00" <= char <= "\u9fff"}
    right = {char for char in b if "\u4e00" <= char <= "\u9fff"}
    return len(left & right) / max(1, len(left | right))


# 角色名常带的敬称/职衔后缀；用于把"古德里安"与"古德里安教授"归并到同一实体。
_HONORIFIC_SUFFIXES = (
    "教授", "老师", "先生", "女士", "小姐", "博士", "同学", "校长", "院长", "主任",
    "队长", "上校", "中校", "少校", "将军", "大人", "阁下", "老板",
)


def _normal_name(value: str) -> str:
    return re.sub(r"[\s·•・,，。！？!?“”\"'（）()《》〈〉\-—、]", "", value or "").lower()


def _merge_key(value: str) -> str:
    """归并键：在规范名基础上再剥离敬称后缀，但保留足够长的词干（>1 字）。"""
    stem = _normal_name(value)
    for suffix in _HONORIFIC_SUFFIXES:
        if stem.endswith(suffix) and len(stem) > len(suffix) + 1:
            return stem[: -len(suffix)]
    return stem


def _is_temp_id(token: str) -> bool:
    return bool(re.fullmatch(r"[eE]\d{1,4}", token.strip()))


def _stable_id(prefix: str, *parts: Any) -> str:
    digest = hashlib.sha1("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


def _parse_json(raw: str) -> dict[str, Any] | None:
    if not raw:
        return None
    text = raw.strip().strip("`")
    if text.lower().startswith("json"):
        text = text[4:].strip()
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        try:
            value = json.loads(text[start:end + 1])
            return value if isinstance(value, dict) else None
        except Exception:
            return None
    return None


def _dict_list(value: Any) -> list[dict[str, Any]]:
    return [item for item in (value or []) if isinstance(item, dict)]


def _string_list(value: Any) -> list[str]:
    return [str(item).strip() for item in (value or []) if str(item).strip()]


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def _is_mock(llm: LLMClient) -> bool:
    return "mock" in str(getattr(llm, "name", "")).lower()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
