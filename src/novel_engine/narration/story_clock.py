"""结构化故事时钟 Story Clock：给规划层一个**可比较的时间观念**，治本解决
"钟点只散落在非结构化 fact 文本里 → Planner 排章时随手写一个更早的时间 → 时间倒流"。

设计是**纯附加层**：无 LLM、抽不到任何钟点时全部空操作，不改变既有行为。

三个职责：
  1) 把中文/数字钟点归一化为"距故事第 0 天 0 点的分钟数"（含跨日 day offset）；模糊时间返回 None，
     绝不强填精度（避免幻觉）。
  2) 从本章正文抽 {章末时间, 关键事件时间, 新增/推进的死线}（LLM 抽 JSON + 正则兜底）。
  3) 折叠 timeline（project_meta 里的轻量 JSON）得到「上一章末时间」+「当前活跃死线」，供 Planner
     施加硬约束、供确定性审计兜底。
"""
from __future__ import annotations

import json
import re
import uuid
from contextlib import nullcontext
from typing import Any

MINUTES_PER_DAY = 1440

# 时段词 → (无显式钟点时的代表分钟, 12→24 小时换算类别)。
#   am0   ：凌晨/午夜后，钟点原样（0–5 点）。  am  ：上午，钟点原样（5–11 点）。
#   noon  ：正午，12 点。                      pm  ：午后/傍晚/夜晚，<12 的钟点 +12。
#   lateam：深夜/半夜，钟点原样当凌晨（0–5 点）。
_PERIODS: dict[str, tuple[int, str]] = {
    "凌晨": (4 * 60, "am0"),
    "清晨": (6 * 60, "am"),
    "拂晓": (5 * 60, "am0"),
    "破晓": (5 * 60, "am0"),
    "早晨": (6 * 60, "am"),
    "早上": (7 * 60, "am"),
    "上午": (9 * 60, "am"),
    "中午": (12 * 60, "noon"),
    "正午": (12 * 60, "noon"),
    "晌午": (12 * 60, "noon"),
    "午后": (15 * 60, "pm"),
    "下午": (15 * 60, "pm"),
    "傍晚": (18 * 60, "pm"),
    "黄昏": (18 * 60, "pm"),
    "入夜": (19 * 60, "pm"),
    "晚上": (20 * 60, "pm"),
    "晚间": (20 * 60, "pm"),
    "夜里": (22 * 60, "pm"),
    "夜晚": (22 * 60, "pm"),
    "半夜": (1 * 60, "lateam"),
    "深夜": (23 * 60 + 30, "lateam"),
    "午夜": (0, "lateam"),
    "子夜": (0, "lateam"),
}
# 较长的时段词排前，避免"早上"被"早"截断
_PERIOD_KEYS = sorted(_PERIODS, key=len, reverse=True)
_PERIOD_ALT = "|".join(_PERIOD_KEYS)

_ZH_DIGIT = {"零": 0, "〇": 0, "一": 1, "两": 2, "二": 2, "三": 3, "四": 4,
             "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _zh_num(token: str) -> int | None:
    """解析 0–59 的中文/阿拉伯数字。支持 十/二十/二十三/十五 等。"""
    token = (token or "").strip()
    if not token:
        return None
    if token.isdigit():
        return int(token)
    if "十" not in token:
        if len(token) == 1 and token in _ZH_DIGIT:
            return _ZH_DIGIT[token]
        # 多位连写（如"四十"已含十；"五七"不规范）→ 尝试逐位
        if all(c in _ZH_DIGIT for c in token):
            return _ZH_DIGIT[token[-1]]
        return None
    # 含"十"
    tens, _, ones = token.partition("十")
    t = _ZH_DIGIT.get(tens, 1) if tens else 1
    o = _ZH_DIGIT.get(ones, 0) if ones else 0
    val = t * 10 + o
    return val if val < 100 else None


def _apply_period(hour: int, kind: str) -> int:
    if kind == "pm" and hour < 12:
        return hour + 12
    if kind == "noon":
        return 12 if hour == 12 else hour
    # am0 / am / lateam：钟点原样（12 点视作 0）
    if hour == 12 and kind in ("am0", "lateam"):
        return 0
    return hour


def _minute_phrase(raw: str | None) -> int:
    """点后的"分"短语：'半'=30，'一刻'=15，'三刻'=45，否则数字。"""
    if not raw:
        return 0
    raw = raw.strip()
    if raw in ("半",):
        return 30
    if "刻" in raw:
        n = _zh_num(raw.replace("刻", "")) or 1
        return min(n, 3) * 15
    n = _zh_num(raw.replace("分", "").strip())
    return n if (n is not None and 0 <= n < 60) else 0


def clock_to_minutes(text: str | None) -> int | None:
    """把单个钟点短语归一化为**当日**分钟数（0–1439）。模糊/无钟点 → None。

    识别：HH:MM、X点Y分、X时半、（凌晨/下午/晚上…）X点、纯时段词。
    """
    if not text:
        return None
    s = str(text).strip()
    # 1) HH:MM / HH：MM（可带前缀时段词）
    m = re.search(rf"(?:({_PERIOD_ALT}))?\s*([0-2]?\d)\s*[:：]\s*([0-5]\d)", s)
    if m:
        period, hh, mm = m.group(1), int(m.group(2)), int(m.group(3))
        if hh < 24:
            if period:
                hh = _apply_period(hh, _PERIODS[period][1])
            return (hh % 24) * 60 + mm
    # 2) （时段词）X点[Y分/半/一刻]
    m = re.search(
        rf"(?:({_PERIOD_ALT}))?\s*([0-9]{{1,2}}|[零〇一两二三四五六七八九十]+)\s*[点时]"
        rf"\s*((?:[0-5]?\d|半|[零〇一两二三四五六七八九十]+)\s*(?:分|刻)?)?",
        s,
    )
    if m:
        period = m.group(1)
        hour = _zh_num(m.group(2))
        if hour is not None and 0 <= hour <= 24:
            minute = _minute_phrase(m.group(3))
            if period:
                hour = _apply_period(hour, _PERIODS[period][1])
            return (hour % 24) * 60 + minute
    # 3) 纯时段词 → 代表分钟
    for key in _PERIOD_KEYS:
        if key in s:
            return _PERIODS[key][0]
    return None


def to_absolute(within_day: int | None, day_offset: int = 0) -> int | None:
    """当日分钟 + 天偏移 → 绝对分钟（距故事第 0 天 0 点）。"""
    if within_day is None:
        return None
    return int(day_offset) * MINUTES_PER_DAY + int(within_day)


def roll_forward(within_day: int | None, prev_abs: int | None) -> int | None:
    """正文未明写天数时，按"不得倒流"把当日分钟落到 ≥ 上一章末的最近一天。

    例：上一章末=次日 04:17（abs=1697），本章抽到当日 06:00（360）→ 落到次日 06:00（1800），
    而不是第 0 天 06:00（会倒流）。跨度限制 1 天，避免凭空跳很多天。
    """
    if within_day is None:
        return None
    if prev_abs is None:
        return within_day
    base_day = prev_abs // MINUTES_PER_DAY
    abs_same = base_day * MINUTES_PER_DAY + within_day
    if abs_same >= prev_abs:
        return abs_same
    return abs_same + MINUTES_PER_DAY  # 落到次日


def format_minutes(m: int | None) -> str:
    """绝对分钟 → 人类可读（'04:17' / '次日 06:00' / '第3天 09:30'）。"""
    if m is None:
        return ""
    m = int(m)
    day, within = divmod(m, MINUTES_PER_DAY)
    hh, mm = divmod(within, 60)
    clock = f"{hh:02d}:{mm:02d}"
    if day <= 0:
        return clock
    if day == 1:
        return f"次日 {clock}"
    return f"第{day + 1}天 {clock}"


# --------------------------------------------------------------------------- #
# 正则兜底：从正文直接抓钟点 / 死线（LLM 不可用或返回空时）
# --------------------------------------------------------------------------- #
_DEADLINE_REMAIN_RE = re.compile(
    r"(?:还剩|剩|仅剩|只剩|距[^，。；\n]{0,8}?还有)\s*"
    r"([0-9]{1,3}|[零〇一两二三四五六七八九十]+)\s*(小时|个小时|分钟|分)"
)


def regex_clocks(prose: str) -> list[int]:
    """正文里所有可识别的当日钟点（去重、按出现顺序）。"""
    if not prose:
        return []
    out: list[int] = []
    seen: set[int] = set()
    pattern = re.compile(
        rf"(?:{_PERIOD_ALT})?\s*(?:[0-2]?\d\s*[:：]\s*[0-5]\d"
        rf"|(?:[0-9]{{1,2}}|[零〇一两二三四五六七八九十]+)\s*[点时]"
        rf"\s*(?:[0-5]?\d|半|[零〇一两二三四五六七八九十]+)?\s*(?:分|刻)?)"
    )
    for mt in pattern.finditer(prose):
        val = clock_to_minutes(mt.group(0))
        if val is not None and val not in seen:
            seen.add(val)
            out.append(val)
    return out


# --------------------------------------------------------------------------- #
# LLM 抽取（+ 正则兜底）
# --------------------------------------------------------------------------- #
def _safe_json(raw: str) -> dict[str, Any] | None:
    text = (raw or "").strip().strip("`")
    if text.lower().startswith("json"):
        text = text[4:].strip()
    start, end = text.find("{"), text.rfind("}")
    try:
        data = json.loads(text[start : end + 1] if 0 <= start < end else text)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _scope(llm, caller: str, chapter_no: int):
    scope = getattr(llm, "scope", None)
    if callable(scope):
        return scope(caller=caller, meta={"chapter_no": chapter_no})
    return nullcontext()


_DEADLINE_STATUS = {"open", "met", "missed", "cancelled"}
_CLOSED_STATUS = {"met", "missed", "cancelled"}


def _label_grams(s: str) -> set[str]:
    """死线 label 的 2-gram 指纹（只取中文/字母数字），用于跨章归并同一条死线。"""
    t = "".join(ch for ch in (s or "") if "一" <= ch <= "鿿" or ch.isalnum())
    return {t[i:i + 2] for i in range(len(t) - 1)}


def _labels_similar(a: str, b: str, thresh: float = 0.34) -> bool:
    ga, gb = _label_grams(a), _label_grams(b)
    if not ga or not gb:
        return False
    inter, union = len(ga & gb), len(ga | gb)
    return union > 0 and inter / union >= thresh


def _same_deadline(label: str, due: int | None, other: dict[str, Any]) -> bool:
    """同一条死线判定：label 相似(2-gram Jaccard) 或 (due 相近且 label 有词重叠)。
    治"每章给同一死线起不同名字 → 关不掉"的归并问题。"""
    o_label = str(other.get("label", ""))
    if _labels_similar(label, o_label):
        return True
    o_due = other.get("due")
    if (due is not None and isinstance(o_due, (int, float)) and abs(due - int(o_due)) <= 120
            and (_label_grams(label) & _label_grams(o_label))):
        return True
    return False


def _resolve_deadline_id(label: str, due: int | None, given_id: str,
                         actives: list[dict[str, Any]]) -> str:
    """给本章死线定身份 id：LLM 显式给的 id 命中活跃死线就用它；否则按相似度归并到已有死线；
    都不命中才发新 id。"""
    if given_id:
        for a in actives:
            if a.get("id") == given_id:
                return given_id
    for a in actives:
        if _same_deadline(label, due, a) and a.get("id"):
            return str(a["id"])
    return f"dl_{uuid.uuid4().hex[:8]}"


def extract_chapter_clock(
    llm,
    prose: str,
    chapter_no: int,
    prev_abs: int | None = None,
    active_deadlines: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """抽取本章 {章末绝对时间, 关键事件绝对时间, 死线}。

    返回 {"end_clock": int|None, "end_clock_text": str,
          "key_clocks": [int], "deadlines": [{label, due, due_text, status, source_chapter}]}。
    全空（无钟点、无死线）也是合法返回 —— 调用方据此空操作。
    """
    prose = (prose or "").strip()
    result: dict[str, Any] = {
        "end_clock": None, "end_clock_text": "", "key_clocks": [], "deadlines": [],
    }
    if not prose:
        return result

    actives = [a for a in (active_deadlines or []) if isinstance(a, dict)]
    data: dict[str, Any] | None = None
    if llm is not None:
        system = (
            "你是小说时间抽取器。从本章正文抽取**明确写出或可直接推断**的钟点，"
            "模糊的时间（如『许久之后』『某天』）不要强行精确。只输出JSON："
            '{"end_clock":"HH:MM或null","day_offset":0,'
            '"key_clocks":["HH:MM"],'
            '"deadlines":[{"id":"已有死线的id或留空","label":"死线简述","due":"HH:MM或null",'
            '"due_day_offset":0,"status":"open|met|missed|cancelled"}]}。'
            "end_clock=本章叙事**结束时**的钟点；day_offset=相对故事开篇的天数偏移"
            "（同一天填0，跨到次日填1，依此类推）。"
            "deadlines=本章出现或推进的**限期/倒计时**（如『7:15前必须赶到救念念』），"
            "status：仍未到=open，已达成=met，已错过=missed，已取消=cancelled。"
            "若提供了[active_deadlines]（前面章节立下、仍未了结的死线），判断本章是否让其中某条"
            "**达成/错过/取消**——是则在 deadlines 里用它**相同的 id** 回报并更新 status，"
            "**不要**给同一条死线另起新名字。只有正文真正**新立**的限期才用新 label、id 留空。"
            "没有任何明确钟点时 end_clock 填 null、数组留空。"
        )
        active_brief = [{"id": a.get("id"), "label": a.get("label"),
                         "due": a.get("due_text") or "", "status": a.get("status")}
                        for a in actives]
        user = (f"[chapter]{chapter_no}\n"
                f"[active_deadlines]{json.dumps(active_brief, ensure_ascii=False)}\n"
                f"[prose]\n{prose[:16000]}\n\n只输出JSON。")
        try:
            with _scope(llm, "story_clock_extract", chapter_no):
                data = _safe_json(llm.complete_at(system, user, 0.0))
        except Exception:
            data = None

    if data:
        end_within = clock_to_minutes(str(data.get("end_clock") or "")) if data.get("end_clock") else None
        end_day = data.get("day_offset")
        if end_within is not None:
            if isinstance(end_day, (int, float)):
                result["end_clock"] = to_absolute(end_within, int(end_day))
            else:
                result["end_clock"] = roll_forward(end_within, prev_abs)
        for c in data.get("key_clocks") or []:
            w = clock_to_minutes(str(c))
            if w is not None:
                result["key_clocks"].append(roll_forward(w, prev_abs))
        # 死线以**当前故事时间**为锚展望未来：取本章末与上一章末中较晚者（章末不得早于上一章，
        # 原始抽取偶尔给出更早的 day-0 钟点，这里按单调时间纠正，避免死线被错钉到过去）。
        _candidates = [c for c in (result["end_clock"], prev_abs) if c is not None]
        anchor = max(_candidates) if _candidates else None
        for d in data.get("deadlines") or []:
            if not isinstance(d, dict):
                continue
            label = str(d.get("label", "")).strip()
            if not label:
                continue
            status = str(d.get("status", "open")).strip().lower()
            if status not in _DEADLINE_STATUS:
                status = "open"
            due_within = clock_to_minutes(str(d.get("due") or "")) if d.get("due") else None
            due_day = d.get("due_day_offset")
            if due_within is None:
                due_abs = None
            elif isinstance(due_day, (int, float)):
                due_abs = to_absolute(due_within, int(due_day))
            else:
                due_abs = roll_forward(due_within, anchor)
            # 仍未到(open)的死线却落在当前钟点之前 → LLM 的 day_offset 不可信，按"展望未来"重锚
            if (due_abs is not None and status == "open" and anchor is not None
                    and due_abs < anchor and due_within is not None):
                due_abs = roll_forward(due_within, anchor)
            # 方案二·归并：给死线定稳定 id（命中已有死线则复用，杜绝换名字关不掉）
            dl_id = _resolve_deadline_id(label, due_abs, str(d.get("id", "")).strip(), actives)
            # 复用已有死线时，due 缺失就沿用旧的（避免更新状态时把时限丢了）
            if due_abs is None:
                for a in actives:
                    if a.get("id") == dl_id and isinstance(a.get("due"), (int, float)):
                        due_abs = int(a["due"])
                        break
            result["deadlines"].append({
                "id": dl_id, "label": label, "due": due_abs,
                "due_text": format_minutes(due_abs),
                "status": status, "source_chapter": chapter_no,
            })

    # 正则兜底：LLM 没给出 end_clock 时，取正文最后一个可识别钟点
    if result["end_clock"] is None:
        clocks = regex_clocks(prose)
        if clocks:
            result["end_clock"] = roll_forward(clocks[-1], prev_abs)
            if not result["key_clocks"]:
                result["key_clocks"] = [roll_forward(c, prev_abs) for c in clocks]

    result["end_clock_text"] = format_minutes(result["end_clock"])
    return result


# --------------------------------------------------------------------------- #
# timeline 折叠：上一章末 + 活跃死线（供 Planner / 审计）
# --------------------------------------------------------------------------- #
def capture_story_clock(repo, chapter, prose: str, llm, event_ids: list[str] | None = None) -> dict[str, Any] | None:
    """阶段1·抽取与落库：从本章正文抽 {章末时间, 关键事件时间, 死线} →
    写入 events.story_clock + project_meta.timeline。

    纯附加层：无 chapter / 无 prose / 抽不到任何钟点与死线时返回 None（空操作）。
    """
    if chapter is None or not (prose or "").strip():
        return None
    chapter_no = int(getattr(chapter, "sequence_order", 0) or 0)
    if chapter_no <= 0:
        return None
    folded = fold_timeline(repo.get_story_timeline(), before_chapter=chapter_no)
    prev_abs = folded.get("last_end_clock")
    # 把仍未了结的死线喂给抽取器，让本章可按 id 关掉它们（而非每章另起新名字）
    active = (folded.get("active_deadlines") or []) + (folded.get("overdue_deadlines") or [])
    payload = extract_chapter_clock(llm, prose, chapter_no, prev_abs, active_deadlines=active)
    end_clock = payload.get("end_clock")
    deadlines = payload.get("deadlines") or []
    if end_clock is None and not deadlines:
        return None  # 本章没有任何可结构化的时间信息
    # 不得倒流：timeline 末时间钳为非递减（LLM 给错 day_offset 时兜底）
    if end_clock is not None and prev_abs is not None and end_clock < prev_abs:
        end_clock = prev_abs
        payload["end_clock"] = end_clock
        payload["end_clock_text"] = format_minutes(end_clock)
    entry = {
        "chapter_no": chapter_no,
        "end_clock": end_clock,
        "end_clock_text": format_minutes(end_clock),
        "deadlines": deadlines,
    }
    repo.upsert_timeline_entry(entry)
    if end_clock is not None:
        repo.set_events_story_clock(list(event_ids or []), end_clock)
    return entry


_DAY_ADVANCE_RE = re.compile(
    r"次日|翌日|第二天|第三天|隔天|转天|越日|几天[后過过]|数日[后過过]|"
    r"过了一夜|一夜过去|一夜之间|又一[天日]"
)


def audit_time_regression(repo, ch, prose: str) -> dict[str, Any] | None:
    """阶段3·确定性兜底闸：本章正文的钟点若**明显早于上一章末**（同日、无跨日标记、
    且所有可识别钟点都更早、回退在 6 小时内），判为时间倒流。

    刻意保守（仅在高把握时触发），避免误伤合法的次日清晨/闪回：fact_delta 已做事后**语义**
    拦截（canonical_fact_conflict=P0），这条只是零成本的**确定性**补网。命中返回违规 dict，否则 None。
    """
    if not (prose or "").strip() or ch is None:
        return None
    chapter_no = int(getattr(ch, "sequence_order", 0) or 0)
    if chapter_no <= 1:
        return None
    folded = fold_timeline(repo.get_story_timeline(), before_chapter=chapter_no)
    prev_abs = folded.get("last_end_clock")
    if prev_abs is None:
        return None
    if _DAY_ADVANCE_RE.search(prose):
        return None  # 正文已明示跨日 → 不是倒流
    clocks = regex_clocks(prose)
    if not clocks:
        return None
    prev_within = int(prev_abs) % MINUTES_PER_DAY
    mx = max(clocks)
    if mx < prev_within and 0 < (prev_within - mx) <= 6 * 60:
        return {
            "type": "story_clock_regression",
            "text": (f"本章钟点（最晚约{format_minutes(mx)}）早于上一章末"
                     f"（{folded.get('last_end_text')}），疑似时间倒流。"),
            "advice": (f"把本章的钟点/时段调整到不早于上一章末（{folded.get('last_end_text')}），"
                       "时间只能往后走；若确为次日请显式写出『次日/第二天』。"),
            "repair_scope": "sentence",
        }
    return None


def fold_timeline(
    timeline: list[dict[str, Any]] | None,
    before_chapter: int | None = None,
) -> dict[str, Any]:
    """把 project_meta.timeline 折叠成当前时间态。

    before_chapter 给定时只看其之前的章（规划"下一章"用，避免把本章自己算进去）。
    返回 {last_end_clock, last_end_text, last_end_chapter,
          active_deadlines: 仍未到的开放死线（按最近优先排序）,
          overdue_deadlines: 时刻已过却仍未了结的死线（供 planner 触发"正面收掉"）}。

    方案二·归并：死线按稳定 id 去重（老数据无 id 时按 label 相似度归并），同 id/同义只留最新状态；
    达成/错过/取消 即关闭；钟点走过 due 仍 open 的自动判为 overdue（到点收口）。"""
    out: dict[str, Any] = {
        "last_end_clock": None, "last_end_text": "", "last_end_chapter": None,
        "active_deadlines": [], "overdue_deadlines": [],
    }
    rows = [r for r in (timeline or []) if isinstance(r, dict)]
    if before_chapter is not None:
        rows = [r for r in rows if int(r.get("chapter_no", 0) or 0) < int(before_chapter)]
    rows.sort(key=lambda r: int(r.get("chapter_no", 0) or 0))
    tracked: dict[str, dict[str, Any]] = {}  # 稳定 key → 最新死线状态
    for r in rows:
        ec = r.get("end_clock")
        if isinstance(ec, (int, float)):
            out["last_end_clock"] = int(ec)
            out["last_end_text"] = format_minutes(int(ec))
            out["last_end_chapter"] = int(r.get("chapter_no", 0) or 0)
        for d in r.get("deadlines") or []:
            if not isinstance(d, dict) or not str(d.get("label", "")).strip():
                continue
            key = str(d.get("id") or "").strip()
            if not key:  # 老数据无 id：按相似度归并到已跟踪的某条
                key = next((k for k, v in tracked.items()
                            if _same_deadline(str(d["label"]), d.get("due"), v)), "")
                key = key or f"label::{str(d['label']).strip()}"
            tracked[key] = {**d, "id": d.get("id") or key}
    now = out["last_end_clock"]
    open_active, overdue = [], []
    for d in tracked.values():
        if str(d.get("status", "open")) in _CLOSED_STATUS:
            continue
        due = d.get("due")
        if now is not None and isinstance(due, (int, float)) and due < now:
            overdue.append(d)            # 到点未结 → 自动收口为 overdue
        else:
            open_active.append(d)

    def _rank(d: dict[str, Any]) -> tuple:
        due = d.get("due")
        return (due is None, due if due is not None else 0)

    out["active_deadlines"] = sorted(open_active, key=_rank)
    out["overdue_deadlines"] = sorted(overdue, key=lambda d: d.get("due") or 0)
    return out
