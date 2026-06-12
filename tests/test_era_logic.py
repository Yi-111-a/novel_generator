"""B0.6 文化/意识形态隔离墙（方法.txt 主题8）：时代逻辑数据 + 注入 + 闸门。"""
from __future__ import annotations

import json

from novel_engine import db
from novel_engine.models import ToneProfile
from novel_engine.repository import Repository
from novel_engine.tone import build_tone_profile, tone_gate


def _era(enabled=True) -> dict:
    return {
        "enabled": enabled, "moral_index": 20, "religiosity": 90, "science_level": 0,
        "banned_modern_words": ["心理学", "潜意识", "效率", "人权"],
        "forced_attribution": "灾难归因于神罚与女巫，而非科学因果。",
    }


# ---- 迁移：新表带 era_logic 列 ----
def test_tone_profile_has_era_logic_column():
    conn = db.connect(":memory:")
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(tone_profile)").fetchall()}
    assert "era_logic" in cols


# ---- model round-trip ----
def test_set_get_tone_profile_round_trips_era_logic():
    r = Repository(db.connect(":memory:"))
    r.set_tone_profile(ToneProfile(genre="tragedy", register="典雅", era_logic=_era()))
    got = r.get_tone_profile()
    assert got.era_logic.get("enabled") is True
    assert "心理学" in got.era_logic.get("banned_modern_words", [])


def test_era_logic_defaults_empty_when_unset():
    r = Repository(db.connect(":memory:"))
    r.set_tone_profile(ToneProfile(genre="mystery", register="冷静"))
    assert r.get_tone_profile().era_logic == {}


# ---- 注入 tone_profile_prompt（覆盖 agent+narrator 两端）----
def test_prompt_injects_wall_when_enabled():
    r = Repository(db.connect(":memory:"))
    r.set_tone_profile(ToneProfile(genre="tragedy", register="典雅", era_logic=_era()))
    block = r.tone_profile_prompt()
    assert "时代/语境隔离墙" in block
    assert "心理学" in block and "神罚" in block


def test_prompt_no_wall_when_disabled():
    r = Repository(db.connect(":memory:"))
    r.set_tone_profile(ToneProfile(genre="mystery", register="冷静", era_logic=_era(enabled=False)))
    assert "隔离墙" not in r.tone_profile_prompt()


# ---- 闸门：现代禁用词命中即重写 ----
def test_tone_gate_flags_modern_word_under_wall():
    p = ToneProfile(genre="tragedy", primary_effect="grief", register="典雅", era_logic=_era())
    ok, fb = tone_gate("他冷静地分析了对方的潜意识动机。", p, llm=None)
    assert ok is False and "时代" in fb


def test_tone_gate_passes_modern_word_without_wall():
    p = ToneProfile(genre="mystery", primary_effect="curiosity", register="冷静",
                    era_logic=_era(enabled=False))
    ok, _ = tone_gate("他分析了对方的潜意识动机。", p, llm=None)
    assert ok is True


# ---- 派生：无 LLM → 默认不开（现代题材不污染）----
def test_build_offline_disables_wall():
    r = Repository(db.connect(":memory:"))
    p = build_tone_profile(r, llm=None, genre="mystery", theme="都市悬疑")
    assert not (p.era_logic or {}).get("enabled")


# ---- 派生：LLM 给出 era_logic → 合并进契约 ----
class _LLMWithEra:
    def complete(self, system: str, user: str) -> str:
        return json.dumps({
            "genre": "tragedy", "primary_effect": "grief", "register": "典雅书面",
            "sentence_rhythm": "绵长", "diction_do": ["象征"], "diction_dont": ["插科打诨"],
            "device_kit": ["宿命铺垫"], "pacing": "舒缓", "tension_curve_bias": "递进",
            "reveal_cadence": "均匀延迟", "complexity": "高", "tone_reference": "风过荒原。",
            "era_logic": _era(),
        }, ensure_ascii=False)

    @property
    def name(self) -> str:
        return "EraLLM"


def test_build_with_llm_merges_era_logic():
    r = Repository(db.connect(":memory:"))
    p = build_tone_profile(r, llm=_LLMWithEra(), genre="tragedy", theme="中世纪",
                           setting_hint="中世纪黑暗奇幻")
    assert (p.era_logic or {}).get("enabled") is True
    assert "神罚" in p.era_logic.get("forced_attribution", "")
