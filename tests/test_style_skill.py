"""B0-a 文风模拟引擎核心：量化指纹 / 摄取 / 安全注入 / 软闸门 / 与 tone 叠加。"""
from __future__ import annotations

from novel_engine import db
from novel_engine.models import StyleProfile
from novel_engine.repository import Repository
from novel_engine.style_skill import (
    compute_style_metrics,
    distill_from_works,
    parse_style_skill,
    style_metric_gate,
)


# ---- 6 维量化指纹 ----
def test_metrics_has_six_plus_dims_and_json_safe():
    import json
    m = compute_style_metrics("他握紧了绳。海是蓝的。鱼在深处游动，缓慢而沉默。")
    for k in ("avg_sentence_len", "sentence_len_variance", "punct_density", "ttr",
              "content_function_ratio", "semantic_leaping", "sensory_e"):
        assert k in m and isinstance(m[k], (int, float))
    json.dumps(m)  # 必须 JSON 可序列化


def test_metrics_distinguishes_short_vs_long_sentences():
    short = compute_style_metrics("他走。她笑。风停。狗叫。门开。")
    long = compute_style_metrics(
        "他沿着那条被雨水泡得发胀的木栈道一直往前走，心里盘算着等会儿该如何开口，"
        "才能既不显得唐突又能把那件压在胸口许久的事情说清楚。")
    assert short["avg_sentence_len"] < long["avg_sentence_len"]


# ---- 摄取（离线兜底，无 LLM）----
def test_distill_from_works_offline_produces_metrics_and_samples():
    p = distill_from_works(None, "短句。很短。\n\n又一段，稍微长一点的句子在这里铺陈开来。", name="测试体")
    assert p.is_set() and p.metrics and p.samples
    assert p.name == "测试体"


def test_parse_style_skill_offline():
    p = parse_style_skill(None, "风格名：江南腔\n句法：长短句交替，破折号拖尾。\n样例：少年仰头望天——")
    assert p.is_set() and p.metrics


# ---- 安全注入：含样例 + 硬红线，且 enabled=0 不注入 ----
def test_style_skill_prompt_has_redline_and_samples():
    r = Repository(db.connect(":memory:"))
    r.set_style_skill(StyleProfile(name="海明威", source="老人与海", register="极简",
                                   rhythm="短句", samples=["他握紧了绳。"], enabled=True))
    block = r.style_skill_prompt()
    assert "文风模拟" in block and "严禁照搬" in block and "海明威" in block


def test_disabled_style_skill_not_injected():
    r = Repository(db.connect(":memory:"))
    r.set_style_skill(StyleProfile(name="x", register="极简", samples=["短。"], enabled=False))
    assert r.style_skill_prompt() == ""


# ---- 软闸门 ----
def test_style_metric_gate_flags_large_deviation():
    # 目标=极短句文风；本场全是长句 → 句长偏离过大被拦
    target = StyleProfile(name="极简", samples=["他走。她笑。"], enabled=True)
    target.metrics = compute_style_metrics("他走。她笑。风停。狗叫。门开。雨落。")
    longish = ("他沿着那条被雨水泡得发胀的木栈道缓慢地往前走着，一边走一边在心里反复盘算"
               "着等会儿见了面究竟该用怎样一种不轻不重的语气把那桩埋了很久的旧事讲出来。")
    ok, fb = style_metric_gate(longish, target)
    assert ok is False and "句子长度" in fb


def test_style_metric_gate_passes_when_no_metrics():
    # 无指纹（如现成文风未量化）→ 不阻断
    ok, _ = style_metric_gate("随便一段话。", StyleProfile(name="x", samples=["a"], enabled=True))
    assert ok is True
