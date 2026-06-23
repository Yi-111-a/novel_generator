# -*- coding: utf-8 -*-
"""C1 · 每章逐字蒸馏（完全蒸馏的第一层）。

对原作**每一章全文**（超长则按窗口切，带重叠）做四件独立的事：
  C1.a 单章事件抽取   -> source_events
  C1.b 单章人物快照   -> character_state_snapshots
  C1.c 设定/规则增量   -> settings_codex
  C1.e 伏笔候选抽取   -> foreshadow_setups

全部 prompt 通用：只说"本章/原作/角色"，不含任何作品或作者专名。
章与章之间无依赖，可用线程池并发（KeyPoolClient 线程安全）。
"""
from __future__ import annotations

import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from ..llm.base import LLMClient
from ..repository import Repository

WINDOW_SIZE = 6000
WINDOW_OVERLAP = 800
EXTRACT_TEMPERATURE = 0.2


# ---------------- 工具 ----------------
def _split_windows(text: str, size: int = WINDOW_SIZE, overlap: int = WINDOW_OVERLAP) -> list[str]:
    """把单章正文切成带重叠的窗口；尽量在段落/句子边界断开。"""
    text = (text or "").strip()
    if len(text) <= size:
        return [text] if text else []
    windows: list[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + size, n)
        if end < n:
            # 往后找一个自然断点（段落 > 句末 > 原位）
            window = text[start:end]
            cut = max(window.rfind("\n"), window.rfind("。"), window.rfind("！"), window.rfind("？"))
            if cut > size // 2:
                end = start + cut + 1
        windows.append(text[start:end])
        if end >= n:
            break
        start = max(end - overlap, start + 1)
    return windows


def _parse_json(raw: str) -> Any:
    if not raw:
        return None
    s = raw.strip().strip("`")
    if s.lower().startswith("json"):
        s = s[4:].strip()
    # 优先按对象/数组括号截取
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


def _gram_overlap(a: str, b: str, n: int = 4) -> float:
    def grams(t: str) -> set[str]:
        t = re.sub(r"\s+", "", t or "")
        return {t[i:i + n] for i in range(max(0, len(t) - n + 1))}
    ga, gb = grams(a), grams(b)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)


def _sid(prefix: str, *parts: Any) -> str:
    h = hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:10]
    return f"{prefix}_{h}"


# ---------------- C1.a 事件 ----------------
_EVENTS_SYS = (
    "你在为长篇小说做单章事件蒸馏。读完给定的本章正文后，按时间顺序列出本章发生的所有"
    "**剧情相关客观事件**（人物做了什么、发生了什么、关系/处境如何改变）。"
    "不要抽心理独白或语言学细节，只抽客观事件。绝不杜撰正文没有的事。\n"
    "只输出 JSON：{\"events\":[{"
    "\"summary\":\"一句话事件(≤30字)\",\"participants\":[\"出场角色名\"],\"location\":\"发生地点\","
    "\"time_marker\":\"时间线索(如黄昏/三天后/翌日,没有留空)\","
    "\"kind\":\"encounter|conflict|reveal|travel|decision|loss|gain|emotion_shift|threshold 之一\","
    "\"effects\":\"该事件造成的可观察变化\"}]}。事件数量按本章实际，不要凑数也不要漏。"
)


def _extract_events(llm: LLMClient, chapter_no: int, title: str, window: str) -> list[dict]:
    user = f"[第{chapter_no}章 {title}]（本章正文片段）\n{window}\n\n只输出 JSON。"
    data = _parse_json(llm.complete_at(_EVENTS_SYS, user, EXTRACT_TEMPERATURE))
    if not isinstance(data, dict):
        return []
    out = []
    for e in (data.get("events") or []):
        if isinstance(e, dict) and str(e.get("summary", "")).strip():
            out.append({
                "summary": str(e.get("summary", "")).strip(),
                "participants": [str(x).strip() for x in (e.get("participants") or []) if str(x).strip()],
                "location": str(e.get("location", "")).strip(),
                "time_marker": str(e.get("time_marker", "")).strip(),
                "kind": str(e.get("kind", "")).strip() or "encounter",
                "effects": str(e.get("effects", "")).strip(),
            })
    return out


# ---------------- C1.b 人物快照 ----------------
_SNAPSHOT_SYS = (
    "你在为长篇小说做单章人物状态蒸馏。读完本章正文后，对**本章出场的每个角色**，"
    "给出本章结束时刻的状态快照。若正文片段只覆盖中段、无法确认章末状态，"
    "仍可抽取该片段状态，但 at_chapter_end 必须标为 false。绝不杜撰正文没有的信息。\n"
    "只输出 JSON：{\"characters\":[{"
    "\"name\":\"角色名\",\"location\":\"章末所在地\",\"physical_state\":\"身体/处境状态\","
    "\"emotional_state\":\"情绪状态\",\"goal_now\":\"当前目标\","
    "\"knows_new\":[\"本章新得知的关键信息\"],\"gained\":[\"本章获得的东西/能力/关系\"],"
    "\"lost\":[\"本章失去的东西/人/状态\"],"
    "\"relationship_changes\":[{\"with\":\"对象名\",\"delta\":\"关系如何变化\"}],"
    "\"at_chapter_end\":true或false}]}。"
)


def _extract_snapshots(llm: LLMClient, chapter_no: int, title: str, window: str) -> list[dict]:
    user = f"[第{chapter_no}章 {title}]（本章正文片段）\n{window}\n\n只输出 JSON。"
    data = _parse_json(llm.complete_at(_SNAPSHOT_SYS, user, EXTRACT_TEMPERATURE))
    if not isinstance(data, dict):
        return []
    out = []
    for c in (data.get("characters") or []):
        if isinstance(c, dict) and str(c.get("name", "")).strip():
            end_flag = c.get("at_chapter_end", c.get("present_at_chapter_end", False))
            out.append({
                "name": str(c.get("name", "")).strip(),
                "location": str(c.get("location", "")).strip(),
                "physical_state": str(c.get("physical_state", "")).strip(),
                "emotional_state": str(c.get("emotional_state", "")).strip(),
                "goal_now": str(c.get("goal_now", "")).strip(),
                "knows_new": [str(x).strip() for x in (c.get("knows_new") or []) if str(x).strip()],
                "gained": [str(x).strip() for x in (c.get("gained") or []) if str(x).strip()],
                "lost": [str(x).strip() for x in (c.get("lost") or []) if str(x).strip()],
                "relationship_changes": [r for r in (c.get("relationship_changes") or [])
                                         if isinstance(r, dict) and str(r.get("with", "")).strip()],
                "at_chapter_end": end_flag is True or str(end_flag).strip().lower() in ("true", "1", "yes", "是"),
            })
    return out


# ---------------- C1.c 设定 codex ----------------
_CODEX_SYS = (
    "你在为长篇小说做世界设定蒸馏。读完本章正文后，抽取本章出现或被进一步揭示的"
    "**世界设定/规则/物品/组织/术语/历史/地点细节**。每条必须能指回原文证据，绝不臆造。\n"
    "只输出 JSON：{\"codex\":[{"
    "\"name\":\"设定名\",\"type\":\"概念分类(如:能力体系/机构/信物/历史事件/地理)\","
    "\"kind\":\"rule|item|organization|term|location_detail|magic_system|history 之一\","
    "\"summary\":\"这条设定说了什么\",\"evidence_excerpt\":\"原文摘录(≤40字)\"}]}。"
)


def _extract_codex(llm: LLMClient, chapter_no: int, title: str, window: str) -> list[dict]:
    user = f"[第{chapter_no}章 {title}]（本章正文片段）\n{window}\n\n只输出 JSON。"
    data = _parse_json(llm.complete_at(_CODEX_SYS, user, EXTRACT_TEMPERATURE))
    if not isinstance(data, dict):
        return []
    out = []
    for c in (data.get("codex") or []):
        if isinstance(c, dict) and str(c.get("name", "")).strip():
            out.append({
                "name": str(c.get("name", "")).strip(),
                "type": str(c.get("type", "")).strip(),
                "kind": str(c.get("kind", "")).strip() or "term",
                "summary": str(c.get("summary", "")).strip(),
                "evidence_excerpt": str(c.get("evidence_excerpt", "")).strip()[:80],
            })
    return out


# ---------------- C1.e 伏笔候选 ----------------
_FORESHADOW_SYS = (
    "你在为长篇小说做伏笔蒸馏。读完本章正文后，抽取本章里"
    "**看似随意、却可能是作者刻意埋下、将来会回报**的细节（一句异常的话、反复出现的物件、"
    "没解释的反应、刻意的留白、被强调却没下文的名字/地点/能力）。宁可多报，配对留给后续步骤。"
    "绝不臆造正文没有的细节。\n"
    "只输出 JSON：{\"setups\":[{"
    "\"excerpt\":\"原文摘录(≤50字)\",\"what_planted\":\"埋下了什么\","
    "\"why_suspect\":\"为何判断是伏笔\",\"salience\":0.0到1.0 作者强调程度}]}。"
)


def _extract_foreshadow(llm: LLMClient, chapter_no: int, title: str, window: str) -> list[dict]:
    user = f"[第{chapter_no}章 {title}]（本章正文片段）\n{window}\n\n只输出 JSON。"
    data = _parse_json(llm.complete_at(_FORESHADOW_SYS, user, EXTRACT_TEMPERATURE))
    if not isinstance(data, dict):
        return []
    out = []
    for s in (data.get("setups") or []):
        if isinstance(s, dict) and str(s.get("excerpt", "")).strip():
            try:
                sal = max(0.0, min(1.0, float(s.get("salience", 0.5))))
            except Exception:
                sal = 0.5
            out.append({
                "excerpt": str(s.get("excerpt", "")).strip()[:120],
                "what_planted": str(s.get("what_planted", "")).strip(),
                "why_suspect": str(s.get("why_suspect", "")).strip(),
                "salience": sal,
            })
    return out


# ---------------- 章内合并（跨窗口去重） ----------------
def _dedup_events(events: list[dict]) -> list[dict]:
    out: list[dict] = []
    for e in events:
        if any(_gram_overlap(e["summary"], o["summary"], 4) >= 0.6 for o in out):
            continue
        out.append(e)
    return out


def _merge_snapshots(snaps: list[dict]) -> list[dict]:
    """同一角色多窗口快照：章末标记优先，否则取最后一个非空字段。"""
    by_name: dict[str, dict] = {}
    end_fields: dict[str, set[str]] = {}
    for s in snaps:
        cur = by_name.setdefault(s["name"], {"name": s["name"]})
        locked = end_fields.setdefault(s["name"], set())
        is_end = bool(s.get("at_chapter_end"))
        if is_end:
            cur["at_chapter_end"] = True
        for k, v in s.items():
            if k in ("name", "at_chapter_end"):
                continue
            if isinstance(v, list):
                merged = list(cur.get(k, []))
                for item in v:
                    if item not in merged:
                        merged.append(item)
                cur[k] = merged
            elif v and (is_end or k not in locked):
                cur[k] = v
                if is_end:
                    locked.add(k)
    return list(by_name.values())


def _dedup_codex(items: list[dict]) -> list[dict]:
    by_name: dict[str, dict] = {}
    for c in items:
        key = c["name"]
        if key not in by_name or len(c["summary"]) > len(by_name[key]["summary"]):
            by_name[key] = c
    return list(by_name.values())


def _dedup_foreshadow(items: list[dict]) -> list[dict]:
    out: list[dict] = []
    for s in items:
        if any(_gram_overlap(s["excerpt"], o["excerpt"], 4) >= 0.6 for o in out):
            continue
        out.append(s)
    return out


# ---------------- 单章编排 ----------------
def distill_one_chapter(llm: LLMClient, chapter_no: int, title: str, text: str) -> dict[str, Any]:
    """对一章跑完四件套（多窗口），返回合并后的结构（不落库，便于并发）。"""
    windows = _split_windows(text)
    events: list[dict] = []
    snaps: list[dict] = []
    codex: list[dict] = []
    setups: list[dict] = []
    for w in windows:
        if not w.strip():
            continue
        try:
            events.extend(_extract_events(llm, chapter_no, title, w))
        except Exception:
            pass
        try:
            snaps.extend(_extract_snapshots(llm, chapter_no, title, w))
        except Exception:
            pass
        try:
            codex.extend(_extract_codex(llm, chapter_no, title, w))
        except Exception:
            pass
        try:
            setups.extend(_extract_foreshadow(llm, chapter_no, title, w))
        except Exception:
            pass
    return {
        "chapter_no": chapter_no,
        "events": _dedup_events(events),
        "snapshots": _merge_snapshots(snaps),
        "codex": _dedup_codex(codex),
        "foreshadow": _dedup_foreshadow(setups),
    }


def _persist_chapter(repo: Repository, result: dict[str, Any], created_at: str) -> None:
    ch = result["chapter_no"]
    for i, e in enumerate(result["events"], 1):
        repo.insert_source_event(
            event_id=_sid("ev", ch, i, e["summary"]),
            chapter_no=ch, seq=i, summary=e["summary"], participants=e["participants"],
            location=e["location"], time_marker=e["time_marker"], kind=e["kind"],
            causes_from=[], effects=e["effects"], created_at=created_at,
        )
    for s in result["snapshots"]:
        repo.insert_character_snapshot(
            chapter_no=ch, character_name=s["name"],
            snapshot={k: v for k, v in s.items() if k != "name"}, changed_fields=[],
        )
    for c in result["codex"]:
        repo.upsert_codex(
            codex_id=_sid("cx", c["name"]), name=c["name"], type_=c["type"], kind=c["kind"],
            summary=c["summary"], evidence_chapter=ch, evidence_excerpt=c["evidence_excerpt"],
        )
    for s in result["foreshadow"]:
        repo.insert_foreshadow(
            setup_id=_sid("fs", ch, s["excerpt"]), chapter_no=ch, excerpt=s["excerpt"],
            what_planted=s["what_planted"], why_suspect=s["why_suspect"], salience=s["salience"],
        )


def distill_all_chapters(
    repo: Repository,
    llm: LLMClient,
    *,
    created_at: str = "",
    max_workers: int = 8,
    on_progress: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """C1 主入口：对所有 source_chapters 并发逐章蒸馏并落库。"""
    chapters = repo.list_source_chapters()
    total = len(chapters)
    if total == 0:
        return {"chapters": 0, "events": 0, "snapshots": 0, "codex": 0, "foreshadow": 0}
    repo.clear_distillation_artifacts()

    results: list[dict] = []
    done = [0]
    import threading
    lock = threading.Lock()

    def _job(ch) -> dict:
        r = distill_one_chapter(llm, ch.chapter_no, ch.title, ch.text)
        with lock:
            done[0] += 1
            if on_progress:
                on_progress(done[0], total)
        return r

    if max_workers <= 1:
        for ch in chapters:
            results.append(_job(ch))
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            results = list(ex.map(_job, chapters))

    # 落库需串行（SQLite 单连接）
    for r in sorted(results, key=lambda x: x["chapter_no"]):
        _persist_chapter(repo, r, created_at)

    return {
        "chapters": total,
        "events": sum(len(r["events"]) for r in results),
        "snapshots": sum(len(r["snapshots"]) for r in results),
        "codex": len({c["name"] for r in results for c in r["codex"]}),
        "foreshadow": sum(len(r["foreshadow"]) for r in results),
    }
