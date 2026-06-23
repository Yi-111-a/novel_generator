"""方案一·章纲强承接：下一章 beat1 必须承接上一章实际结尾。"""
from __future__ import annotations

import json

from novel_engine import db
from novel_engine.llm.base import LLMClient
from novel_engine.models import (
    AcceptedChapterRecord,
    Arc,
    ChapterPlan,
    Entity,
    Part,
    Persona,
)
from novel_engine.planner import Planner
from novel_engine.repository import Repository


def _seed() -> Repository:
    r = Repository(db.connect(":memory:"))
    r.insert_entity(Entity("loc_main", "location", "主场景", {}))
    r.insert_entity(Entity("hero", "character", "云鹤子", {}))
    r.insert_persona(Persona(agent_id="hero", name="云鹤子", want="求道"))
    # 上一章（已写出正文）+ 其章纲（提供承接锚词：地点名/人物名）
    r.insert_accepted_chapter(AcceptedChapterRecord(
        project_id="p", chapter_no=1, title="井边",
        prose="前段铺垫。\n\n云鹤子俯身看向主场景的枯井，井底似有人影一闪，他的手停在井沿上。",
    ))
    r.upsert_chapter_plan(ChapterPlan(
        chapter_id="ch1", arc_id="a1", sequence_order=1,
        cast=["hero"], location_ids=["loc_main"], status="done",
    ))
    return r


def test_prev_chapter_tail_and_anchor_terms():
    p = Planner(_seed(), llm=None, theme="证道")
    tail = p._prev_chapter_tail(2)
    assert "井底似有人影" in tail and "枯井" in tail
    assert p._prev_anchor_terms(2) == ["主场景", "云鹤子"]
    # 开篇/无前章 → 空
    assert p._prev_chapter_tail(1) == ""
    assert p._prev_anchor_terms(1) == []


class _DriftLLM(LLMClient):
    """章纲 LLM 返回**另起炉灶**的 beats（不含上一章任何锚词）。"""

    def __init__(self, beats):
        self._beats = beats

    def complete(self, system: str, user: str) -> str:
        if "下一章的章纲" in system:
            return json.dumps({
                "beats": self._beats,
                "beat_povs": ["云鹤子", "云鹤子", "云鹤子"],
                "location": "主场景",
                "question": "他能查清这桩旧案吗？",
                "exit_state": "他拿到半张地契，但被人盯上，处境更险。",
                "props": [],
            }, ensure_ascii=False)
        return "{}"

    def complete_at(self, system: str, user: str, temperature: float) -> str:
        return self.complete(system, user)


def _spec(p: Planner, role: str):
    return p._chapter_spec(
        Part(part_id="pt", sequence_order=2, title="第二卷", goal="查案"),
        Arc(arc_id="a1", part_id="pt", sequence_order=1, summary="旧案浮现"),
        role, has_reveal=False, locs=[("loc_main", "主场景")], prev_loc="loc_main",
        prev_hook="井底有人影", recent_locs=[], conflict_type="",
        pov_name="云鹤子", cast_names="云鹤子", cast_agent_ids=["hero"], chapter_idx=1,
    )


def test_climax_drift_gets_continuity_prefix():
    drifted = [
        "林婆婆在荒庙后院翻出一只旧木箱，箱底压着半张地契",
        "差役赶来盘问，她谎称是来上香的香客，险些露馅",
        "她蹲在庙前石阶上喘气，指尖还沾着木箱上的灰",
    ]
    p = Planner(_seed(), llm=_DriftLLM(drifted), theme="证道")
    beats = _spec(p, "climax")[0]
    # 收束章漂走 → 确定性把承接补回 beat1（引上一章锚词「主场景」）
    assert beats[0].startswith("紧接上一章结尾（主场景")
    assert drifted[0] in beats[0]


def test_no_prefix_when_beats_already_continue():
    continued = [
        "云鹤子翻身下到主场景的枯井里，借着火折子查看井壁",
        "井底暗格藏着半张地契，他刚伸手就听见上方有脚步",
        "他贴着井壁屏息，水珠顺着石缝滴在他后颈",
    ]
    p = Planner(_seed(), llm=_DriftLLM(continued), theme="证道")
    beats = _spec(p, "climax")[0]
    assert not beats[0].startswith("紧接上一章结尾")  # 已承接，不重复补
