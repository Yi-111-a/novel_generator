from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from typing import Any

from ..llm.base import LLMClient
from ..models import Entity, Faction, GraphEdge, Location, Persona, Thread
from ..repository import Repository

_WORLD_SECTION_KEYS = ("settingCore", "geography", "culture", "rules", "history")
_LOCATION_SUFFIXES = (
    "城", "镇", "村", "学院", "书院", "学校", "街", "路", "巷", "门", "楼", "馆", "阁",
    "塔", "宫", "殿", "山", "海", "江", "河", "湖", "岛", "湾", "站", "厅", "室", "堂",
)
_FACTION_SUFFIXES = (
    "学院", "家族", "社", "会", "盟", "帮", "门", "派", "团", "局", "部", "厅", "军", "公司",
    "委员会", "教会", "组织",
)
_CHARACTER_STOPWORDS = {
    "第一章", "第二章", "第三章", "第四章", "第五章", "第六章", "第七章", "第八章",
    "第九章", "第十章", "正文", "时候", "东西", "地方", "什么", "自己", "所有人",
}


def distill_continuation_world(repo: Repository, llm: LLMClient | None = None) -> dict[str, Any]:
    payload = _extract_world_payload(repo, llm=llm)
    theme = str(payload.get("theme", "")).strip()
    protagonist_want = str(payload.get("protagonistWant", "")).strip()
    geography_summary = str(payload.get("geography", "")).strip()
    culture_summary = str(payload.get("culture", "")).strip()
    repo.set_world_bible(
        setting_core=str(payload.get("settingCore", "")).strip(),
        geography={"summary": geography_summary} if geography_summary else {},
        culture={"summary": culture_summary} if culture_summary else {},
        physics_rules=[str(payload.get("rules", "")).strip()] if str(payload.get("rules", "")).strip() else [],
        protagonist_want=protagonist_want,
        theme=theme,
    )
    repo.conn.execute("DELETE FROM world_bible_sections WHERE source='continuation_distill'")
    for key in _WORLD_SECTION_KEYS:
        body = str(payload.get(key, "")).strip()
        if body:
            repo.add_bible_section(key, key, body, source="continuation_distill")
    return {
        "theme": theme,
        "sections": sum(1 for key in _WORLD_SECTION_KEYS if str(payload.get(key, "")).strip()),
    }


def distill_continuation_structures(repo: Repository, llm: LLMClient | None = None) -> dict[str, Any]:
    payload = _extract_structured_payload(repo, llm=llm)
    character_ids: dict[str, str] = {}
    location_ids: dict[str, str] = {}
    faction_ids: dict[str, str] = {}

    for item in payload.get("characters", []):
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        agent_id = _ensure_entity(repo, entity_type="character", name=name, attrs={"source": "continuation_distill"})
        character_ids[name] = agent_id
        repo.insert_persona(
            Persona(
                agent_id=agent_id,
                name=name,
                want=str(item.get("want", "")).strip(),
                fatal_flaw=str(item.get("fatalFlaw", "") or item.get("fatal_flaw", "")).strip(),
                voice=str(item.get("voice", "")).strip(),
                obstacles=[str(item.get("role", "")).strip()] if str(item.get("role", "")).strip() else [],
            )
        )

    for item in payload.get("locations", []):
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        loc_id = _ensure_entity(repo, entity_type="location", name=name, attrs={"canon": True, "source": "continuation_distill"})
        location_ids[name] = loc_id

    for item in payload.get("factions", []):
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        faction_ids[name] = _stable_id("fac_cont", name)

    for item in payload.get("locations", []):
        name = str(item.get("name", "")).strip()
        loc_id = location_ids.get(name)
        if not loc_id:
            continue
        parent_id = location_ids.get(str(item.get("parent", "")).strip(), "")
        controlling_name = str(item.get("controllingFaction", "") or item.get("controlling_faction", "")).strip()
        repo.upsert_location(
            Location(
                loc_id=loc_id,
                part_id="",
                name=name,
                geo_full=str(item.get("detail", "") or item.get("summary", "")).strip(),
                controlling_faction=faction_ids.get(controlling_name, ""),
                level=str(item.get("level", "")).strip(),
                parent=parent_id,
                culture_local=str(item.get("cultureLocal", "") or item.get("culture_local", "")).strip(),
                summary=str(item.get("summary", "")).strip(),
                detail=str(item.get("detail", "")).strip(),
            )
        )

    for item in payload.get("factions", []):
        name = str(item.get("name", "")).strip()
        faction_id = faction_ids.get(name)
        if not faction_id:
            continue
        territory = [
            location_ids[loc_name]
            for loc_name in [str(v).strip() for v in item.get("territory", []) or []]
            if loc_name in location_ids
        ]
        relations = []
        for rel in item.get("relations", []) or []:
            target_name = str(rel.get("target", "")).strip()
            target_faction_id = faction_ids.get(target_name, "")
            kind = _sanitize_rel(str(rel.get("kind", "")).strip() or "related_to")
            if not target_faction_id or target_faction_id == faction_id:
                continue
            relations.append(
                {
                    "target_faction_id": target_faction_id,
                    "kind": kind,
                    "intensity": max(1, min(5, int(rel.get("intensity", 3) or 3))),
                    "note": str(rel.get("note", "")).strip(),
                }
            )
        repo.upsert_faction(
            Faction(
                faction_id=faction_id,
                name=name,
                ideology=str(item.get("ideology", "")).strip(),
                goals=str(item.get("goals", "")).strip(),
                methods=str(item.get("methods", "")).strip(),
                territory=territory,
                structure=str(item.get("structure", "")).strip(),
                key_members=[],
                history=str(item.get("history", "")).strip(),
                relations=relations,
                secret=str(item.get("secret", "")).strip(),
                summary=str(item.get("summary", "")).strip(),
                detail=str(item.get("detail", "")).strip(),
                source="continuation_distill",
            )
        )

    for item in payload.get("characters", []):
        name = str(item.get("name", "")).strip()
        faction_name = str(item.get("faction", "")).strip()
        agent_id = character_ids.get(name)
        faction_id = faction_ids.get(faction_name, "")
        if agent_id and faction_id:
            repo.update_entity_attributes(agent_id, {"faction_id": faction_id})

    for index, item in enumerate(payload.get("open_threads", []) or []):
        question = str(item.get("question", "")).strip()
        if not question:
            continue
        involved_agents = [
            character_ids[name]
            for name in [str(v).strip() for v in item.get("involved", []) or []]
            if name in character_ids
        ]
        repo.insert_thread(
            Thread(
                thread_id=f"thread_cont_{index}_{hashlib.sha1(question.encode('utf-8')).hexdigest()[:8]}",
                central_question=question,
                involved_agents=involved_agents,
                priority_weight=float(item.get("priorityWeight", 0.7) or 0.7),
                current_tension=float(item.get("currentTension", 0.6) or 0.6),
                status=str(item.get("status", "open")).strip() or "open",
            )
        )

    return {
        "characters": len(character_ids),
        "locations": len(location_ids),
        "factions": len(faction_ids),
        "threads": len(payload.get("open_threads", []) or []),
    }


def distill_continuation_graph(repo: Repository, llm: LLMClient | None = None) -> dict[str, Any]:
    payload = _extract_structured_payload(repo, llm=llm)
    name_map = _entity_name_map(repo)
    before = len(repo.list_edges())
    for edge in payload.get("graph_edges", []) or []:
        src_id = _resolve_node_id(name_map, edge.get("src"), edge.get("srcType") or edge.get("src_type"))
        dst_id = _resolve_node_id(name_map, edge.get("dst"), edge.get("dstType") or edge.get("dst_type"))
        if not src_id or not dst_id or src_id == dst_id:
            continue
        repo.upsert_edge(
            GraphEdge(
                src=src_id,
                rel=_sanitize_rel(str(edge.get("rel", "")).strip() or "related_to"),
                dst=dst_id,
                meta={"source": "continuation_distill", "note": str(edge.get("note", "")).strip()},
                intensity=max(0.1, min(1.0, float(edge.get("intensity", 0.7) or 0.7))),
            )
        )
    _add_heuristic_cooccurrence_edges(repo)
    from ..worldbible import build_static_graph

    build_static_graph(repo)
    after = len(repo.list_edges())
    return {"edges": after, "added": max(0, after - before)}


def continuation_graph_summary(repo: Repository, *, limit: int = 12) -> dict[str, Any]:
    edges = repo.list_edges()
    name_of = _id_to_name_map(repo)
    rel_counter = Counter(edge.rel for edge in edges)
    ranked = sorted(edges, key=lambda edge: (-edge.intensity, edge.rel, edge.src, edge.dst))
    return {
        "edge_count": len(edges),
        "relation_counts": dict(rel_counter),
        "top_edges": [
            {
                "src": edge.src,
                "src_name": name_of.get(edge.src, edge.src),
                "rel": edge.rel,
                "dst": edge.dst,
                "dst_name": name_of.get(edge.dst, edge.dst),
                "intensity": edge.intensity,
                **({"note": edge.meta.get("note", "")} if edge.meta.get("note") else {}),
            }
            for edge in ranked[:limit]
        ],
    }


def _extract_world_payload(repo: Repository, llm: LLMClient | None = None) -> dict[str, Any]:
    chapters = _sample_source_chapters(repo, limit=8, excerpt_chars=900)
    fallback = _heuristic_world_payload(repo)
    if llm is None or not chapters:
        return fallback
    prompt = (
        "你在为长篇小说续写系统做 B2 世界配置蒸馏。只允许根据给定原文片段归纳，不得杜撰。"
        "请输出 JSON，对应键为 settingCore/geography/culture/rules/history/theme/protagonistWant。"
        "每个 section 用简洁但可写作的中文概括；缺失则给空字符串。"
    )
    data = _parse_json(
        llm.complete(prompt, _sample_blob(chapters))
    )
    if not isinstance(data, dict):
        return fallback
    return {
        key: str(data.get(key, fallback.get(key, "")) or fallback.get(key, "")).strip()
        for key in (*_WORLD_SECTION_KEYS, "theme", "protagonistWant")
    }


def _extract_structured_payload(repo: Repository, llm: LLMClient | None = None) -> dict[str, Any]:
    chapters = _sample_source_chapters(repo, limit=10, excerpt_chars=800)
    recent = repo.list_source_chapters()[-4:]
    fallback = _heuristic_structured_payload(repo)
    if llm is None or not chapters:
        return fallback
    system = (
        "你在为长篇小说续写系统做 B3/B4/B5 蒸馏。"
        "只根据给定原文片段提取结构化角色、地点、势力、书末未解线索和知识图谱边。"
        "不能补写正文，不能杜撰未出现的重要设定。"
        "输出 JSON，结构为："
        "{\"characters\":[{\"name\":\"\",\"role\":\"\",\"want\":\"\",\"fatalFlaw\":\"\",\"voice\":\"\",\"faction\":\"\"}],"
        "\"locations\":[{\"name\":\"\",\"summary\":\"\",\"detail\":\"\",\"parent\":\"\",\"controllingFaction\":\"\"}],"
        "\"factions\":[{\"name\":\"\",\"summary\":\"\",\"ideology\":\"\",\"goals\":\"\",\"territory\":[],\"relations\":[{\"target\":\"\",\"kind\":\"\",\"intensity\":3,\"note\":\"\"}]}],"
        "\"open_threads\":[{\"question\":\"\",\"status\":\"open\",\"involved\":[]}],"
        "\"graph_edges\":[{\"src\":\"\",\"srcType\":\"character\",\"rel\":\"related_to\",\"dst\":\"\",\"dstType\":\"character\",\"intensity\":0.7,\"note\":\"\"}]}"
        "。角色不超过 12 个，地点不超过 10 个，势力不超过 8 个，未解线索不超过 10 个，图谱边不超过 24 条。"
        "rel 使用简短英文下划线标签。"
    )
    user = "[全局样本]\n" + _sample_blob(chapters) + "\n\n[书末样本]\n" + _sample_blob(recent)
    data = _parse_json(llm.complete(system, user))
    if not isinstance(data, dict):
        return fallback
    return {
        "characters": list(data.get("characters", []) or []),
        "locations": list(data.get("locations", []) or []),
        "factions": list(data.get("factions", []) or []),
        "open_threads": list(data.get("open_threads", []) or []),
        "graph_edges": list(data.get("graph_edges", []) or []),
    }


def _sample_source_chapters(repo: Repository, *, limit: int, excerpt_chars: int) -> list[Any]:
    chapters = repo.list_source_chapters()
    if len(chapters) <= limit:
        picked = chapters
    else:
        idxs = sorted({round(i * (len(chapters) - 1) / max(1, limit - 1)) for i in range(limit)})
        picked = [chapters[i] for i in idxs]
    out = []
    for chapter in picked:
        clone = type("SampleChapter", (), {})()
        clone.chapter_no = chapter.chapter_no
        clone.title = chapter.title
        clone.text = (chapter.text or "")[:excerpt_chars]
        clone.summary = chapter.summary
        out.append(clone)
    return out


def _sample_blob(chapters: list[Any]) -> str:
    return "\n\n".join(
        f"[第{chapter.chapter_no}章 {chapter.title}]\n{chapter.text}"
        for chapter in chapters
        if getattr(chapter, "text", "").strip()
    )[:12000]


def _heuristic_world_payload(repo: Repository) -> dict[str, Any]:
    chapters = repo.list_source_chapters()
    text = "\n\n".join(ch.text for ch in chapters[:3])
    locations = [item["name"] for item in _heuristic_structured_payload(repo)["locations"][:6]]
    geography = "、".join(locations)
    return {
        "settingCore": text[:300],
        "geography": geography,
        "culture": "原作中的社会气氛、人际秩序与日常场景需沿用 source chapters 原貌推进。",
        "rules": "续写阶段只允许继承原文已显露的世界规则与冲突逻辑，不补写越界设定。",
        "history": "\n".join(
            f"第{ch.chapter_no}章：{(ch.summary or ch.text[:60]).replace(chr(10), ' ')}"
            for ch in chapters[-5:]
        )[:500],
        "theme": "",
        "protagonistWant": "",
    }


def _heuristic_structured_payload(repo: Repository) -> dict[str, Any]:
    chapters = repo.list_source_chapters()
    text = "\n".join(ch.text for ch in chapters)
    names = _heuristic_character_names(text)
    locations = _heuristic_location_names(text)
    factions = _heuristic_faction_names(text)
    characters = [
        {
            "name": name,
            "role": "source_character",
            "want": "",
            "fatalFlaw": "",
            "voice": "",
            "faction": "",
        }
        for name in names[:10]
    ]
    location_rows = [
        {
            "name": name,
            "summary": f"原文出现地点：{name}",
            "detail": f"续写时需要延续原文对{name}的空间印象与事件功能。",
            "parent": "",
            "controllingFaction": "",
        }
        for name in locations[:10]
    ]
    faction_rows = [
        {
            "name": name,
            "summary": f"原文出现势力：{name}",
            "ideology": "",
            "goals": "",
            "territory": [],
            "relations": [],
        }
        for name in factions[:8]
    ]
    recent = chapters[-3:]
    open_threads = []
    for idx, chapter in enumerate(recent):
        summary = (chapter.summary or chapter.text[:80]).replace("\n", " ").strip()
        if not summary:
            continue
        open_threads.append(
            {
                "question": f"第{chapter.chapter_no}章留下的问题：{summary[:40]} 将如何继续？",
                "status": "open",
                "involved": names[:2],
            }
        )
        if idx >= 2:
            break
    graph_edges: list[dict[str, Any]] = []
    if len(names) >= 2:
        graph_edges.append(
            {
                "src": names[0],
                "srcType": "character",
                "rel": "related_to",
                "dst": names[1],
                "dstType": "character",
                "intensity": 0.7,
                "note": "同章节共现",
            }
        )
    if names and locations:
        graph_edges.append(
            {
                "src": names[0],
                "srcType": "character",
                "rel": "appears_in",
                "dst": locations[0],
                "dstType": "location",
                "intensity": 0.6,
                "note": "原文章节共现",
            }
        )
    return {
        "characters": characters,
        "locations": location_rows,
        "factions": faction_rows,
        "open_threads": open_threads,
        "graph_edges": graph_edges,
    }


def _heuristic_character_names(text: str) -> list[str]:
    candidates = re.findall(r"(?<!第)([\u4e00-\u9fff]{2,4})(?=(?:[，。；：、“”‘’\s]|走|看|说|问|想|道|笑|站|坐|拿|握|等|回|进|出|来|去|把|将|对|向|从|在))", text)
    counter: Counter[str] = Counter()
    for item in candidates:
        if item in _CHARACTER_STOPWORDS:
            continue
        if item.endswith(_LOCATION_SUFFIXES) or item.endswith(_FACTION_SUFFIXES):
            continue
        if len(item) < 2 or len(item) > 4:
            continue
        counter[item] += 1
    return [name for name, _ in counter.most_common(12)]


def _heuristic_location_names(text: str) -> list[str]:
    counter: Counter[str] = Counter()
    for item in re.findall(r"([\u4e00-\u9fff]{2,8}(?:%s))" % "|".join(_LOCATION_SUFFIXES), text):
        counter[item] += 1
    for item in re.findall(r"(?:在|到|进|走进|来到|回到|前往)([\u4e00-\u9fff]{2,8})", text):
        if item.endswith(_LOCATION_SUFFIXES):
            counter[item] += 1
    return [name for name, _ in counter.most_common(10)]


def _heuristic_faction_names(text: str) -> list[str]:
    counter: Counter[str] = Counter()
    for item in re.findall(r"([\u4e00-\u9fff]{2,10}(?:%s))" % "|".join(_FACTION_SUFFIXES), text):
        if item.endswith(_LOCATION_SUFFIXES) and not item.endswith("学院"):
            continue
        counter[item] += 1
    return [name for name, _ in counter.most_common(8)]


def _ensure_entity(repo: Repository, *, entity_type: str, name: str, attrs: dict[str, Any]) -> str:
    for entity in repo.list_entities():
        if entity.type == entity_type and entity.name == name:
            repo.update_entity_attributes(entity.entity_id, attrs)
            return entity.entity_id
    entity_id = _stable_id({"character": "char_cont", "location": "loc_cont", "object": "obj_cont"}.get(entity_type, "ent_cont"), name)
    repo.insert_entity(Entity(entity_id, entity_type, name, attrs))
    return entity_id


def _stable_id(prefix: str, name: str) -> str:
    return f"{prefix}_{hashlib.sha1(name.encode('utf-8')).hexdigest()[:10]}"


def _entity_name_map(repo: Repository) -> dict[tuple[str, str], str]:
    mapping: dict[tuple[str, str], str] = {}
    for entity in repo.list_entities():
        mapping[(entity.type, entity.name)] = entity.entity_id
    for faction in repo.list_factions():
        mapping[("faction", faction.name)] = faction.faction_id
    return mapping


def _id_to_name_map(repo: Repository) -> dict[str, str]:
    out = {entity.entity_id: entity.name for entity in repo.list_entities()}
    out.update({faction.faction_id: faction.name for faction in repo.list_factions()})
    return out


def _resolve_node_id(mapping: dict[tuple[str, str], str], raw_name: Any, raw_type: Any) -> str:
    name = str(raw_name or "").strip()
    node_type = str(raw_type or "").strip().lower()
    if not name:
        return ""
    node_type = {
        "character": "character",
        "persona": "character",
        "location": "location",
        "place": "location",
        "faction": "faction",
    }.get(node_type, node_type)
    return mapping.get((node_type, name), "")


def _sanitize_rel(value: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9_]+", "_", value.strip().lower()).strip("_")
    return text or "related_to"


def _parse_json(raw: str) -> Any:
    text = (raw or "").strip().strip("`")
    if text.lower().startswith("json"):
        text = text[4:].strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    for left, right in (("{", "}"), ("[", "]")):
        start = text.find(left)
        end = text.rfind(right)
        if 0 <= start < end:
            try:
                return json.loads(text[start:end + 1])
            except Exception:
                pass
    return None


def _add_heuristic_cooccurrence_edges(repo: Repository) -> None:
    characters = {entity.name: entity.entity_id for entity in repo.list_entities() if entity.type == "character"}
    locations = {entity.name: entity.entity_id for entity in repo.list_entities() if entity.type == "location"}
    for chapter in repo.list_source_chapters():
        chapter_text = chapter.text or ""
        present_characters = [name for name in characters if name in chapter_text]
        present_locations = [name for name in locations if name in chapter_text]
        for idx, name in enumerate(present_characters):
            for other in present_characters[idx + 1:]:
                repo.upsert_edge(
                    GraphEdge(
                        src=characters[name],
                        rel="related_to",
                        dst=characters[other],
                        meta={"source": "heuristic_cooccurrence", "chapter_no": chapter.chapter_no},
                        intensity=0.55,
                        since_chapter=chapter.chapter_no,
                        last_active_chapter=chapter.chapter_no,
                    )
                )
        for char_name in present_characters:
            for loc_name in present_locations:
                repo.upsert_edge(
                    GraphEdge(
                        src=characters[char_name],
                        rel="appears_in",
                        dst=locations[loc_name],
                        meta={"source": "heuristic_cooccurrence", "chapter_no": chapter.chapter_no},
                        intensity=0.5,
                        since_chapter=chapter.chapter_no,
                        last_active_chapter=chapter.chapter_no,
                    )
                )
