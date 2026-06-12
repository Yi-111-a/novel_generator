"""§16 文风契约：数据化、确认后只读、前置块注入、tone_gate 硬闸门。"""
from __future__ import annotations

from novel_engine import db
from novel_engine.llm.base import LLMClient
from novel_engine.models import ToneProfile
from novel_engine.repository import Repository
from novel_engine.tone import build_tone_profile, tone_gate


def _repo() -> Repository:
    return Repository(db.connect(":memory:"))


class _ScriptedLLM(LLMClient):
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.last_user = ""

    def complete(self, system: str, user: str) -> str:
        self.last_user = user
        return self.reply


# ---------- 预设 / 回退 ----------
def test_preset_fallback_per_genre():
    r = _repo()
    p = build_tone_profile(r, llm=None, genre="爽文", theme="少年逆袭")
    assert p.genre == "xuanhuan_powerfantasy"
    assert p.primary_effect == "catharsis_satisfaction"
    assert "冗长心理描写" in p.diction_dont
    # 已落库
    assert r.get_tone_profile().primary_effect == "catharsis_satisfaction"


def test_unknown_genre_defaults_literary():
    r = _repo()
    p = build_tone_profile(r, llm=None, genre="不存在的题材")
    assert p.genre == "literary"


# ---------- 确认后只读 ----------
def test_confirmed_is_readonly():
    r = _repo()
    build_tone_profile(r, llm=None, genre="恐怖")
    r.confirm_tone_profile()
    # 再次构建不应覆盖
    build_tone_profile(r, llm=None, genre="喜剧")
    assert r.get_tone_profile().genre == "horror"
    # set 也被拒
    r.set_tone_profile(ToneProfile(genre="comedy", primary_effect="laugh"))
    assert r.get_tone_profile().genre == "horror"


# ---------- LLM 合并 + 预设补全缺字段 ----------
def test_llm_merge_keeps_preset_for_missing_fields():
    r = _repo()
    llm = _ScriptedLLM('{"primary_effect":"dread","tone_reference":"灯灭的那一刻，墙在呼吸。"}')
    p = build_tone_profile(r, llm=llm, genre="horror", theme="古宅")
    assert p.primary_effect == "dread"
    assert p.tone_reference.startswith("灯灭")
    # 缺失字段回退到 horror 预设
    assert "插科打诨" in p.diction_dont


# ---------- 前置块注入 ----------
def test_tone_profile_prompt_block():
    r = _repo()
    assert r.tone_profile_prompt() == ""  # 空契约不注入
    build_tone_profile(r, llm=None, genre="comedy")
    block = r.tone_profile_prompt()
    assert "文风契约" in block and "laugh" in block
    assert "禁忌" in block


# ---------- tone_gate 硬闸门 ----------
def test_tone_gate_deterministic_banned_word():
    p = build_tone_profile(_repo(), llm=None, genre="horror")
    p.diction_dont = ["插科打诨"]
    ok, fb = tone_gate("两人插科打诨，气氛轻松。", p, llm=None)
    assert ok is False and "插科打诨" in fb


def test_tone_gate_offline_pass_when_no_banned():
    p = build_tone_profile(_repo(), llm=None, genre="horror")
    ok, _ = tone_gate("走廊尽头传来不属于这屋子的呼吸声。", p, llm=None)
    assert ok is True


def test_tone_gate_llm_low_score_fails():
    p = build_tone_profile(_repo(), llm=None, genre="xuanhuan_powerfantasy")
    llm = _ScriptedLLM('{"effect_score":0.1,"violates_dont":true,"reason":"全是心理描写，毫无爽点"}')
    ok, fb = tone_gate("他久久地反思着自己的内心。", p, llm=llm)
    assert ok is False and "重写" in fb


def test_tone_gate_empty_profile_passes():
    ok, _ = tone_gate("任意文本", ToneProfile(), llm=None)
    assert ok is True
