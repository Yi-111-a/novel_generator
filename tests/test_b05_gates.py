"""B0.5 快赢批：反升华 / 道德灰度 / 尾部铁律加固+注意力锚定 / E≥3.5 情感比 / 情感滞后。

对应方法.txt 主题 1(StoryScope 三硬伤) / 3(注意力) / 6(情感共鸣)。
全部为提示层 + 确定性纯函数闸门，零新表零迁移。
"""
from __future__ import annotations

from novel_engine import db
from novel_engine.agent import _SYSTEM_TMPL
from novel_engine.llm.base import LLMClient
from novel_engine.models import ChapterPlan, Entity, Event, Persona
from novel_engine.narration.audit import _sermon_tail
from novel_engine.narration.narrator import Narrator
from novel_engine.repository import Repository
from novel_engine.tone import emotion_ratio_gate, sensory_ratio


# ---- ① 反升华：章尾升华检测（确定性，低误报）----
def test_sermon_tail_flags_moralizing_ending():
    assert _sermon_tail("他锁上门，转身离开。\n\n这就是命运给他的全部答案，原来人生不过是一场错过。")
    assert _sermon_tail("说到底，所谓的真相，无非是每个人都想要的那点希望罢了。")


def test_sermon_tail_not_flagged_for_concrete_ending():
    # 停在具体动作/物象上 → 不算升华
    assert not _sermon_tail("他把钢笔搁回桌角，灯灭了。")
    assert not _sermon_tail("门外的雨还在下，她数着台阶往下走，一级，又一级。")
    # 有抽象词但无归纳式连接词 → 不误伤
    assert not _sermon_tail("她想起了命运两个字，却什么也没说，只是把伞收了起来。")


def test_anti_sermon_redline_in_agent_and_narrator():
    # ② 道德灰度进角色决策模板
    assert "道德灰度" in _SYSTEM_TMPL


# ---- ④ 情感共鸣 E 比率 ----
def test_sensory_ratio_counts():
    s, a = sensory_ratio("指尖发凉，喉咙发干。绝望。")
    assert s >= 2 and a == 1


def test_emotion_ratio_gate_fails_on_abstract_high_tension():
    # 高张力 + 堆抽象情感词 + 几乎无生理细节 → 不过
    ok, fb = emotion_ratio_gate("绝望。痛苦。崩溃。她彻底崩溃了。", tension=0.85)
    assert ok is False and "生理" in fb


def test_emotion_ratio_gate_passes_when_embodied():
    # 高张力但用生理/环境承载（感官词多）→ 通过
    text = ("指尖发凉，喉咙发干，呼吸乱掉，心跳擂鼓，冷汗顺着脊背滑下，"
            "手指发抖，胸口发紧——只有一个绝望两个字，一个痛苦。")
    ok, _ = emotion_ratio_gate(text, tension=0.85)
    assert ok is True


def test_emotion_ratio_gate_skips_low_tension_and_sparse_abstract():
    # 低张力场不启用
    ok, _ = emotion_ratio_gate("绝望。痛苦。崩溃。", tension=0.4)
    assert ok is True
    # 抽象情感词不足 min_abstract → 放行（不误伤克制场）
    ok2, _ = emotion_ratio_gate("他望着窗外，绝望地笑了笑。", tension=0.9)
    assert ok2 is True


# ---- ③ 尾部铁律加固 + 注意力锚定 + ⑤ 情感滞后（捕获 prompt 验证）----
class _Capture(LLMClient):
    def __init__(self) -> None:
        self.system = ""
        self.user = ""

    def complete(self, system: str, user: str) -> str:
        self.system, self.user = system, user
        return "他在桌前坐下，把钢笔搁好，没有再说话。"

    @property
    def name(self) -> str:
        return "Capture"


def _repo_with_chapter(tension: float) -> Repository:
    r = Repository(db.connect(":memory:"))
    r.insert_entity(Entity("hero", "character", "云鹤子", {}))
    r.insert_entity(Entity("obj_pen", "object", "钢笔", {}))
    r.insert_persona(Persona(agent_id="hero", name="云鹤子", motif_objects=["obj_pen"]))
    r.upsert_chapter_plan(ChapterPlan(
        chapter_id="ch1", arc_id="a1", sequence_order=1, title="",
        cast=["hero"], items_present=["obj_pen"], target_tension=tension,
        beat_goals=["撞破真相"]))
    return r


def test_tail_ironlaw_and_anchor_present():
    r = _repo_with_chapter(tension=0.8)
    llm = _Capture()
    Narrator(r, llm).render("hero", [Event("e1", 1, ["hero"], "对峙", beat_id="ch1")],
                            "", [], [], scene_pos=1)
    # ③ 尾部铁律块在 user 末尾 + 复述可用器物
    assert "本场铁律" in llm.user
    assert "钢笔" in llm.user
    # 注意力锚定：让关键道具被本场消费一次
    assert "触及一次" in llm.user
    # ① 反升华红线在 system
    assert "反升华红线" in llm.system
    # ⑤ 情感滞后 nudge（高张力场）
    assert "情感滞后" in llm.user


def test_affective_lag_absent_for_low_tension():
    r = _repo_with_chapter(tension=0.3)
    llm = _Capture()
    Narrator(r, llm).render("hero", [Event("e1", 1, ["hero"], "闲谈", beat_id="ch1")],
                            "", [], [], scene_pos=1)
    assert "情感滞后" not in llm.user        # 低张力场不加滞后约束
    assert "本场铁律" in llm.user            # 但尾部铁律恒在
