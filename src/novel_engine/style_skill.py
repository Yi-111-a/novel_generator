"""B0 文风模拟（Style Skill）。

两个摄取入口（都产出 StyleProfile 供用户确认）：
  distill_from_works(llm, raw_text)   上传作品原文 → LLM 蒸馏腔调 + 确定性 6 维量化指纹
  parse_style_skill(llm, skill_md)    上传现成 SKILL.md / 文风描述 → LLM 归一化成 StyleProfile

量化（方法.txt 主题5 / Stylometry，确定性纯函数，零新依赖）：
  compute_style_metrics(text) → 6 维指纹（句长均值/方差、标点密度、TTR、实虚词比、语义跳跃度、感官比 E）
  style_metric_gate(prose, profile) 软闸门：本场指纹偏离目标区间过大 → 反馈重写（低阈值防误报）

安全注入：见 repository.style_skill_prompt（只学腔调，样例内容严禁照搬）。
"""
from __future__ import annotations

import json
import re

from .llm.base import LLMClient
from .models import StyleProfile
from .tone import sensory_ratio

# 句子切分：中文句末标点
_SENT_SPLIT = re.compile(r"[。！？；!?;\n]+")
# 虚词表（功能词）：用于"实词/虚词比"近似（无需分词依赖）
_FUNCTION_WORDS = [
    "的", "了", "着", "和", "与", "在", "是", "也", "就", "都", "而", "之", "其", "以",
    "于", "把", "被", "给", "向", "从", "对", "为", "所", "或", "且", "但", "却", "并",
    "则", "又", "还", "再", "很", "更", "最", "不", "没", "无", "吗", "呢", "吧", "啊",
    "呀", "么", "嘛", "哦", "嗯", "这", "那", "里", "上", "下", "中",
]


def _char_bigrams(s: str) -> list[str]:
    s = "".join(s.split())
    return [s[i:i + 2] for i in range(len(s) - 1)] if len(s) >= 2 else []


def _bigram_jaccard(a: str, b: str) -> float:
    ga, gb = set(_char_bigrams(a)), set(_char_bigrams(b))
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)


def compute_style_metrics(text: str) -> dict:
    """从原文确定性地算出 6 维文风指纹（全部 JSON 可序列化的 float）。"""
    t = (text or "").strip()
    chars = len("".join(t.split())) or 1
    sents = [s.strip() for s in _SENT_SPLIT.split(t) if s.strip()]
    lens = [len("".join(s.split())) for s in sents] or [0]
    n = len(lens)
    avg_len = sum(lens) / n
    variance = sum((x - avg_len) ** 2 for x in lens) / n

    # 标点密度（每千字）：破折号/省略号/逗号/分号/顿号
    punct = sum(t.count(c) for c in ["—", "－", "…", "，", "、", "；", ",", ";"])
    punct_density = punct / chars * 1000

    # TTR：字符 2-gram 词汇丰富度（免分词）
    bigrams = _char_bigrams(t)
    ttr = (len(set(bigrams)) / len(bigrams)) if bigrams else 0.0

    # 实词/虚词比：非虚词字符 / 虚词字符（近似信息密度）
    func = sum(t.count(w) for w in _FUNCTION_WORDS) or 1
    content_function_ratio = max(0.0, (chars - func)) / func

    # 语义跳跃度：相邻句 1 - bigram_jaccard 的均值（海明威低跳跃 / 意识流高跳跃）
    if len(sents) >= 2:
        leaps = [1.0 - _bigram_jaccard(sents[i], sents[i + 1]) for i in range(len(sents) - 1)]
        semantic_leaping = sum(leaps) / len(leaps)
    else:
        semantic_leaping = 0.0

    # 感官比 E：复用 B0.5 的感官/抽象情感统计
    s, a = sensory_ratio(t)
    sensory_e = s / a if a else float(s)

    return {
        "avg_sentence_len": round(avg_len, 2),
        "sentence_len_variance": round(variance, 2),
        "punct_density": round(punct_density, 2),
        "ttr": round(ttr, 4),
        "content_function_ratio": round(content_function_ratio, 2),
        "semantic_leaping": round(semantic_leaping, 4),
        "sensory_e": round(sensory_e, 2),
    }


def _excerpts(text: str, k: int = 2, lo: int = 60, hi: int = 150) -> list[str]:
    """从原文取 k 段长度适中的样例（离线兜底，仅供模仿腔调）。"""
    paras = [p.strip() for p in re.split(r"\n\s*\n|\n", text or "") if len(p.strip()) >= lo]
    out = []
    for p in paras:
        out.append(p[:hi])
        if len(out) >= k:
            break
    if not out and text.strip():
        out = [text.strip()[:hi]]
    return out


def _distill_json(llm: LLMClient, system: str, user: str):
    try:
        raw = llm.complete(system + " 只输出合法 JSON。", user)
    except Exception:
        return None
    text = (raw or "").strip().strip("`")
    if text.lower().startswith("json"):
        text = text[4:].strip()
    try:
        return json.loads(text)
    except Exception:
        o, c = text.find("{"), text.rfind("}")
        if 0 <= o < c:
            try:
                return json.loads(text[o:c + 1])
            except Exception:
                return None
        return None


def _profile_from_data(data: dict, fallback_samples: list[str], metrics: dict) -> StyleProfile:
    def s(k):
        v = data.get(k)
        return str(v).strip() if isinstance(v, str) else ""

    def lst(k):
        v = data.get(k)
        return [str(x).strip() for x in v if str(x).strip()] if isinstance(v, list) else []

    samples = lst("samples") or fallback_samples
    return StyleProfile(
        name=s("name"), source=s("source"), register=s("register"), rhythm=s("rhythm"),
        devices=lst("devices"), diction_do=lst("diction_do"), diction_dont=lst("diction_dont"),
        motifs=lst("motifs"), samples=[x[:150] for x in samples[:4]], metrics=metrics, enabled=True,
    )


def distill_from_works(llm: LLMClient | None, raw_text: str, name: str = "", source: str = "") -> StyleProfile:
    """入口A：上传作品原文 → 蒸馏文风 + 量化指纹。无 LLM 时仅出指纹 + 原文样例。"""
    metrics = compute_style_metrics(raw_text)
    fallback = _excerpts(raw_text)
    if llm is None:
        return StyleProfile(name=name, source=source, samples=fallback, metrics=metrics, enabled=True)
    data = _distill_json(
        llm,
        "你是文体测量学专家。阅读给定作品原文，**只提炼它的写作腔调**（不要复述情节）。"
        "输出 JSON：{name, source, register(语域/腔调), rhythm(句法节奏), devices:[标志性手法], "
        "diction_do:[偏好词类], diction_dont:[该作几乎不用的词类], motifs:[母题意象], "
        "samples:[2-4 段 60-150 字的**原文短样例**，仅供模仿腔调]}。所有字段用中文。",
        f"作品名/作者（可空）：{name} / {source}\n\n原文：\n{(raw_text or '')[:4000]}\n\n只输出 JSON。",
    )
    if not isinstance(data, dict):
        return StyleProfile(name=name, source=source, samples=fallback, metrics=metrics, enabled=True)
    prof = _profile_from_data(data, fallback, metrics)
    if name and not prof.name:
        prof.name = name
    if source and not prof.source:
        prof.source = source
    return prof


def parse_style_skill(llm: LLMClient | None, skill_md: str) -> StyleProfile:
    """入口B：上传现成 SKILL.md / 文风描述 → 归一化成 StyleProfile。
    指纹从其样例文本计算（若有）；无 LLM 时把全文当单条样例兜底。"""
    fallback = _excerpts(skill_md)
    if llm is None:
        return StyleProfile(samples=fallback, metrics=compute_style_metrics(skill_md), enabled=True)
    data = _distill_json(
        llm,
        "下面是一份现成的【文风技能/文风描述】文档。把它归一化成结构化文风档案。"
        "输出 JSON：{name, source, register, rhythm, devices:[…], diction_do:[…], "
        "diction_dont:[…], motifs:[…], samples:[文档里给出的风格样例，2-4 段，仅供模仿腔调]}。中文。",
        f"文档：\n{(skill_md or '')[:4000]}\n\n只输出 JSON。",
    )
    if not isinstance(data, dict):
        return StyleProfile(samples=fallback, metrics=compute_style_metrics(skill_md), enabled=True)
    # 指纹：优先据归一化后的样例算；样例为空则据全文
    prof = _profile_from_data(data, fallback, {})
    basis = "\n".join(prof.samples) or skill_md
    prof.metrics = compute_style_metrics(basis)
    return prof


def style_metric_gate(
    prose: str, profile: StyleProfile, tol: float = 0.6,
) -> tuple[bool, str]:
    """软闸门（可关）：本场指纹与目标文风偏离过大 → 反馈重写。低阈值防误报。
    仅比较两个最稳健的维度：平均句长、标点密度（相对偏差 > tol 才判不达标）。"""
    target = (profile.metrics or {}) if profile.is_set() else {}
    if not target or not (prose or "").strip():
        return True, ""
    cur = compute_style_metrics(prose)
    issues = []
    for key, cn in (("avg_sentence_len", "句子长度"), ("punct_density", "标点密度")):
        tv, cv = float(target.get(key, 0) or 0), float(cur.get(key, 0) or 0)
        if tv <= 0:
            continue
        if abs(cv - tv) / tv > tol:
            direction = "偏长，请多用短句、断句" if (cv > tv and key == "avg_sentence_len") else \
                        ("偏短，可让句子更舒展" if key == "avg_sentence_len" else
                         ("标点偏密，精简破折号/逗号" if cv > tv else "标点偏疏，节奏可更顿挫"))
            issues.append(f"{cn}与目标文风差距过大（{direction}）")
    if issues:
        return False, "；".join(issues) + "，请向目标文风的句法节奏靠拢重写。"
    return True, ""
