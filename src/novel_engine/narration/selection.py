"""选材 + 排序（设计文档 §4.1 / §4.2）。

§4.1 选材：不是每个事件都进小说。drama_score = f(冲突, 揭露/反转, 抉择, 不可逆, 主题相关)。
       高分→全文细写；低分→概述或跳过（变速 = 节奏）。
§4.2 排序：开场点选择器挑 drama_score 最高的瞬间作第 1 场（in medias res）；
       其之前的内容降级为闪回素材。discourse_order ≠ story_time。
"""
from __future__ import annotations

from dataclasses import dataclass

from ..models import Event
from ..repository import Repository


def compute_drama_score(repo: Repository, ev: Event, theme: str = "") -> float:
    """后置计算戏剧分（0..1 量级，可叠加）。基于事件已有的结构化信息，零额外 LLM。"""
    payload = ev.payload or {}
    score = 0.0
    # 抉择：带 chosen_value 的多为两难抉择产物
    if payload.get("chosen_value"):
        score += 0.4
    # 冲突/对峙：有对白
    if payload.get("dialogue"):
        score += 0.25
    # 揭露/内心波动
    if payload.get("inner_thought"):
        score += 0.15
    # 涉及多人在场（perceivers>1）→ 有见证、张力更高
    if len(ev.perceivers) > 1:
        score += 0.1
    # 主题相关
    text = " ".join(str(v) for v in payload.values() if v) + " " + ev.action_type
    if theme and any(c in text for c in theme if c.strip()):
        score += 0.2
    return round(min(score, 1.0), 3)


@dataclass
class SelectionResult:
    selected: list[Event]      # 进小说的高分事件（已按话语顺序排好）
    skipped: list[Event]       # 低分：概述或跳过
    opening_event_id: str      # 开场事件（in medias res）


def select_and_order(
    repo: Repository, theme: str = "", threshold: float = 0.5
) -> SelectionResult:
    events = repo.list_events()
    if not events:
        return SelectionResult([], [], "")

    scored: list[tuple[Event, float]] = []
    for ev in events:
        s = compute_drama_score(repo, ev, theme)
        repo.set_event_drama_score(ev.event_id, s)  # 后置标注
        scored.append((ev, s))

    selected = [ev for ev, s in scored if s >= threshold]
    skipped = [ev for ev, s in scored if s < threshold]
    if not selected:  # 阈值太高时，至少保留最高分一个，避免空稿
        selected = [max(scored, key=lambda t: t[1])[0]]
        skipped = [ev for ev, _ in scored if ev.event_id != selected[0].event_id]

    # 开场点选择器：选 drama_score 最高者作第 1 场（in medias res）
    opening = max(selected, key=lambda e: repo.get_event_drama_score(e.event_id) or 0.0)

    # 话语排序：开场在前；其余按故事时间。开场之前发生的 → 闪回素材（排在开场之后）
    rest = [e for e in selected if e.event_id != opening.event_id]
    before = sorted([e for e in rest if e.story_time < opening.story_time], key=lambda e: e.story_time)
    after = sorted([e for e in rest if e.story_time >= opening.story_time], key=lambda e: e.story_time)
    ordered = [opening] + after + before  # 闪回(before)放最后，话语序 ≠ 故事序

    return SelectionResult(ordered, skipped, opening.event_id)
