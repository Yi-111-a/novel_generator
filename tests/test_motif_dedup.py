"""A1 motif 跨场冷却 + A3 句级去重闸门。"""
from __future__ import annotations

from novel_engine import db
from novel_engine.llm.base import LLMClient
from novel_engine.models import Entity, Event, Persona
from novel_engine.narration.narrator import Narrator, _gram_overlap, _lead_clause, _lead_overlap
from novel_engine.repository import Repository


def _repo() -> Repository:
    r = Repository(db.connect(":memory:"))
    r.insert_entity(Entity("hero", "character", "云鹤子", {}))
    r.insert_entity(Entity("obj_bell", "object", "铜铃", {}))
    r.insert_entity(Entity("obj_scroll", "object", "残卷", {}))
    r.insert_persona(Persona(agent_id="hero", name="云鹤子", motif_objects=["obj_bell", "obj_scroll"]))
    return r


def test_motif_cooldown_alternates_never_consecutive_same():
    # 两个 motif + 冷却 → 交替使用，绝不连续两场用同一个（治"铜钥每场都摸"）
    r = _repo()
    n = Narrator(r, llm=None)
    picks = []
    for pos in range(4):
        picks.append(n._pick_motif(r.get_persona("hero"), scene_pos=pos))
    assert picks == ["铜铃", "残卷", "铜铃", "残卷"]
    assert all(a != b for a, b in zip(picks, picks[1:]))  # 无连续重复


def test_single_motif_rests_when_on_cooldown():
    # 只有一个 motif：刚用过的下一场进入冷却 → 返回 ''（本场不靠 motif）
    r = _repo()
    r.insert_persona(Persona(agent_id="solo", name="独", motif_objects=["obj_bell"]))
    n = Narrator(r, llm=None)
    assert n._pick_motif(r.get_persona("solo"), scene_pos=0) == "铜铃"
    assert n._pick_motif(r.get_persona("solo"), scene_pos=1) == ""   # 冷却中
    assert n._pick_motif(r.get_persona("solo"), scene_pos=2) == "铜铃"  # 过冷却复用


def test_gram_overlap_detects_near_duplicate():
    a = "雾从窗缝渗进来，带着海藻腐烂的甜腥。沈砚的手指悬在半空。"
    b = "雾从窗缝渗进来，裹着海藻腐烂的甜腥。沈砚的手指悬在半空。"
    c = "灯塔的光昏黄如病眼，照不透三步外的礁石。"
    assert _gram_overlap(a, b, n=6) >= 0.18      # 近乎复制 → 高重合
    assert _gram_overlap(a, c, n=6) < 0.18       # 不同 → 低重合


class _DupThenFresh(LLMClient):
    """首稿复读上一场，二稿换新——验证去重闸门触发重渲。"""
    def __init__(self, prev: str) -> None:
        self.prev, self.n = prev, 0

    def complete(self, system: str, user: str) -> str:
        self.n += 1
        return self.prev if self.n == 1 else "灯塔的光在雾里摇晃，像一只睁不开的眼。"


def test_lead_clause_and_overlap():
    assert _lead_clause("雾在涨。不是海雾——") == "雾在涨"
    assert _lead_clause("雾在涨——不是从海上来，是从石阶") == "雾在涨"
    # 同开场 → 高重合；不同开场 → 低
    assert _lead_overlap("雾在涨。不是海雾", "雾在涨——不是从海上来") >= 0.6
    assert _lead_overlap("雾在涨。海雾压境", "他数着自己的步子，四十七步") < 0.6


def test_opening_repetition_flagged_even_if_bodies_differ():
    r = _repo()
    n = Narrator(r, llm=None)
    prev = "雾在涨。不是海雾，是另一种更重的、带着铁锈味的雾，从石缝里渗出，沿着堤岸爬行，吞没了整座码头。"
    cur = "雾在涨——不是从海上来，是从石阶的缝隙里往外渗。沈砚摩挲着掌心的铜钥，迟迟没有落步，远处缆绳钝涩作响。"
    ok, fb = n._dedup_check(cur, [prev])
    assert ok is False and "开头" in fb        # 整场不雷同，但开场雷同 → 被拦


class _DupOpenThenFresh(LLMClient):
    """首稿与上一场同开场，二稿换开场。"""
    def __init__(self) -> None:
        self.n = 0

    def complete(self, system: str, user: str) -> str:
        self.n += 1
        if self.n == 1:
            return "雾在涨——又一次漫过码头，沈砚站在原地，铜钥在掌心发凉，海风穿过他单薄的衣衫。"
        return "沈砚踏上第三级石阶，靴底碾碎一枚干枯的海星，粉尘散入积水，像一场无人知晓的祭奠。"


def test_opening_dedup_triggers_rerender():
    from novel_engine.models import Scene
    r = _repo()
    r.insert_scene(Scene("s0", 1, ["e0"], pov="hero",
                         prose_text="雾在涨。不是海雾，是带着铁锈味的雾，从石缝里渗出来，慢慢吞没了码头与栈桥。"))
    llm = _DupOpenThenFresh()
    out = Narrator(r, llm).render("hero", [Event("e1", 2, ["hero"], "试探")], "", [], [], scene_pos=1)
    assert llm.n >= 2                          # 开场雷同 → 重渲
    assert _lead_overlap(out, "雾在涨。不是海雾") < 0.6   # 最终稿换了开场


def test_dedup_gate_triggers_rerender():
    r = _repo()
    prev = "雾从窗缝渗进来，带着海藻腐烂的甜腥，沈砚的手指悬在半空，迟迟没有落下。"
    # 先放一场已成稿场景作为"近场"
    from novel_engine.models import Scene
    r.insert_scene(Scene("s0", 1, ["e0"], pov="hero", prose_text=prev))
    llm = _DupThenFresh(prev)
    out = Narrator(r, llm).render("hero", [Event("e1", 2, ["hero"], "试探")], "", [], [], scene_pos=1)
    assert llm.n >= 2                            # 触发重渲
    assert _gram_overlap(out, prev, n=6) < 0.18  # 最终稿不再与上一场雷同


# ---- 去除 LLM 偶发的"第N场/第N章"元标题（阅读层不应出现场/章标号）----
def test_strip_scene_headers():
    from novel_engine.narration.narrator import _strip_scene_headers
    assert _strip_scene_headers("# 第七场  亭子间的木门虚掩着").startswith("亭子间")
    assert _strip_scene_headers("第八场\n\n馄饨摊的白汽").startswith("馄饨摊")
    assert _strip_scene_headers("## 第三章 墨迹未干\n沈砚低头").startswith("沈砚")
    # 正常正文不被误伤
    body = "爵士乐滑过留声机的唱针。"
    assert _strip_scene_headers(body) == body
    # 不误删以"第…"开头但非标号的正文
    keep = "第一缕晨光照进窗子。"
    assert _strip_scene_headers(keep) == keep


# ---- C 动作/桥段去重：近场反复出现的同一招式 → 禁令 ----
def test_recurring_actions_flagged():
    from novel_engine.narration.narrator import _recurring_actions
    scenes = [
        "他蹲下系鞋带，借机回头确认身后。",
        "出门前他又蹲下，假意系鞋带，目光扫过身后。",
        "他蹲下身，手在鞋带上一顿，余光瞥向身后的尾巴。",
    ]
    ban = _recurring_actions(scenes, min_scenes=3)
    assert "蹲下" in ban and "身后" in ban

def test_recurring_actions_not_flagged_when_distinct():
    from novel_engine.narration.narrator import _recurring_actions
    scenes = ["他推开门。", "她点了烟。", "桌上摊着信。"]
    assert _recurring_actions(scenes, min_scenes=3) == []
