# -*- coding: utf-8 -*-
"""A0→A1→A2 蒸馏管线：上传作品 → AuthorWritingSheet。

A0 切片：按段落/章节切成 2k-4k 词片段
A1 对照提取：Average Author 基线 → delta → 四维 Claim-Evidence
A2 增量移动式合并：逐片 O(n) 合并

论文：arXiv:2502.13028 "Whose story is it?"
"""
from __future__ import annotations

import json
import re
from typing import Callable

from ..llm.base import LLMClient
from ..models import AuthorWritingSheet, StyleClaim
from .average_author import get_average_author

SEGMENT_MIN = 1500
SEGMENT_MAX = 4000
MAX_CLAIMS_PER_DIM = 10

_CHAPTER_SPLIT = re.compile(r"(?:^|\n)\s*第[零一二三四五六七八九十百千\d]+[章节回]\s*")


def _split_segments(text: str) -> list[str]:
    """A0 切片：优先按章节分；章节太长则按段落二分。"""
    chapters = [c.strip() for c in _CHAPTER_SPLIT.split(text) if len(c.strip()) >= SEGMENT_MIN]
    if not chapters:
        chapters = [text.strip()] if text.strip() else []

    segments: list[str] = []
    for ch in chapters:
        if len(ch) <= SEGMENT_MAX:
            segments.append(ch)
        else:
            paras = [p.strip() for p in ch.split("\n") if p.strip()]
            buf = ""
            for p in paras:
                if len(buf) + len(p) > SEGMENT_MAX and len(buf) >= SEGMENT_MIN:
                    segments.append(buf)
                    buf = p
                else:
                    buf = (buf + "\n" + p) if buf else p
            if len(buf) >= SEGMENT_MIN:
                segments.append(buf)
            elif buf and segments:
                segments[-1] += "\n" + buf
            elif buf:
                segments.append(buf)
    return segments or ([text[:SEGMENT_MAX]] if text.strip() else [])


def _reverse_prompt(llm: LLMClient, segment: str) -> str:
    """从片段反推 writing prompt（供 Average Author 生成对照版）。"""
    system = (
        "你是写作教练。阅读下面的小说片段，反向推断出一个简短的写作 prompt "
        "（50-80 字），描述这段文字要完成的叙事任务（场景/氛围/事件/人物状态变化）。"
        "只输出 prompt，不加任何前缀。"
    )
    try:
        return llm.complete(system, segment[:2000]).strip()
    except Exception:
        return "写一段小说片段。"


def _generate_average(llm: LLMClient, prompt: str, genre: str) -> str:
    """用 Average Author 基线生成"平均作者"版。"""
    avg = get_average_author(genre)
    system = avg["profile"] + "\n请根据以下 prompt 写一段 300-500 字的小说片段。只输出正文。"
    try:
        return llm.complete(system, prompt).strip()
    except Exception:
        return ""


def _extract_sheet(llm: LLMClient, real: str, average: str) -> dict:
    """A1 对照提取：对比真实文本 vs 平均文本的 delta → 四维 Claim-Evidence JSON。"""
    system = (
        "你是文风分析专家。下面有两段文本：【真实作者版】和【平均作者版】。\n"
        "对比两者差异，提取真实作者**独特的**风格特征（不是共有的、平庸的特征）。\n"
        "输出 JSON，四个维度各最多 5 条：\n"
        '{"plot":[{"claim":"...", "evidence":"原文摘录≤80字"}], '
        '"creativity":[...], "development":[...], "language":[...]}\n'
        "- plot: 情节结构偏好（如叙事视角、时间线处理、冲突构建）\n"
        "- creativity: 创意手法（如独特比喻、意象系统、打破常规的表达）\n"
        "- development: 发展节奏（如铺垫方式、转折密度、张弛比）\n"
        "- language: 语言运用（如句式节奏、用词偏好、语域、标点策略）\n"
        "claim 要锐利可执行（不要'文笔优美'这种废话），evidence 用原文原句。只输出 JSON。"
    )
    user = (
        f"【真实作者版】\n{real[:2500]}\n\n"
        f"【平均作者版】\n{average[:1500]}\n\n"
        "只输出 JSON。"
    )
    try:
        raw = llm.complete(system, user).strip().strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except Exception:
        try:
            o, c = raw.find("{"), raw.rfind("}")  # type: ignore[possibly-undefined]
            if 0 <= o < c:
                return json.loads(raw[o:c + 1])  # type: ignore[possibly-undefined]
        except Exception:
            pass
    return {}


def _parse_claims(data: dict, dim: str) -> list[StyleClaim]:
    items = data.get(dim, [])
    if not isinstance(items, list):
        return []
    claims = []
    for item in items[:MAX_CLAIMS_PER_DIM]:
        if isinstance(item, dict):
            claims.append(StyleClaim(
                claim=str(item.get("claim", "")).strip(),
                evidence=str(item.get("evidence", "")).strip()[:150],
            ))
        elif isinstance(item, str):
            claims.append(StyleClaim(claim=item.strip()))
    return [c for c in claims if c.claim]


def _merge_sheets(base: AuthorWritingSheet, new: AuthorWritingSheet,
                  llm: LLMClient | None = None) -> AuthorWritingSheet:
    """A2 增量移动式合并：归并等价 Claim、保留独特 Claim、每维 ≤10。"""
    if llm is None:
        merged = AuthorWritingSheet(
            name=base.name or new.name,
            source_genre=base.source_genre or new.source_genre,
            n_segments=base.n_segments + new.n_segments,
        )
        for dim in ("plot", "creativity", "development", "language"):
            base_claims = getattr(base, dim)
            new_claims = getattr(new, dim)
            existing_texts = {c.claim.lower() for c in base_claims}
            combined = list(base_claims)
            for c in new_claims:
                if c.claim.lower() not in existing_texts:
                    combined.append(c)
                    existing_texts.add(c.claim.lower())
            setattr(merged, dim, combined[:MAX_CLAIMS_PER_DIM])
        return merged

    system = (
        "你是文风分析合并专家。合并两份 Author Writing Sheet 的同一维度。\n"
        "规则：等价/重复的 claim 只保留表述更锐利的那条（合并 evidence）；"
        "独特的 claim 都保留；总数 ≤10 条。\n"
        '输出 JSON 数组：[{"claim":"...", "evidence":"..."}]。只输出 JSON。'
    )

    merged = AuthorWritingSheet(
        name=base.name or new.name,
        source_genre=base.source_genre or new.source_genre,
        n_segments=base.n_segments + new.n_segments,
    )

    def _merge_one_dim(dim: str) -> tuple[str, list[StyleClaim]]:
        base_claims = getattr(base, dim)
        new_claims = getattr(new, dim)
        if not new_claims:
            return dim, base_claims[:MAX_CLAIMS_PER_DIM]
        if not base_claims:
            return dim, new_claims[:MAX_CLAIMS_PER_DIM]
        base_json = json.dumps([{"claim": c.claim, "evidence": c.evidence} for c in base_claims],
                               ensure_ascii=False)
        new_json = json.dumps([{"claim": c.claim, "evidence": c.evidence} for c in new_claims],
                              ensure_ascii=False)
        user = f"维度：{dim}\n累积 sheet：{base_json}\n新片 sheet：{new_json}\n只输出合并后的 JSON 数组。"
        raw = ""
        try:
            raw = llm.complete(system, user).strip().strip("`")
            if raw.lower().startswith("json"):
                raw = raw[4:].strip()
            items = json.loads(raw)
            claims = []
            for item in (items if isinstance(items, list) else []):
                if isinstance(item, dict):
                    claims.append(StyleClaim(
                        claim=str(item.get("claim", "")).strip(),
                        evidence=str(item.get("evidence", "")).strip()[:150]))
            return dim, [c for c in claims if c.claim][:MAX_CLAIMS_PER_DIM]
        except Exception:
            # 容错：合并失败 → 走确定性去重合并，不让单维 LLM 抖动整轮挂掉
            existing = {c.claim.lower() for c in base_claims}
            combined = list(base_claims)
            for c in new_claims:
                if c.claim.lower() not in existing:
                    combined.append(c)
                    existing.add(c.claim.lower())
            return dim, combined[:MAX_CLAIMS_PER_DIM]

    # 4 个维度独立 → 并发跑（KeyPool 是线程安全的；非 KeyPool 也可串行 fallback）
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=4) as ex:
        for dim, claims in ex.map(_merge_one_dim, ("plot", "creativity", "development", "language")):
            setattr(merged, dim, claims)
    return merged


def _distill_one_segment(
    llm: LLMClient, seg: str, name: str, genre: str
) -> AuthorWritingSheet | None:
    """A1 单段对照提取：reverse_prompt → average → delta。返回 None 表示该段平均生成失败。"""
    prompt = _reverse_prompt(llm, seg)
    avg_text = _generate_average(llm, prompt, genre or "严肃文学")
    if not avg_text:
        return None
    delta = _extract_sheet(llm, seg, avg_text)
    return AuthorWritingSheet(
        name=name,
        source_genre=genre,
        plot=_parse_claims(delta, "plot"),
        creativity=_parse_claims(delta, "creativity"),
        development=_parse_claims(delta, "development"),
        language=_parse_claims(delta, "language"),
        n_segments=1,
    )


def distill_author_sheet(
    llm: LLMClient,
    text: str,
    name: str = "",
    genre: str = "",
    on_progress: Callable[[int, int], None] | None = None,
    max_workers: int = 1,
) -> AuthorWritingSheet:
    """完整管线：A0 切片 → A1 对照提取 → A2 增量合并 → AuthorWritingSheet。

    max_workers > 1 时，A1 各段并发抽取（独立无依赖），A2 仍按段顺序串行合并。
    传入的 llm 必须线程安全（KeyPoolClient 满足）。
    """
    segments = _split_segments(text)
    total = len(segments)
    accumulated: AuthorWritingSheet | None = None

    if total == 0:
        return AuthorWritingSheet(name=name, source_genre=genre)

    if max_workers <= 1 or total <= 1:
        # 串行
        for i, seg in enumerate(segments):
            if on_progress:
                on_progress(i, total)
            seg_sheet = _distill_one_segment(llm, seg, name, genre)
            if seg_sheet is None:
                continue
            if accumulated is None:
                accumulated = seg_sheet
            else:
                accumulated = _merge_sheets(accumulated, seg_sheet, llm if total > 3 else None)
    else:
        # 并发：A1 全部 segment 一起跑，A2 改成按轮 pairwise 归并，充分利用多 key。
        from concurrent.futures import ThreadPoolExecutor

        seg_sheets: list[AuthorWritingSheet | None] = [None] * total
        done = [0]
        done_lock = __import__("threading").Lock()

        def _job(idx: int, seg: str) -> None:
            try:
                seg_sheets[idx] = _distill_one_segment(llm, seg, name, genre)
            except Exception:
                seg_sheets[idx] = None
            with done_lock:
                done[0] += 1
                if on_progress:
                    on_progress(done[0], total)

        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            for idx, seg in enumerate(segments):
                ex.submit(_job, idx, seg)

        # A2 分轮并行归并。每轮保持原始顺序做 pairwise merge，语义稳定，耗时近似 O(log n) 轮。
        active = [sheet for sheet in seg_sheets if sheet is not None]
        merge_n = len(active)
        if merge_n == 1:
            accumulated = active[0]
        elif merge_n > 1:
            round_idx = 0
            import sys as _sys

            while len(active) > 1:
                round_idx += 1
                pair_count = len(active) // 2
                carry = active[-1] if len(active) % 2 == 1 else None
                merged_round: list[AuthorWritingSheet | None] = [None] * pair_count

                def _merge_job(pair_idx: int, left: AuthorWritingSheet, right: AuthorWritingSheet) -> None:
                    try:
                        merged_round[pair_idx] = _merge_sheets(left, right, llm if total > 3 else None)
                    except Exception as exc:
                        print(f"  [merge] round {round_idx} pair {pair_idx + 1}/{pair_count} FAILED: {exc!r}", flush=True)
                        merged_round[pair_idx] = _merge_sheets(left, right, None)

                workers = max(1, min(max_workers, pair_count))
                if pair_count > 0:
                    with ThreadPoolExecutor(max_workers=workers) as ex:
                        for pair_idx in range(pair_count):
                            ex.submit(_merge_job, pair_idx, active[pair_idx * 2], active[pair_idx * 2 + 1])

                active = [sheet for sheet in merged_round if sheet is not None]
                if carry is not None:
                    active.append(carry)
                print(f"  [merge-round] {round_idx}: remaining {len(active)}/{merge_n}", flush=True)
                _sys.stdout.flush()

            accumulated = active[0] if active else None

    if on_progress:
        on_progress(total, total)

    if accumulated is None:
        return AuthorWritingSheet(name=name, source_genre=genre)

    accumulated.name = name
    accumulated.source_genre = genre
    return accumulated
