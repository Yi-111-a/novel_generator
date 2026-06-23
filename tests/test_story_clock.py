from __future__ import annotations

import json

from novel_engine import db
from novel_engine.llm.base import LLMClient
from novel_engine.models import ChapterPlan, Event
from novel_engine.narration.story_clock import (
    audit_time_regression,
    capture_story_clock,
    clock_to_minutes,
    extract_chapter_clock,
    fold_timeline,
    format_minutes,
    roll_forward,
)
from novel_engine.repository import Repository


# --------------------------- 归一化 --------------------------- #
def test_clock_to_minutes_varied_forms():
    assert clock_to_minutes("7:15") == 7 * 60 + 15
    assert clock_to_minutes("凌晨四点十七分") == 4 * 60 + 17
    assert clock_to_minutes("凌晨四点") == 4 * 60
    assert clock_to_minutes("下午三点") == 15 * 60
    assert clock_to_minutes("晚上九点") == 21 * 60
    assert clock_to_minutes("七点十五分") == 7 * 60 + 15
    assert clock_to_minutes("十二点半") == 12 * 60 + 30
    assert clock_to_minutes("中午12点") == 12 * 60
    assert clock_to_minutes("傍晚") == 18 * 60
    # 模糊时间不强填
    assert clock_to_minutes("许久之后") is None
    assert clock_to_minutes("") is None
    assert clock_to_minutes(None) is None


def test_roll_forward_enforces_monotonic_across_midnight():
    # 上一章末=次日 04:17（abs=1697），本章抽到当日 06:00 → 落到次日 06:00（不倒流）
    prev = 1440 + 4 * 60 + 17
    assert roll_forward(6 * 60, prev) == 1440 + 6 * 60
    # 本章 03:00 早于 prev 当日分钟 → 滚到再次日
    assert roll_forward(3 * 60, prev) == 2 * 1440 + 3 * 60
    assert roll_forward(None, prev) is None


def test_format_minutes():
    assert format_minutes(4 * 60 + 17) == "04:17"
    assert format_minutes(1440 + 6 * 60) == "次日 06:00"
    assert format_minutes(2 * 1440 + 9 * 60 + 30) == "第3天 09:30"
    assert format_minutes(None) == ""


# --------------------------- 抽取 --------------------------- #
class _ClockLLM(LLMClient):
    def __init__(self, payload):
        self._payload = payload

    def complete(self, system, user):
        return self.complete_at(system, user, 0.0)

    def complete_at(self, system, user, temperature):
        if "时间抽取器" in system:
            return json.dumps(self._payload, ensure_ascii=False)
        return "{}"


def test_extract_chapter_clock_llm_with_deadline():
    payload = {
        "end_clock": "04:17", "day_offset": 1,
        "key_clocks": ["04:17"],
        "deadlines": [{"label": "7:15前救念念", "due": "07:15",
                       "due_day_offset": 1, "status": "open"}],
    }
    out = extract_chapter_clock(_ClockLLM(payload), "正文……凌晨四点十七分。", 8)
    assert out["end_clock"] == 1440 + 4 * 60 + 17
    assert out["deadlines"][0]["due"] == 1440 + 7 * 60 + 15
    assert out["deadlines"][0]["status"] == "open"


def test_extract_chapter_clock_regex_fallback_without_llm():
    prose = "他看了眼表，凌晨四点十七分，陈野早已离开店里。"
    out = extract_chapter_clock(None, prose, 8)
    assert out["end_clock"] == 4 * 60 + 17  # 正则兜底抓到末钟点


def test_extract_empty_when_no_time():
    out = extract_chapter_clock(None, "他沉默着，什么也没说。", 3)
    assert out["end_clock"] is None
    assert out["deadlines"] == []


# --------------------------- 落库 + 折叠 --------------------------- #
def test_capture_writes_timeline_and_event_clock():
    repo = Repository(db.connect(":memory:"))
    repo.append_event(Event(event_id="ev1", story_time=1, actors=[], action_type="x"))
    payload = {
        "end_clock": "04:17", "day_offset": 1, "key_clocks": [],
        "deadlines": [{"label": "7:15前救念念", "due": "07:15",
                       "due_day_offset": 1, "status": "open"}],
    }
    chapter = ChapterPlan("c8", "a", 8)
    entry = capture_story_clock(repo, chapter, "凌晨四点十七分。", _ClockLLM(payload), ["ev1"])
    assert entry["end_clock"] == 1440 + 4 * 60 + 17
    # events.story_clock 回填，story_time(tick) 未被覆盖
    ev = repo.get_event("ev1")
    assert ev.story_clock == 1440 + 4 * 60 + 17
    assert ev.story_time == 1
    # timeline 落库
    tl = repo.get_story_timeline()
    assert tl[0]["chapter_no"] == 8


def test_fold_timeline_active_deadlines_and_close():
    repo = Repository(db.connect(":memory:"))
    repo.upsert_timeline_entry({
        "chapter_no": 8, "end_clock": 1440 + 4 * 60 + 17, "end_clock_text": "次日 04:17",
        "deadlines": [{"label": "救念念", "due": 1440 + 7 * 60 + 15,
                       "due_text": "次日 07:15", "status": "open", "source_chapter": 8}],
    })
    folded = fold_timeline(repo.get_story_timeline(), before_chapter=13)
    assert folded["last_end_clock"] == 1440 + 4 * 60 + 17
    assert len(folded["active_deadlines"]) == 1
    # ch10 关闭该死线 → 不再活跃
    repo.upsert_timeline_entry({
        "chapter_no": 10, "end_clock": 1440 + 7 * 60 + 20, "end_clock_text": "次日 07:20",
        "deadlines": [{"label": "救念念", "due": 1440 + 7 * 60 + 15,
                       "due_text": "次日 07:15", "status": "met", "source_chapter": 10}],
    })
    folded2 = fold_timeline(repo.get_story_timeline(), before_chapter=13)
    assert folded2["active_deadlines"] == []
    assert folded2["last_end_clock"] == 1440 + 7 * 60 + 20


def test_fold_ranks_future_deadline_before_stale_past_one():
    """已过期但未关闭的 open 死线不得抢占"最近目标"；未来死线优先。"""
    repo = Repository(db.connect(":memory:"))
    repo.upsert_timeline_entry({
        "chapter_no": 1, "end_clock": 0, "end_clock_text": "00:00",
        "deadlines": [{"label": "旧工单", "due": 4320, "due_text": "第4天 00:00",
                       "status": "open", "source_chapter": 1}],  # 早已过去
    })
    repo.upsert_timeline_entry({
        "chapter_no": 8, "end_clock": 5 * 1440 + 60, "end_clock_text": "第6天 01:00",
        "deadlines": [{"label": "救念念", "due": 5 * 1440 + 7 * 60 + 15, "due_text": "第6天 07:15",
                       "status": "open", "source_chapter": 8}],  # 仍在未来
    })
    folded = fold_timeline(repo.get_story_timeline(), before_chapter=13)
    assert folded["active_deadlines"][0]["label"] == "救念念"  # 未来死线排第一


def test_capture_is_noop_without_time_data():
    repo = Repository(db.connect(":memory:"))
    entry = capture_story_clock(repo, ChapterPlan("c3", "a", 3), "纯对话无时间。", None, [])
    assert entry is None
    assert repo.get_story_timeline() == []


# --------------------------- 阶段3·确定性兜底闸 --------------------------- #
def _seed_prev_clock(repo, end_clock=4 * 60 + 17):
    repo.upsert_timeline_entry({
        "chapter_no": 8, "end_clock": end_clock, "end_clock_text": format_minutes(end_clock),
        "deadlines": [],
    })


def test_time_regression_flags_backward_clock():
    repo = Repository(db.connect(":memory:"))
    _seed_prev_clock(repo)  # 上一章末 04:17
    ch = ChapterPlan("c13", "a", 13)
    viol = audit_time_regression(repo, ch, "凌晨四点的槐荫巷44号，灯还亮着。")  # 04:00 < 04:17
    assert viol is not None
    assert viol["type"] == "story_clock_regression"


def test_time_regression_skips_with_day_marker():
    repo = Repository(db.connect(":memory:"))
    _seed_prev_clock(repo)
    ch = ChapterPlan("c13", "a", 13)
    # 显式写"次日" → 不判倒流
    assert audit_time_regression(repo, ch, "次日凌晨四点，槐荫巷44号。") is None


def test_time_regression_skips_when_later():
    repo = Repository(db.connect(":memory:"))
    _seed_prev_clock(repo)
    ch = ChapterPlan("c13", "a", 13)
    assert audit_time_regression(repo, ch, "早上七点，校车快来了。") is None  # 07:00 > 04:17


# --------------------------- 方案二·死线归并 + 到点收口 --------------------------- #
def test_deadlines_merge_by_similar_label_across_chapters():
    """同一条死线换名字跨章出现 → 按相似度归并成一条，按 id 也归并。"""
    repo = Repository(db.connect(":memory:"))
    repo.upsert_timeline_entry({"chapter_no": 8, "end_clock": 1440 + 60, "deadlines": [
        {"id": "dl_a", "label": "七点十五前救念念", "due": 1440 + 7 * 60 + 15,
         "due_text": "次日 07:15", "status": "open", "source_chapter": 8}]})
    # 换了名字、没带 id（老式抽取）→ 应归并到 dl_a，不新增第二条
    repo.upsert_timeline_entry({"chapter_no": 10, "end_clock": 1440 + 2 * 60, "deadlines": [
        {"label": "七点十五前赶到救念念", "due": 1440 + 7 * 60 + 15,
         "due_text": "次日 07:15", "status": "open", "source_chapter": 10}]})
    f = fold_timeline(repo.get_story_timeline(), before_chapter=13)
    assert len(f["active_deadlines"]) == 1  # 归并成一条


def test_deadline_closed_by_status_update():
    """用相同 id 回报 met → 死线关闭，不再活跃。"""
    repo = Repository(db.connect(":memory:"))
    repo.upsert_timeline_entry({"chapter_no": 8, "end_clock": 100, "deadlines": [
        {"id": "dl_x", "label": "救念念", "due": 435, "due_text": "07:15",
         "status": "open", "source_chapter": 8}]})
    repo.upsert_timeline_entry({"chapter_no": 9, "end_clock": 500, "deadlines": [
        {"id": "dl_x", "label": "救念念", "due": 435, "due_text": "07:15",
         "status": "met", "source_chapter": 9}]})
    f = fold_timeline(repo.get_story_timeline(), before_chapter=13)
    assert f["active_deadlines"] == [] and f["overdue_deadlines"] == []


def test_overdue_deadline_auto_closes_to_overdue():
    """钟点走过 due 仍 open → 自动判 overdue（从活跃里移出，供 planner 收口）。"""
    repo = Repository(db.connect(":memory:"))
    repo.upsert_timeline_entry({"chapter_no": 8, "end_clock": 435, "deadlines": [
        {"id": "dl_y", "label": "救念念", "due": 435, "due_text": "07:15",
         "status": "open", "source_chapter": 8}]})
    repo.upsert_timeline_entry({"chapter_no": 9, "end_clock": 600, "deadlines": []})  # 钟点已过 07:15
    f = fold_timeline(repo.get_story_timeline(), before_chapter=13)
    assert f["active_deadlines"] == []
    assert len(f["overdue_deadlines"]) == 1 and f["overdue_deadlines"][0]["label"] == "救念念"


def test_extract_assigns_id_and_reuses_for_active():
    """抽取给死线发稳定 id；命中活跃死线则复用其 id（不另起）。"""
    payload = {"end_clock": "06:00", "day_offset": 1, "key_clocks": [],
               "deadlines": [{"label": "赶在七点十五前救念念", "due": "07:15",
                              "due_day_offset": 1, "status": "open"}]}
    active = [{"id": "dl_keep", "label": "七点十五救念念", "due": 1440 + 7 * 60 + 15,
               "due_text": "次日 07:15", "status": "open"}]
    out = extract_chapter_clock(_ClockLLM(payload), "正文", 10, prev_abs=1440 + 60,
                                active_deadlines=active)
    assert out["deadlines"][0]["id"] == "dl_keep"  # 归并复用，不发新 id
