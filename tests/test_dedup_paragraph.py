"""段落级去重：本场整段搬运前场某段 → 打回；仅风格相近无搬运 → 放行（防误杀）。"""
from __future__ import annotations

from novel_engine import db
from novel_engine.narration.narrator import Narrator, _para_overlap, _split_paragraphs
from novel_engine.repository import Repository


def _narrator() -> Narrator:
    return Narrator(Repository(db.connect(":memory:")), llm=None)


# 一段足够长、独特的"前场段落"
PREV_PARA = (
    "沈砚掸了掸衣襟上并不存在的烟灰，指尖落下的动作却比往日慢了半拍。"
    "吧台后的暗格抽屉虚掩着，他侧身时余光扫过那枚擦痕，铜质的边角被什么尖锐的东西刮过，"
    "露出底下铁锈的底色。他想起昨天藏进书柜暗格的那份名单，纸页折角处折出四道不规则的折痕。"
)
PREV_SCENE = "留声机的唱针在划痕处顿了一下，爵士乐便在那处停顿上洇出一圈毛边。\n\n" + PREV_PARA


def test_split_paragraphs_filters_short():
    paras = _split_paragraphs("短句。\n\n" + PREV_PARA)
    assert all(len(p) >= 20 for p in paras)
    assert any("掸了掸衣襟" in p for p in paras)


def test_paragraph_carryover_is_flagged():
    n = _narrator()
    # 本场：原创开头 + 整段搬运前场的 PREV_PARA
    cur = "他推开咖啡馆的门，冷风灌进衣领，留声机的乐声扑面而来。\n\n" + PREV_PARA
    ok, fb = n._dedup_check(cur, [PREV_SCENE])
    assert ok is False
    assert "重复" in fb or "复述" in fb or "照搬" in fb


def test_distinct_scene_not_flagged():
    n = _narrator()
    # 风格相近但内容完全不同、无整段搬运 → 应放行
    cur = (
        "苏静放下茶杯，指甲在杯沿刮出一声轻响。她盯着对面那只搁在桌上的手，"
        "一字一句地问，三点到四点的窗口，到底是谁定的。\n\n"
        "赵九把卷烟在指间转了个圈，笑了一下，眼睛却没笑。他说这年头，连敲桌子都成了学问。"
    )
    ok, fb = n._dedup_check(cur, [PREV_SCENE])
    assert ok is True, f"无搬运不应误杀：{fb}"


def test_para_overlap_helper_direct():
    # 直接验证 helper：同段判重、异段放行
    ok, _ = _para_overlap(PREV_PARA, [PREV_SCENE])
    assert ok is False
    ok2, _ = _para_overlap("一段毫不相干、讲的是江边日落与归航渔船的文字，没有任何重合。", [PREV_SCENE])
    assert ok2 is True


# ---- 对白级去重 ----
def test_repeated_dialogue_is_flagged():
    n = _narrator()
    prev = "苏静站在门口，指甲掐了掐烟卷。\n「来得正好，我正盯着呢。」"
    # 本场内容全新，但重复了同一句登场台词
    cur = ("队长的皮靴声远去，雨声渐小。沈砚缓缓吐出一口气。\n\n"
           "苏静的声音从书架那边传来：「来得正好，我正盯着呢。」")
    ok, fb = n._dedup_check(cur, [prev])
    assert ok is False
    assert "对白" in fb or "同一句" in fb


def test_distinct_dialogue_not_flagged():
    n = _narrator()
    prev = "苏静掐着烟卷。\n「来得正好，我正盯着呢。」"
    cur = ("沈砚翻开书页，指尖停在第十三页。\n"
           "苏静在书架另一侧取下一本《春》，轻声说：「你找的那个人，不在这页。」")
    ok, fb = n._dedup_check(cur, [prev])
    assert ok is True, f"不同台词不应误杀：{fb}"
