"""B4 双向控制器：在beat/矛盾 主判定 + 关键场解离反推；director scripted 失败触发重写。"""
from __future__ import annotations

import json

from novel_engine import db
from novel_engine.agent import CharacterAgent
from novel_engine.dilemma import DilemmaGenerator
from novel_engine.director import Director
from novel_engine.llm.base import LLMClient
from novel_engine.llm.mock import MockClient
from novel_engine.models import ChapterPlan, Entity, Fact, KnowledgeItem, Persona
from novel_engine.monitors import Monitors
from novel_engine.narration.controller import Controller
from novel_engine.narration.fact_extractor import FactExtractor
from novel_engine.narration.scene_writer import SceneSpec, SceneWriter
from novel_engine.repository import Repository
from novel_engine.validator import Validator


def _repo() -> Repository:
    r = Repository(db.connect(":memory:"))
    r.insert_entity(Entity("hero", "character", "沈砚", {}))
    r.insert_persona(Persona(agent_id="hero", name="沈砚"))
    return r


def _ch() -> ChapterPlan:
    return ChapterPlan(chapter_id="ch1", arc_id="a1", sequence_order=1, title="", status="active",
                       cast=["hero"], beat_goals=["甲"], min_scenes=1, target_scenes=1)


class _JudgeLLM(LLMClient):
    """主判定返回 ok=false。"""
    def complete(self, s, u):
        return self.complete_at(s, u)

    def complete_at(self, s, u, temperature=None):
        return json.dumps({"ok": False, "feedback": "跑题了，没在演本拍目标。"}, ensure_ascii=False)


class _RevControlLLM(LLMClient):
    """主判定过、反推因果断裂。"""
    def __init__(self):
        self.n = 0

    def complete(self, s, u):
        return self.complete_at(s, u)

    def complete_at(self, s, u, temperature=None):
        self.n += 1
        if "意图让读者得出的结论" in s:                # 反推调用
            return json.dumps({"consistent": False, "reason": "正文无任何铺垫。"}, ensure_ascii=False)
        return json.dumps({"ok": True, "feedback": ""}, ensure_ascii=False)


def test_controller_offline_passes():
    ok, _ = Controller(_repo(), llm=None).check("正文。", SceneSpec("hero", "甲", _ch()), ["hero"])
    assert ok is True


def test_controller_judge_fails_off_beat():
    ok, fb = Controller(_repo(), _JudgeLLM()).check("无关的内容。", SceneSpec("hero", "甲", _ch()), ["hero"])
    assert ok is False and "跑题" in fb


def test_controller_reverse_flags_unfounded_reveal():
    r = _repo()
    r.append_fact(Fact(fact_id="f_t", fact_type="secret", canonical_content="赵九是卧底", story_time=0))
    spec = SceneSpec("hero", "甲", _ch(), pov_known=[KnowledgeItem("hero", "f_t", "赵九是卧底", 1.0, 0)],
                     may_reveal=["f_t"])
    llm = _RevControlLLM()
    ok, fb = Controller(r, llm).check("沈砚忽然断定赵九是卧底。", spec, ["hero"])
    assert ok is False and "铺垫" in fb
    assert llm.n == 2                       # 独立两次调用：主判定 + 反推


def test_controller_no_reverse_when_no_reveal():
    # 非关键场（may_reveal 空）→ 不触发反推（只 1 次调用）
    spec = SceneSpec("hero", "甲", _ch())
    llm = _RevControlLLM()
    Controller(_repo(), llm).check("一段平常的正文。", spec, ["hero"])
    assert llm.n == 1


# ---- director scripted：Controller 不过 → 触发一次重写 ----
class _FailOnceController:
    def __init__(self):
        self.calls = 0

    def check(self, prose, spec, present):
        self.calls += 1
        return (False, "重写") if self.calls == 1 else (True, "")


class _CountWriter(SceneWriter):
    def __init__(self, repo):
        super().__init__(repo, llm=None)
        self.writes = 0

    def write(self, spec, feedback=""):
        self.writes += 1
        return super().write(spec, feedback)


def test_scripted_controller_triggers_rewrite():
    r = _repo()
    ch = _ch()
    r.upsert_chapter_plan(ch)
    ctrl = _FailOnceController()
    writer = _CountWriter(r)
    d = Director(r, DilemmaGenerator(r, llm=None, theme=""), CharacterAgent(r, MockClient()),
                 Validator(r), Monitors(r),
                 planner=type("P", (), {"ensure_chapter": lambda s: ch})(),
                 writer=writer, extractor=FactExtractor(r, llm=None), controller=ctrl, mode="scripted")
    d.step()
    assert ctrl.calls == 1 and writer.writes == 2     # 校验一次 + 因不过而重写一次
