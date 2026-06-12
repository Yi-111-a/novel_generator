"""W1 World Skill：分节全量化 + 两级(summary/detail) + 多级生成+校验回路 + 渐进细化。"""
from __future__ import annotations

import json
import re

from novel_engine import db
from novel_engine.llm.base import LLMClient
from novel_engine.repository import Repository
from novel_engine.worldbible import _W1_BASE, _W1_SECTIONS, build_world_skill, deepen_section


def _sec(system: str) -> str:
    m = re.search(r"section=([A-Za-z]+)", system)
    return m.group(1) if m else ""


class _SkillLLM(LLMClient):
    """脚本化各阶段响应（总览/深写/校验/修订/深化），按任务标记分支。"""

    def __init__(self, issues=None) -> None:
        self.issues = issues or []
        self.calls = []

    def complete(self, system: str, user: str) -> str:
        self.calls.append(system[:40])
        if "【任务：总览】" in system:
            return json.dumps({k: f"{k}的一句话概述。" for k in _W1_SECTIONS}, ensure_ascii=False)
        if "【任务：深写】" in system:
            sec = _sec(system)
            return ("详" * 360) if sec in _W1_BASE else (f"{sec}的简要设定。" * 6)
        if "【任务：校验】" in system:
            return json.dumps({"issues": self.issues}, ensure_ascii=False)
        if "【任务：修订】" in system:
            sec = _sec(system)
            return json.dumps({"summary": f"{sec}修订后概述", "detail": f"【修订】{sec}" + "正" * 300},
                              ensure_ascii=False)
        if "【任务：深化】" in system:
            return "深化追加：霞飞路尽头新辟的一条窄巷，巡捕房默许的灰色地带。"
        return "{}"


def _repo() -> Repository:
    r = Repository(db.connect(":memory:"))
    r.add_bible_section("settingCore", "设定内核", "1939 上海孤岛，租界孤悬于沦陷区。", source="user")
    r.add_bible_section("geography", "地理", "霞飞路、七十六号、百乐门。", source="user")
    return r


# ---------------- build_world_skill ----------------
def test_build_creates_two_level_w1_rows():
    r = _repo()
    res = build_world_skill(r, llm=_SkillLLM(), theme="孤岛谍战")
    assert res["sections"] == len(_W1_SECTIONS)
    w1 = [s for s in r.list_bible_sections() if s["source"] == "w1"]
    assert {s["section"] for s in w1} == set(_W1_SECTIONS)        # 9 节都有权威行
    assert all(s["summary"].strip() and s["body_full"].strip() for s in w1)  # 两级齐全
    base = next(s for s in w1 if s["section"] in _W1_BASE)
    assert len(base["body_full"]) >= 300                          # 基础层够厚
    # 常驻 summary 速览 + 按需 detail（detail 取 w1 加厚版）
    assert r.bible_summaries_text().count("·") >= len(_W1_SECTIONS)
    assert "详" in r.bible_sections_text(["settingCore"])


def test_build_is_idempotent():
    r = _repo()
    llm = _SkillLLM()
    build_world_skill(r, llm=llm, theme="x")
    n1 = len([s for s in r.list_bible_sections() if s["source"] == "w1"])
    assert build_world_skill(r, llm=llm, theme="x") == {"skipped": "exists"}
    n2 = len([s for s in r.list_bible_sections() if s["source"] == "w1"])
    assert n1 == n2 == len(_W1_SECTIONS)                          # 不堆积


def test_build_noop_without_llm_or_source():
    assert build_world_skill(_repo(), llm=None)["skipped"] == "no_llm"
    empty = Repository(db.connect(":memory:"))
    assert build_world_skill(empty, llm=_SkillLLM())["skipped"] == "no_source"


def test_review_loop_applies_fix():
    r = _repo()
    issues = [{"section": "geography", "problem": "地名与设定冲突", "fix": "改用真实租界地名"}]
    res = build_world_skill(r, llm=_SkillLLM(issues=issues), theme="孤岛")
    assert res["issues"] == 1 and res["fixed"] == 1
    geo = next(s for s in r.list_bible_sections()
               if s["source"] == "w1" and s["section"] == "geography")
    assert geo["body_full"].startswith("【修订】")                # 修订已应用
    assert geo["summary"] == "geography修订后概述"


# ---------------- deepen_section（渐进细化，可多次）----------------
def test_deepen_section_multi_round():
    r = _repo()
    build_world_skill(r, llm=_SkillLLM(), theme="x")
    llm = _SkillLLM()
    assert deepen_section(r, llm=llm, section="culture", context="陈啸亮出身份") is True
    deep = [s for s in r.list_bible_sections() if s["source"] == "w1_deepened"]
    assert len(deep) == 1 and deep[0]["section"] == "culture"
    # 可多次深化
    assert deepen_section(r, llm=llm, section="culture", context="再来一次") is True
    deep2 = [s for s in r.list_bible_sections() if s["source"] == "w1_deepened"]
    assert len(deep2) == 2
    # 带 hint 深化
    assert deepen_section(r, llm=llm, section="culture", context="c", hint="地下交易") is True
    assert len([s for s in r.list_bible_sections() if s["source"] == "w1_deepened"]) == 3


def test_deepen_rejects_unknown_section_and_no_llm():
    r = _repo()
    build_world_skill(r, llm=_SkillLLM(), theme="x")
    assert deepen_section(r, llm=_SkillLLM(), section="factions", context="x") is False  # 非 W1 节
    assert deepen_section(r, llm=None, section="culture", context="x") is False


# ---------------- 迁移 ----------------
def test_migration_adds_summary_column():
    conn = db.connect(":memory:")
    conn.execute("DROP TABLE world_bible_sections")
    conn.execute("CREATE TABLE world_bible_sections (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                 "section TEXT, title TEXT, body_full TEXT, source TEXT, created_at INTEGER)")
    db._migrate(conn)
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(world_bible_sections)").fetchall()}
    assert "summary" in cols
