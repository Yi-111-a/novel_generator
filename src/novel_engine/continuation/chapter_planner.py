from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from ..disclosure import auto_schedule_disclosures
from ..entity_matching import longest_name_matches
from ..llm.base import LLMClient
from ..models import ChapterPlan, GraphEdge, Thread
from ..prompt_addons import ANTI_AI_FLAVOR_GUIDANCE
from ..repository import Repository
from .chapter_numbering import next_chapter_no


def ensure_continuation_chapter_plan(
    repo: Repository,
    *,
    target_words: int = 0,
    guidance: str = "",
) -> ChapterPlan | None:
    meta = repo.get_continuation_meta()
    if not (meta.write_mode or "").strip():
        return None
    chapter_no = next_chapter_no(repo)
    existing = next((c for c in repo.list_chapter_plans() if c.sequence_order == chapter_no), None)
    if existing is not None:
        return existing
    plan = _build_continuation_plan(repo, chapter_no=chapter_no, target_words=target_words, guidance=guidance)
    if plan is None:
        return None
    repo.upsert_chapter_plan(plan)
    auto_schedule_disclosures(repo)
    return plan


def _build_continuation_plan(
    repo: Repository,
    *,
    chapter_no: int,
    target_words: int,
    guidance: str,
) -> ChapterPlan | None:
    personas = repo.list_personas()
    if not personas:
        return None
    threads = _sorted_threads(repo.list_threads())
    primary_thread = threads[0] if threads else None
    cast = _select_cast(repo, primary_thread=primary_thread)
    pov = cast[0] if cast else personas[0].agent_id
    location_id = _select_location(repo, cast=cast, thread=primary_thread)
    allowed_plan_ids = set(cast) | ({location_id} if location_id else set())
    dramatic_question = _sanitize_summary_for_scope(
        repo,
        _dramatic_question(repo, primary_thread=primary_thread),
        allowed_ids=allowed_plan_ids,
    ) or "主角能否从当前异常中确定下一步行动？"
    exit_state = _sanitize_summary_for_scope(
        repo,
        _exit_state(repo, dramatic_question=dramatic_question),
        allowed_ids=allowed_plan_ids,
    ) or "主角决定继续追查当前异常"
    reveal_gate = _reveal_gate(repo, cast=cast)
    conflict_type = _conflict_type(dramatic_question=dramatic_question, cast=cast, location_id=location_id, repo=repo)
    role = "setup" if repo.get_continuation_meta().write_mode == "new_series_book" else "rising"
    pov_name = _display_name(repo, pov)
    location_name = _location_name(repo, location_id)
    faction_pressure = _faction_pressure(repo, cast=cast, location_id=location_id)
    recent_summary = _sanitize_summary_for_scope(
        repo,
        _recent_summary(repo),
        allowed_ids=allowed_plan_ids,
    )
    guidance_text = (guidance or "").strip()

    beat_goals = [
        _join_parts(
            [
                f"紧接上一章后，{pov_name}先处理书末留下的局面",
                f"场景落在{location_name}" if location_name else "",
                dramatic_question,
            ]
        ),
        _join_parts(
            [
                _cast_pressure_text(repo, cast=cast, pov=pov),
                faction_pressure,
                "通过试探、对话或短促行动把隐藏信息往外撬开",
            ]
        ),
        _join_parts(
            [
                f"把局面推进到：{exit_state}",
                guidance_text,
            ]
        ),
    ]

    if recent_summary:
        beat_goals[0] = _join_parts([beat_goals[0], f"承接书末状态：{recent_summary}"])
    thread_decisions = _schedule_thread_decisions(
        repo,
        sequence_order=chapter_no,
        cast=cast,
        arc_summary="continuation_runtime",
        beats=beat_goals,
        dramatic_question=dramatic_question,
        exit_state=exit_state,
        reveal_gate=reveal_gate,
    )

    summary = _join_parts(
        [
            f"续写第{chapter_no}章计划",
            f"POV={pov_name}",
            f"核心问题：{dramatic_question}",
            f"目标出口：{exit_state}",
        ]
    )
    if location_name:
        summary = _join_parts([summary, f"地点={location_name}"])

    return ChapterPlan(
        chapter_id=f"cont_ch_{chapter_no}",
        arc_id="continuation_runtime",
        sequence_order=chapter_no,
        title="",
        cast=cast,
        location_ids=[location_id] if location_id else [],
        available_items=[],
        items_present=[],
        items_introduced=[],
        items_consumed=[],
        beat_goals=[line for line in beat_goals if line.strip()],
        beat_povs=[pov] * max(1, len([line for line in beat_goals if line.strip()])),
        reveal_gate=reveal_gate,
        thread_decisions_json=thread_decisions,
        knowledge_delta={},
        summary=summary,
        scene_ids=[],
        target_scenes=max(2, len([line for line in beat_goals if line.strip()])),
        role=role,
        target_tension=0.5 if role == "setup" else 0.66,
        dramatic_question=dramatic_question,
        resolution_predicate="",
        min_scenes=2,
        target_words=target_words or repo.get_writing_settings().target_words,
        ending_hook=exit_state,
        hook_type="new_question" if role == "setup" else "reversal_tease",
        pov_agent=pov,
        exit_state=exit_state,
        conflict_type=conflict_type,
        status="planned",
    )


_PARTS_SYS = (
    "你在为长篇小说续写规划**整本新书的多级大纲**。下面给你原作蒸馏出的素材。"
    "请把全书划分成 {n_parts} 个 Part（大部分），每个 Part 给标题/地域/目标/主题，"
    "并指出本 Part 要消费哪些'未回收伏笔'、推进哪些'剧情主线'。"
    "全书要有完整起承转合，主角弧线从初始姿态走到最后蜕变。"
    "绝不引入与蒸馏材料冲突的设定。\n"
    "只输出 JSON：{{\"parts\":[{{\"title\":\"\",\"region\":\"地域名\",\"goal\":\"本部分要完成的剧情目标\","
    "\"theme\":\"本部分主题\",\"consumes_foreshadows\":[\"伏笔关键词\"],\"advances_arcs\":[\"主线名\"]}}]}}"
)

_ARC_SYS = (
    "你在为一本长篇小说续写规划某个 Part 内的 Arc（小部分）划分。"
    "下面给你这个 Part 的总体目标与素材，请把它拆成 {n_arcs} 个 Arc，每个 Arc 给标题/小目标/"
    "戏份重点角色（focus_agents，2-3 个）/计划章数。\n"
    "只输出 JSON：{{\"arcs\":[{{\"title\":\"\",\"summary\":\"本 Arc 的剧情概要\","
    "\"focus_agents\":[\"角色名\"],\"target_chapters\":整数 {min_ch}-{max_ch}}}]}}"
)

_CHAPTER_SYS = (
    "你在为一本长篇小说续写规划某个 Arc 内的具体章节。下面给你这个 Arc 的概要、"
    "可用角色清单（含一句话画像）、可用地点、未回收伏笔。"
    "请规划 {k} 章，每章是完整戏剧单元（目标→阻力→转折）。"
    "**强约束**：① 整个 Arc 内每章在场角色要轮换、不能只用 2-3 个固定角色，"
    "每章至少 3 个 cast、最多 6 个；② POV 在 lead 角色之间合理切换；"
    "③ 至少有 {min_fs} 章明确回收一条未收伏笔；"
    "④ 章节之间剧情递进，赌注逐章升高。\n"
    "只输出 JSON：{{\"chapters\":[{{\"title\":\"\",\"pov\":\"视角角色名\",\"location\":\"地点名\","
    "\"cast\":[\"出场角色名\",...] 3到6个,"
    "\"beats\":[\"节拍1\",\"节拍2\",\"节拍3\"],\"dramatic_question\":\"本章戏剧问题\","
    "\"exit_state\":\"章末外部可观测变化\",\"uses_foreshadow\":\"本章回收的伏笔关键词或空\"}}]}}"
)


def build_continuation_outline(
    repo: Repository,
    llm: LLMClient | None,
    *,
    num_chapters: int = 0,             # 兼容旧调用；为 0 → 走多级模式
    target_words: int = 0,
    n_parts: int = 4,
    arcs_per_part: int = 3,
    chapters_per_arc: int = 5,
) -> dict[str, Any]:
    """C3 · 续写全书多级大纲：Parts → Arcs → ChapterPlans。

    默认 4 Parts × 3 Arcs × 5 Chapters ≈ 60 章。基于蒸馏材料一次性规划。
    写作层按 sequence_order 命中预生成的 ChapterPlan；前端「大纲」页能显示多级树。
    若 num_chapters>0（旧用法），降级为扁平 N 章规划。
    """
    if llm is None:
        return {"planned": 0, "reason": "no_llm"}
    meta = repo.get_continuation_meta()
    if not (meta.write_mode or "").strip():
        return {"planned": 0, "reason": "no_write_mode"}

    personas = repo.list_personas()
    if not personas:
        return {"planned": 0, "reason": "no_personas"}
    cards = {c.agent_id: c for c in repo.list_cards()}
    name_to_aid = {p.name: p.agent_id for p in personas}
    loc_name_to_id = {l.name: l.loc_id for l in repo.list_locations()}
    record = repo.get_story_bible_record()
    last_state = (record.last_state_json if record else {}) or {}
    threads = _sorted_threads(repo.list_threads())
    story_arcs = repo.list_story_arcs()
    open_fs = repo.list_foreshadows(status="open")

    # 角色花名册（带 tier 与一句话），lead 排前
    def _tier_rank(aid: str) -> int:
        c = cards.get(aid)
        return {"lead": 0, "supporting": 1, "minor": 2}.get(c.tier if c else "minor", 3)
    ranked_personas = sorted(personas, key=lambda p: (_tier_rank(p.agent_id), p.name))
    roster_blob = "\n".join(
        f"- {p.name}（{cards.get(p.agent_id).tier if cards.get(p.agent_id) else 'minor'}）"
        f"：{(cards.get(p.agent_id).one_liner if cards.get(p.agent_id) else '') or p.want}"
        for p in ranked_personas[:40]
    )
    loc_blob = "、".join(l.name for l in repo.list_locations()[:20]) or "（无）"
    proto = last_state.get("protagonist") if isinstance(last_state, dict) else None
    if not isinstance(proto, dict):
        proto = {}
    last_blob = (
        f"主角：{proto.get('name','')}（{proto.get('location','')}，{proto.get('emotional_state','')}，"
        f"目标={proto.get('active_goal','')}）\n"
        f"书末局面：{last_state.get('ending_state','')}\n"
        f"续写钩子：{'; '.join(str(h) for h in (last_state.get('hooks_for_next_book') or [])[:6])}"
    )
    thread_blob = "\n".join(f"- {t.central_question}" for t in threads[:12]) or "（无）"
    sarc_blob = "\n".join(
        f"- {a['name']}（{a['resolution_status']}）：{a['journey_summary']}" for a in story_arcs[:6]
    ) or "（无）"
    fs_blob = "\n".join(f"- {f['what_planted']}（第{f['chapter_no']}章埋）" for f in open_fs[:25]) or "（无）"
    hint = (meta.continuation_hint or "").strip()
    mode_line = ("【模式=同宇宙新书】主角与起点可以是新的，但严格沿用原作世界规则与已知人物。"
                 if meta.write_mode == "new_series_book"
                 else "【模式=接当前书】紧接原作书末局面继续。")

    base_ctx = (
        f"{mode_line}\n续写方向提示：{hint or '（无）'}\n\n"
        f"[书末状态]\n{last_blob}\n\n[未解线索]\n{thread_blob}\n\n"
        f"[剧情主线]\n{sarc_blob}\n\n[未回收伏笔]\n{fs_blob}\n\n"
        f"[可用地点]\n{loc_blob}\n\n[可用角色（含一句话画像）]\n{roster_blob}"
    )

    # 旧式扁平模式（向后兼容）
    if num_chapters and num_chapters > 0:
        return _build_flat_outline(
            repo, llm, num_chapters=num_chapters, target_words=target_words,
            base_ctx=base_ctx, name_to_aid=name_to_aid, loc_name_to_id=loc_name_to_id,
            personas=personas, ranked_personas=ranked_personas,
        )

    # 多级模式
    return _build_multilevel_outline(
        repo, llm, n_parts=n_parts, arcs_per_part=arcs_per_part,
        chapters_per_arc=chapters_per_arc, target_words=target_words,
        base_ctx=base_ctx, name_to_aid=name_to_aid, loc_name_to_id=loc_name_to_id,
        personas=personas, ranked_personas=ranked_personas, meta=meta, cards=cards,
    )


def _build_multilevel_outline(
    repo: Repository, llm: LLMClient, *, n_parts: int, arcs_per_part: int,
    chapters_per_arc: int, target_words: int, base_ctx: str,
    name_to_aid: dict[str, str], loc_name_to_id: dict[str, str],
    personas: list, ranked_personas: list, meta, cards: dict,
) -> dict[str, Any]:
    import uuid as _uuid
    from concurrent.futures import ThreadPoolExecutor

    # Stage 1: Parts
    sys = _PARTS_SYS.format(n_parts=n_parts) + ANTI_AI_FLAVOR_GUIDANCE
    parts_data = _parse_json(llm.complete(sys, f"{base_ctx}\n\n规划 {n_parts} 个 Part。只输出 JSON。")) or {}
    parts_list = parts_data.get("parts") if isinstance(parts_data, dict) else None
    if not isinstance(parts_list, list) or not parts_list:
        return {"planned": 0, "reason": "empty_parts"}

    # 落 Parts 到 DB
    from ..models import Part, Arc, ChapterPlan
    part_records: list[Part] = []
    for i, p in enumerate(parts_list[:n_parts], 1):
        if not isinstance(p, dict):
            continue
        pid = f"cont_part_{i}"
        part_records.append(Part(
            part_id=pid, sequence_order=i,
            title=str(p.get("title", "")).strip() or f"第{i}部",
            goal=str(p.get("goal", "")).strip(),
            region=str(p.get("region", "")).strip(),
            status="planned",
        ))
    for pr in part_records:
        repo.upsert_part(pr)

    # Stage 2: Arcs per Part (并发)
    def _plan_arcs(part_idx: int, part_data: dict) -> tuple[str, list[dict]]:
        part_record = part_records[part_idx]
        arc_sys = _ARC_SYS.format(n_arcs=arcs_per_part, min_ch=max(3, chapters_per_arc - 1),
                                   max_ch=chapters_per_arc + 2) + ANTI_AI_FLAVOR_GUIDANCE
        arc_user = (f"{base_ctx}\n\n[当前 Part]\n标题：{part_record.title}\n地域：{part_record.region}\n"
                    f"目标：{part_record.goal}\n主题：{part_data.get('theme','')}\n\n"
                    f"规划本 Part 内 {arcs_per_part} 个 Arc。只输出 JSON。")
        d = _parse_json(llm.complete(arc_sys, arc_user)) or {}
        arcs = d.get("arcs") if isinstance(d, dict) else None
        return part_record.part_id, (arcs if isinstance(arcs, list) else [])

    with ThreadPoolExecutor(max_workers=min(4, len(part_records))) as ex:
        arc_results = list(ex.map(lambda x: _plan_arcs(x[0], x[1]),
                                   list(enumerate(parts_list[:len(part_records)]))))

    # 落 Arcs 到 DB（先建空 arc，拿到 arc_id 再生成章）
    arcs_with_meta: list[tuple[Arc, dict]] = []
    for part_id, arc_list in arc_results:
        for j, a in enumerate(arc_list[:arcs_per_part], 1):
            if not isinstance(a, dict):
                continue
            aid = f"cont_arc_{part_id}_{j}"
            focus_names = [str(x).strip() for x in (a.get("focus_agents") or []) if str(x).strip()]
            focus = [{"agent_id": name_to_aid[n], "weight": round(1.0 - 0.2 * i, 2)}
                     for i, n in enumerate(focus_names) if n in name_to_aid][:4]
            try:
                tc = int(a.get("target_chapters", chapters_per_arc) or chapters_per_arc)
            except Exception:
                tc = chapters_per_arc
            tc = max(3, min(chapters_per_arc + 3, tc))
            arc = Arc(
                arc_id=aid, part_id=part_id, sequence_order=j,
                title=str(a.get("title", "")).strip() or f"小部分{j}",
                summary=str(a.get("summary", "")).strip(),
                target_chapters=tc, focus_agents=focus, status="planned",
            )
            repo.upsert_arc(arc)
            arcs_with_meta.append((arc, a))

    if not arcs_with_meta:
        return {"planned": 0, "reason": "empty_arcs", "parts": len(part_records)}

    # Stage 3: Chapters per Arc (并发)
    aid_to_name = {p.agent_id: p.name for p in personas}

    def _plan_chapters(arc_meta: tuple[Arc, dict]) -> tuple[Arc, list[dict]]:
        arc, _ = arc_meta
        focus_names = [aid_to_name.get(f["agent_id"], "") for f in arc.focus_agents]
        focus_blob = "、".join(n for n in focus_names if n) or "（按 Arc 题材自选）"
        ch_sys = _CHAPTER_SYS.format(k=arc.target_chapters, min_fs=max(1, arc.target_chapters // 2)) + ANTI_AI_FLAVOR_GUIDANCE
        ch_user = (f"{base_ctx}\n\n[当前 Arc]\n标题：{arc.title}\n概要：{arc.summary}\n"
                   f"戏份重点角色：{focus_blob}\n目标章数：{arc.target_chapters}\n\n"
                   f"规划本 Arc 内 {arc.target_chapters} 章。只输出 JSON。")
        d = _parse_json(llm.complete(ch_sys, ch_user)) or {}
        chs = d.get("chapters") if isinstance(d, dict) else None
        return arc, (chs if isinstance(chs, list) else [])

    with ThreadPoolExecutor(max_workers=min(8, len(arcs_with_meta))) as ex:
        ch_results = list(ex.map(_plan_chapters, arcs_with_meta))

    # 按 part/arc 顺序展平，按 sequence_order 落 ChapterPlan
    start_no = next_chapter_no(repo)
    tw = target_words or repo.get_writing_settings().target_words
    planned = 0
    cur_seq = start_no
    new_series_first = (meta.write_mode == "new_series_book")
    for arc, ch_list in ch_results:
        for ch in ch_list[:arc.target_chapters]:
            if not isinstance(ch, dict):
                continue
            if any(c.sequence_order == cur_seq for c in repo.list_chapter_plans()):
                cur_seq += 1
                continue
            pov_name = str(ch.get("pov", "")).strip()
            pov = name_to_aid.get(pov_name, personas[0].agent_id)
            cast_names = [str(x).strip() for x in (ch.get("cast") or []) if str(x).strip()]
            cast = [name_to_aid[n] for n in cast_names if n in name_to_aid]
            if pov and pov not in cast:
                cast.insert(0, pov)
            cast = list(dict.fromkeys(cast))[:6]
            if len(cast) < 3:
                # 兜底：从 Arc focus + 主角池补到 3
                for fa in arc.focus_agents:
                    if fa["agent_id"] not in cast:
                        cast.append(fa["agent_id"])
                    if len(cast) >= 3:
                        break
                for p in personas:
                    if p.agent_id not in cast:
                        cast.append(p.agent_id)
                    if len(cast) >= 3:
                        break
            loc_name = str(ch.get("location", "")).strip()
            loc_id = loc_name_to_id.get(loc_name, "")
            beats = [str(b).strip() for b in (ch.get("beats") or []) if str(b).strip()][:5] or [
                str(ch.get("exit_state", "")).strip() or "推进剧情"]
            dq = str(ch.get("dramatic_question", "")).strip()
            exit_state = str(ch.get("exit_state", "")).strip()
            role = "setup" if (new_series_first and cur_seq == start_no) else "rising"
            reveal_gate = [str(ch.get("uses_foreshadow", "")).strip()] if str(ch.get("uses_foreshadow", "")).strip() else []
            thread_decisions = _schedule_thread_decisions(
                repo,
                sequence_order=cur_seq,
                cast=cast,
                arc_summary=f"{arc.title}：{arc.summary}",
                beats=beats,
                dramatic_question=dq,
                exit_state=exit_state,
                reveal_gate=reveal_gate,
            )
            repo.upsert_chapter_plan(ChapterPlan(
                chapter_id=f"cont_ch_{cur_seq}",
                arc_id=arc.arc_id, sequence_order=cur_seq,
                title=str(ch.get("title", "")).strip(),
                cast=cast,
                location_ids=[loc_id] if loc_id else [],
                available_items=[], items_present=[], items_introduced=[], items_consumed=[],
                beat_goals=beats, beat_povs=[pov] * len(beats),
                reveal_gate=reveal_gate,
                thread_decisions_json=thread_decisions,
                knowledge_delta={},
                summary=f"第{cur_seq}章·{arc.title}：{ch.get('title','')}｜{dq}",
                scene_ids=[], target_scenes=max(2, len(beats)),
                role=role, target_tension=0.5 if role == "setup" else 0.66,
                dramatic_question=dq, resolution_predicate="", min_scenes=2,
                target_words=tw, ending_hook=exit_state,
                hook_type="new_question" if role == "setup" else "reversal_tease",
                pov_agent=pov, exit_state=exit_state, conflict_type="", status="planned",
            ))
            planned += 1
            cur_seq += 1

    auto_schedule_disclosures(repo)
    return {
        "planned": planned,
        "parts": len(part_records),
        "arcs": len(arcs_with_meta),
        "chapters": planned,
    }


def _build_flat_outline(repo, llm, *, num_chapters, target_words, base_ctx,
                        name_to_aid, loc_name_to_id, personas, ranked_personas) -> dict[str, Any]:
    """旧式扁平大纲（向后兼容）。"""
    from ..models import ChapterPlan
    sys = ("你在为长篇小说续写规划接下来 {k} 章的扁平大纲。每章 3-6 个 cast，"
           "POV 在主角池中切换。\n"
           "只输出 JSON：{{\"chapters\":[{{\"title\":\"\",\"pov\":\"\",\"location\":\"\","
           "\"cast\":[],\"beats\":[],\"dramatic_question\":\"\",\"exit_state\":\"\","
           "\"uses_foreshadow\":\"\"}}]}}").format(k=num_chapters) + ANTI_AI_FLAVOR_GUIDANCE
    data = _parse_json(llm.complete(sys, f"{base_ctx}\n\n规划 {num_chapters} 章。只输出 JSON。")) or {}
    chapters = data.get("chapters") if isinstance(data, dict) else None
    if not isinstance(chapters, list) or not chapters:
        return {"planned": 0, "reason": "empty_outline"}
    start_no = next_chapter_no(repo)
    tw = target_words or repo.get_writing_settings().target_words
    planned = 0
    for offset, ch in enumerate(chapters[:num_chapters]):
        if not isinstance(ch, dict):
            continue
        seq = start_no + offset
        if any(c.sequence_order == seq for c in repo.list_chapter_plans()):
            continue
        pov = name_to_aid.get(str(ch.get("pov", "")).strip(), personas[0].agent_id)
        cast_names = [str(x).strip() for x in (ch.get("cast") or []) if str(x).strip()]
        cast = [name_to_aid[n] for n in cast_names if n in name_to_aid]
        if pov and pov not in cast:
            cast.insert(0, pov)
        cast = list(dict.fromkeys(cast))[:6]
        if len(cast) < 3:
            for p in personas:
                if p.agent_id not in cast:
                    cast.append(p.agent_id)
                if len(cast) >= 3:
                    break
        loc_id = loc_name_to_id.get(str(ch.get("location", "")).strip(), "")
        beats = [str(b).strip() for b in (ch.get("beats") or []) if str(b).strip()][:5] or ["推进剧情"]
        dq = str(ch.get("dramatic_question", "")).strip()
        exit_state = str(ch.get("exit_state", "")).strip()
        reveal_gate = [str(ch.get("uses_foreshadow", "")).strip()] if str(ch.get("uses_foreshadow", "")).strip() else []
        thread_decisions = _schedule_thread_decisions(
            repo,
            sequence_order=seq,
            cast=cast,
            arc_summary="continuation_flat",
            beats=beats,
            dramatic_question=dq,
            exit_state=exit_state,
            reveal_gate=reveal_gate,
        )
        repo.upsert_chapter_plan(ChapterPlan(
            chapter_id=f"cont_flat_{seq}", arc_id="continuation_flat",
            sequence_order=seq, title=str(ch.get("title", "")).strip(),
            cast=cast, location_ids=[loc_id] if loc_id else [],
            available_items=[], items_present=[], items_introduced=[], items_consumed=[],
            beat_goals=beats, beat_povs=[pov] * len(beats),
            reveal_gate=reveal_gate, thread_decisions_json=thread_decisions, knowledge_delta={},
            summary=f"第{seq}章：{ch.get('title','')}",
            scene_ids=[], target_scenes=max(2, len(beats)), role="rising",
            target_tension=0.66,
            dramatic_question=dq,
            resolution_predicate="", min_scenes=2, target_words=tw,
            ending_hook=exit_state,
            hook_type="reversal_tease", pov_agent=pov,
            exit_state=exit_state,
            conflict_type="", status="planned",
        ))
        planned += 1
    auto_schedule_disclosures(repo)
    return {"planned": planned}


def _parse_json(raw: str):
    if not raw:
        return None
    s = raw.strip().strip("`")
    if s.lower().startswith("json"):
        s = s[4:].strip()
    for open_c, close_c in (("{", "}"), ("[", "]")):
        i, j = s.find(open_c), s.rfind(close_c)
        if 0 <= i < j:
            try:
                return json.loads(s[i:j + 1])
            except Exception:
                continue
    try:
        return json.loads(s)
    except Exception:
        return None


def _strip_json(raw: str) -> str:
    s = (raw or "").strip().strip("`")
    if s.lower().startswith("json"):
        s = s[4:].strip()
    i, j = s.find("{"), s.rfind("}")
    return s[i:j + 1] if 0 <= i < j else s


def _sorted_threads(threads: list[Thread]) -> list[Thread]:
    status_rank = {"open": 0, "converging": 1, "resolved": 2}
    return sorted(
        threads,
        key=lambda t: (
            status_rank.get(t.status, 9),
            -(t.priority_weight or 0.0),
            -(t.current_tension or 0.0),
            t.thread_id,
        ),
    )


def _select_cast(repo: Repository, *, primary_thread: Thread | None) -> list[str]:
    personas = repo.list_personas()
    persona_ids = {p.agent_id for p in personas}
    ordered: list[str] = []
    if primary_thread is not None:
        for agent_id in primary_thread.involved_agents:
            if agent_id in persona_ids and agent_id not in ordered:
                ordered.append(agent_id)
    seed = ordered[0] if ordered else (personas[0].agent_id if personas else "")
    if seed:
        for edge in repo.attention_ranked_neighbors(seed, limit=12):
            other = edge.dst if edge.src == seed else edge.src
            entity = repo.get_entity(other)
            if entity is not None and entity.type == "character" and other in persona_ids and other not in ordered:
                ordered.append(other)
            if len(ordered) >= 3:
                break
    if not ordered:
        ordered = [personas[0].agent_id]
    return ordered[:3]


def _select_location(repo: Repository, *, cast: list[str], thread: Thread | None) -> str:
    location_scores: dict[str, float] = {}
    recent_text = _recent_text(repo)
    for agent_id in cast:
        for edge in repo.attention_ranked_neighbors(agent_id, limit=16):
            other = edge.dst if edge.src == agent_id else edge.src
            entity = repo.get_entity(other)
            if entity is not None and entity.type == "location":
                score = float(edge.intensity or 0.0)
                if edge.rel in {"appears_in", "located_in"}:
                    score += 0.25
                location_scores[other] = max(location_scores.get(other, 0.0), score)
    for location in repo.list_locations():
        if location.name and location.name in recent_text:
            location_scores[location.loc_id] = max(location_scores.get(location.loc_id, 0.0), 0.9)
    if thread is not None and thread.central_question:
        for location in repo.list_locations():
            if location.name and location.name in thread.central_question:
                location_scores[location.loc_id] = max(location_scores.get(location.loc_id, 0.0), 0.95)
    if location_scores:
        return max(location_scores.items(), key=lambda item: (item[1], item[0]))[0]
    locations = repo.list_locations()
    return locations[0].loc_id if locations else ""


def _dramatic_question(repo: Repository, *, primary_thread: Thread | None) -> str:
    story_bible = repo.get_story_bible_record()
    if primary_thread is not None and primary_thread.central_question.strip():
        return primary_thread.central_question.strip()
    if story_bible:
        for item in story_bible.open_threads_json:
            question = str(item.get("question", "")).strip()
            if question:
                return question
        ending_state = str((story_bible.last_state_json or {}).get("ending_state", "")).strip()
        if ending_state:
            return f"{ending_state} 将把谁推向下一步？"
    hint = repo.get_continuation_meta().continuation_hint.strip()
    if hint:
        return hint
    recent = _recent_summary(repo)
    return recent or "书末留下的局面将怎样继续发酵？"


def _exit_state(repo: Repository, *, dramatic_question: str) -> str:
    story_bible = repo.get_story_bible_record()
    ending_state = ""
    if story_bible:
        ending_state = str((story_bible.last_state_json or {}).get("ending_state", "")).strip()
    if ending_state:
        return f"让人物对“{_trim_for_plan(dramatic_question, 26)}”做出第一步回应，并把“{_trim_for_plan(ending_state, 26)}”转成可行动的新线索"
    return f"让“{_trim_for_plan(dramatic_question, 30)}”从悬置问题推进成下一章可继续追击的局面"


def _reveal_gate(repo: Repository, *, cast: list[str]) -> list[str]:
    notes: list[str] = []
    for agent_id in cast[:2]:
        for edge in repo.attention_ranked_neighbors(agent_id, limit=8):
            note = str((edge.meta or {}).get("note", "")).strip()
            if note and note not in notes:
                names = f"{_name_of(repo, edge.src)} {edge.rel} {_name_of(repo, edge.dst)}"
                notes.append(f"{names}：{note}")
            if len(notes) >= 3:
                return notes
    story_bible = repo.get_story_bible_record()
    if story_bible:
        for item in story_bible.open_threads_json[:2]:
            question = str(item.get("question", "")).strip()
            if question and question not in notes:
                notes.append(question)
    return notes[:3]


def _schedule_thread_decisions(
    repo: Repository,
    *,
    sequence_order: int,
    cast: list[str],
    arc_summary: str,
    beats: list[str],
    dramatic_question: str,
    exit_state: str,
    reveal_gate: list[str],
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Phase 1: per-chapter suspense scheduling.

    Deterministic and cheap: choose 5-8 active threads, then mark each as
    reveal/hint/hide based on priority, tension, cast overlap and chapter text.
    """
    threads = [t for t in _sorted_threads(repo.list_threads()) if t.status in ("open", "converging")]
    if not threads:
        return []
    cast_set = set(cast or [])
    names = {_name_of(repo, agent_id) for agent_id in cast_set}
    context = " ".join([
        arc_summary or "",
        dramatic_question or "",
        exit_state or "",
        " ".join(beats or []),
        " ".join(reveal_gate or []),
        " ".join(n for n in names if n),
    ])
    reveal_text = " ".join(reveal_gate or [])

    def relevance(t: Thread) -> float:
        q = t.central_question or ""
        score = 0.0
        if any(agent_id in cast_set for agent_id in (t.involved_agents or [])):
            score += 0.45
        involved_names = [_name_of(repo, agent_id) for agent_id in (t.involved_agents or [])]
        if any(n and n in context for n in involved_names):
            score += 0.25
        q_chars = {ch for ch in q if "\u4e00" <= ch <= "\u9fff"}
        ctx_chars = {ch for ch in context if "\u4e00" <= ch <= "\u9fff"}
        if q_chars and ctx_chars:
            score += min(0.35, len(q_chars & ctx_chars) / max(12, len(q_chars)) * 0.7)
        if reveal_text and (q in reveal_text or reveal_text in q):
            score += 0.5
        return min(1.0, score)

    scored: list[tuple[float, float, Thread]] = []
    for t in threads:
        rel = relevance(t)
        base = float(t.priority_weight or 0.5) * max(0.2, float(t.current_tension or 0.0))
        score = base * (0.65 + rel)
        scored.append((score, rel, t))
    scored.sort(key=lambda item: (-item[0], item[2].thread_id))
    selected = scored[:max(5, min(limit, len(scored)))]

    out: list[dict[str, Any]] = []
    reveal_used = False
    for rank, (score, rel, t) in enumerate(selected):
        explicit_reveal = bool(reveal_text and (t.central_question in reveal_text or reveal_text in t.central_question))
        if not reveal_used and (explicit_reveal or (rank == 0 and rel >= 0.32 and sequence_order % 4 == 0)):
            decision = "reveal"
            reveal_used = True
            reason = "本章允许揭示，且线索与当前戏剧问题高度相关"
        elif rank <= 2 and rel >= 0.18:
            decision = "hint"
            reason = "与本章人物/地点/问题相关，只推进可感知信号"
        else:
            decision = "hide"
            reason = "本章保持悬置，不解释动机或真相"
        out.append({
            "threadId": t.thread_id,
            "question": t.central_question,
            "decision": decision,
            "reason": reason,
            "score": round(score, 3),
            "relevance": round(rel, 3),
            "tension": round(float(t.current_tension or 0.0), 3),
        })
    return out


def _conflict_type(*, dramatic_question: str, cast: list[str], location_id: str, repo: Repository) -> str:
    text = dramatic_question
    if re.search(r"(身份|真相|谁|为什么)", text):
        return "身份危机"
    faction_ids = {
        str((repo.get_entity(agent_id).attributes or {}).get("faction_id", ""))
        for agent_id in cast
        if repo.get_entity(agent_id) is not None
    } - {""}
    if location_id:
        location = repo.get_location(location_id)
        if location and location.controlling_faction:
            faction_ids.add(location.controlling_faction)
    if len(faction_ids) >= 2:
        return "三方搅局"
    if re.search(r"(选择|站队|代价|是否)", text):
        return "立场抉择"
    return "心理博弈"


def _faction_pressure(repo: Repository, *, cast: list[str], location_id: str) -> str:
    pieces: list[str] = []
    faction_names = {f.faction_id: f.name for f in repo.list_factions()}
    if location_id:
        location = repo.get_location(location_id)
        if location and location.controlling_faction:
            pieces.append(f"{location.name}受{faction_names.get(location.controlling_faction, location.controlling_faction)}影响")
    seen: list[str] = []
    for agent_id in cast:
        entity = repo.get_entity(agent_id)
        faction_id = str((entity.attributes or {}).get("faction_id", "")) if entity else ""
        faction_name = faction_names.get(faction_id, "")
        if faction_name and faction_name not in seen:
            seen.append(faction_name)
    if seen:
        pieces.append("在场势力：" + "、".join(seen[:2]))
    return "；".join(piece for piece in pieces if piece)


def _cast_pressure_text(repo: Repository, *, cast: list[str], pov: str) -> str:
    names = [_display_name(repo, agent_id) for agent_id in cast if agent_id != pov]
    pov_name = _display_name(repo, pov)
    if names:
        return f"{pov_name}与{'、'.join(names[:2])}正面接触"
    return f"{pov_name}独自推进局面"


def _display_name(repo: Repository, agent_id: str) -> str:
    persona = next((p for p in repo.list_personas() if p.agent_id == agent_id), None)
    if persona is None:
        return agent_id
    return repo.get_character_display_name(agent_id, persona.name)


def _location_name(repo: Repository, location_id: str) -> str:
    if not location_id:
        return ""
    location = repo.get_location(location_id)
    return location.name if location else location_id


def _name_of(repo: Repository, node_id: str) -> str:
    entity = repo.get_entity(node_id)
    if entity is not None:
        return entity.name
    faction = repo.get_faction(node_id)
    if faction is not None:
        return faction.name
    return node_id


def _recent_text(repo: Repository) -> str:
    accepted = repo.list_accepted_chapters()
    if accepted:
        return accepted[-1].prose or ""
    source = repo.list_source_chapters()
    return source[-1].text if source else ""


def _recent_summary(repo: Repository) -> str:
    story_bible = repo.get_story_bible_record()
    if story_bible:
        ending_state = str((story_bible.last_state_json or {}).get("ending_state", "")).strip()
        if ending_state:
            return ending_state
        timeline = story_bible.timeline_json or []
        if timeline:
            return str((timeline[-1] or {}).get("summary", "")).strip()
    source = repo.list_source_chapters()
    if source:
        latest = source[-1]
        return (latest.summary or latest.text[:120]).strip()
    return ""


def _sanitize_summary_for_scope(
    repo: Repository,
    text: str,
    *,
    allowed_ids: set[str],
) -> str:
    """Keep continuity context without turning unrelated entities into beats."""
    rows = [
        (entity.entity_id, entity.type, entity.name)
        for entity in repo.list_entities()
        if entity.name
    ]
    rows.extend(
        (faction.faction_id, "faction", faction.name)
        for faction in repo.list_factions()
        if faction.name
    )
    kept: list[str] = []
    for clause in re.split(r"(?<=[。！？；;])|\n+", str(text or "")):
        clause = clause.strip()
        if not clause:
            continue
        matches = longest_name_matches(clause, rows)
        if any(match.entity_id not in allowed_ids for match in matches):
            continue
        kept.append(clause)
    return "".join(kept)[:240]


def _trim_for_plan(text: str, limit: int) -> str:
    compact = re.sub(r"\s+", " ", text or "").strip()
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 1)] + "…"


def _join_parts(parts: list[str]) -> str:
    clean = [part.strip("；;，,。 ") for part in parts if part and part.strip()]
    return "；".join(clean)
