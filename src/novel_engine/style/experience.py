from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

from ..continuation.importer import load_sources
from ..llm.base import LLMClient
from ..models import (
    AuthorExperienceFragment,
    AuthorExperienceSource,
    AuthorLifeModel,
)
from ..repository import Repository

_TAG_RULES: dict[str, tuple[str, ...]] = {
    "shame": ("自卑", "羞耻", "窘迫", "寒酸", "穷", "不敢", "怕丢脸", "没出息", "配不上"),
    "loneliness": ("孤独", "一个人", "寂寞", "无人", "没人", "被丢下", "离开我", "落单"),
    "outsider": ("旁观", "站在外面", "局外人", "不属于", "格格不入", "看着别人"),
    "nostalgia": ("小时候", "少年", "那年", "那时候", "旧日", "回忆", "故乡", "青春"),
    "ambition": ("梦想", "野心", "想成为", "想去", "远方", "出发", "赢", "证明"),
    "defensive_humor": ("笑", "吐槽", "开玩笑", "装作", "若无其事", "轻描淡写"),
    "distance_intimacy": ("喜欢", "爱", "靠近", "拥抱", "朋友", "又不敢", "退后"),
    "loss": ("失去", "离开", "告别", "死亡", "葬礼", "没了", "找不回"),
    "craft": ("写作", "小说", "作者", "故事", "写下来", "句子", "叙述"),
}

_EMOTION_RULES: dict[str, tuple[str, ...]] = {
    "shame": ("自卑", "羞耻", "局促", "窘", "难堪"),
    "loneliness": ("孤独", "寂寞", "空荡", "没人", "落单"),
    "nostalgia": ("想起", "旧日", "回忆", "青春", "那年"),
    "sadness": ("难过", "伤心", "眼泪", "哀", "失去"),
    "tenderness": ("温柔", "轻轻", "安静", "柔软", "喜欢"),
    "defense": ("笑", "吐槽", "假装", "装作", "耸肩"),
}


def distill_author_experience(
    repo: Repository,
    *,
    project_id: str,
    source_path: str,
    llm: LLMClient | None = None,
    source_text: str = "",
) -> AuthorLifeModel | None:
    path = str(source_path or "").strip()
    if not path:
        return None

    text = _load_experience_text(path)
    if not text.strip():
        return None

    source_id = f"expsrc_{uuid.uuid4().hex[:12]}"
    repo.clear_author_experience()
    repo.insert_author_experience_source(
        AuthorExperienceSource(
            source_id=source_id,
            project_id=project_id,
            label=Path(path).stem or "作者经历材料",
            source_type="essay",
            path=path,
            content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            enabled=True,
            created_at="now",
        )
    )

    fragments = _build_fragments(text=text, project_id=project_id, source_id=source_id)
    for fragment in fragments:
        repo.insert_author_experience_fragment(fragment)

    model = _infer_life_model(
        text=text,
        fragments=fragments,
        label=Path(path).stem or "作者经历材料",
        source_text=source_text,
        llm=llm,
        project_id=project_id,
        source_id=source_id,
    )
    repo.upsert_author_life_model(model)
    meta = repo.get_continuation_meta()
    meta.active_life_model_id = model.model_id
    repo.set_continuation_meta(meta)
    return model


def _load_experience_text(path: str) -> str:
    loaded = load_sources(file_path=path)
    return loaded[0].text if loaded else ""


def _build_fragments(*, text: str, project_id: str, source_id: str) -> list[AuthorExperienceFragment]:
    blocks = [block.strip() for block in re.split(r"\n\s*\n+", text.replace("\r", "")) if len(block.strip()) >= 80]
    if not blocks:
        blocks = [text.strip()]
    merged: list[str] = []
    buf = ""
    for block in blocks:
        if len(buf) + len(block) <= 900:
            buf = f"{buf}\n{block}".strip()
        else:
            if buf:
                merged.append(buf)
            buf = block
    if buf:
        merged.append(buf)
    out: list[AuthorExperienceFragment] = []
    for idx, frag_text in enumerate(merged, 1):
        tags = _match_tags(frag_text, _TAG_RULES)
        emotions = _match_tags(frag_text, _EMOTION_RULES)
        out.append(
            AuthorExperienceFragment(
                fragment_id=f"expfrag_{uuid.uuid4().hex[:12]}",
                project_id=project_id,
                source_id=source_id,
                fragment_index=idx,
                title_hint=_title_hint(frag_text),
                text=frag_text,
                tags_json=tags,
                emotion_json=emotions,
                self_schema_json=_self_schema(frag_text, tags),
                confidence=_fragment_confidence(tags, emotions),
            )
        )
    return out


def _infer_life_model(
    *,
    text: str,
    fragments: list[AuthorExperienceFragment],
    label: str,
    source_text: str,
    llm: LLMClient | None,
    project_id: str,
    source_id: str,
) -> AuthorLifeModel:
    heuristic = _heuristic_life_model(text=text, fragments=fragments, label=label, project_id=project_id, source_id=source_id)
    if llm is None:
        return heuristic
    prompt = _life_model_prompt(label=label, text=text, fragments=fragments, source_text=source_text)
    try:
        raw = llm.complete(
            "你是小说作家人格分析师。请从作者随笔与经历材料中，抽象出会长期影响其叙事与文风的生命经验模型。只输出合法 JSON。",
            prompt,
        ).strip().strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
        data = json.loads(raw)
        if isinstance(data, dict):
            return _model_from_llm_dict(
                data=data,
                fallback=heuristic,
                project_id=project_id,
                source_id=source_id,
            )
    except Exception:
        pass
    return heuristic


# 通用标签→中文人话映射（用于把检测到的 tag 翻成可读的人格信号，不预设任何具体作者）
_TAG_CN = {
    "shame": "羞耻感", "outsider": "局外人姿态", "loneliness": "孤独",
    "nostalgia": "怀旧", "ambition": "渴望被承认", "distance_intimacy": "亲密中的距离感",
    "loss": "丧失", "sadness": "忧伤", "anger": "愤怒", "fear": "恐惧", "joy": "欢愉",
    "family": "家庭羁绊", "duty": "责任", "freedom": "自由",
}


def _heuristic_life_model(
    *,
    text: str,
    fragments: list[AuthorExperienceFragment],
    label: str,
    project_id: str,
    source_id: str,
) -> AuthorLifeModel:
    """无 LLM / LLM 失败时的**通用、数据驱动**兜底。

    只根据实际检测到的 tag / emotion 频次生成一个中性、低置信度的画像，
    绝不预设任何具体作者（如自卑/羞耻/少年命运那一套），避免把任意作者都写成同一个人。
    """
    tag_counts = Counter(tag for frag in fragments for tag in frag.tags_json)
    emotion_counts = Counter(em for frag in fragments for em in frag.emotion_json)
    top_tags = [t for t, _ in tag_counts.most_common(4)]
    top_tags_cn = [_TAG_CN.get(t, t) for t in top_tags]
    top_emotions = [e for e, _ in emotion_counts.most_common(3)]

    core_wound = {
        "name": top_tags[0] if top_tags else "unknown",
        "statement": (f"材料中反复出现的主题：{('、'.join(top_tags_cn))}。"
                      if top_tags_cn else "材料信号不足，未能可靠归纳核心创伤。"),
        "surfaceSigns": top_tags_cn[:3],
    }
    desire_vectors = [
        {"name": _TAG_CN.get(t, t), "weight": round(min(1.0, n / max(1, len(fragments))), 3)}
        for t, n in tag_counts.most_common(3)
    ]
    relationship_model = {
        "baseline": (f"关系基调倾向：{('、'.join(top_tags_cn[:2]))}。" if top_tags_cn
                     else "材料不足，未归纳关系模型。"),
        "rules": [],
    }
    narrative_engines = [{"engine": t, "rule": f"作者经历中{_TAG_CN.get(t, t)}信号较强，可作为叙事母题。"}
                         for t in top_tags[:2]]
    prose_rules = {"tone": "", "sentenceStrategy": [], "characterEngine": []}
    worldview = {
        "dominantMood": top_emotions[0] if top_emotions else "",
        "belief": "",
    }
    evidence = _top_evidence(fragments)
    if top_tags_cn:
        persona_prompt = (
            f"[作者经历层·{label}（启发式兜底·低置信）]\n"
            f"从作者经历材料中检测到的主要信号：{('、'.join(top_tags_cn))}。"
            "写作时可让人物动机与这些信号呼应，但因未经 LLM 深度分析，仅作弱先验，"
            "不要据此强行套用某种固定人格。"
        )
    else:
        persona_prompt = ""
    confidence = {
        "coverage": round(min(1.0, len(fragments) / 12), 3),
        "signalDensity": round(min(1.0, sum(tag_counts.values()) / max(1, len(fragments) * 3)), 3),
        "dominantTags": dict(tag_counts.most_common(6)),
        "mode": "heuristic_fallback",
    }
    summary = (f"基于经历材料标签频次的弱画像，主要信号：{('、'.join(top_tags_cn))}。"
               if top_tags_cn else "经历材料信号不足，未能生成可靠画像。")
    return AuthorLifeModel(
        model_id=f"lifemodel_{uuid.uuid4().hex[:12]}",
        project_id=project_id,
        source_ids_json=[source_id],
        source_label=label,
        summary=summary,
        core_wound_json=core_wound,
        defense_patterns_json=[],
        desire_vectors_json=desire_vectors,
        relationship_model_json=relationship_model,
        narrative_engines_json=narrative_engines,
        prose_rules_json=prose_rules,
        worldview_json=worldview,
        evidence_json=evidence,
        confidence_json=confidence,
        persona_prompt=persona_prompt,
        created_at="now",
    )


def _life_model_prompt(*, label: str, text: str, fragments: list[AuthorExperienceFragment], source_text: str) -> str:
    evidence = "\n\n".join(
        f"[片段{frag.fragment_index}][{','.join(frag.tags_json[:4])}] {frag.text[:260]}"
        for frag in fragments[:12]
    )
    novel_hint = source_text[:1800] if source_text else ""
    return (
        f"材料名：{label}\n\n"
        "请输出 JSON，字段如下："
        '{"summary":"","core_wound":{"name":"","statement":"","surfaceSigns":[]},"defense_patterns":[{"pattern":"","rule":""}],'
        '"desire_vectors":[{"name":"","weight":0.0}],"relationship_model":{"baseline":"","rules":[]},"narrative_engines":[{"engine":"","rule":""}],'
        '"prose_rules":{"tone":"","sentenceStrategy":[],"characterEngine":[]},"worldview":{"dominantMood":"","belief":""},'
        '"evidence":[{"fragmentIndex":0,"why":"","excerpt":""}],"confidence":{"coverage":0.0,"signalDensity":0.0},"persona_prompt":""}\n\n'
        "要求：分析作者是什么样的人，这种生命经验如何投射成文风与人物写法；重点抓自卑、羞耻、旁观感、命运感、少年性。"
        "不要写空泛赞美，不要只写语言标签，要写成可用于续写控制的规则。\n\n"
        f"随笔证据：\n{evidence}\n\n"
        f"小说侧参考（可选）：\n{novel_hint}"
    )


def _model_from_llm_dict(
    *,
    data: dict[str, Any],
    fallback: AuthorLifeModel,
    project_id: str,
    source_id: str,
) -> AuthorLifeModel:
    return AuthorLifeModel(
        model_id=f"lifemodel_{uuid.uuid4().hex[:12]}",
        project_id=project_id,
        source_ids_json=[source_id],
        source_label=fallback.source_label,
        summary=str(data.get("summary") or fallback.summary),
        core_wound_json=_as_dict(data.get("core_wound"), fallback.core_wound_json),
        defense_patterns_json=_as_list_of_dicts(data.get("defense_patterns"), fallback.defense_patterns_json),
        desire_vectors_json=_as_list_of_dicts(data.get("desire_vectors"), fallback.desire_vectors_json),
        relationship_model_json=_as_dict(data.get("relationship_model"), fallback.relationship_model_json),
        narrative_engines_json=_as_list_of_dicts(data.get("narrative_engines"), fallback.narrative_engines_json),
        prose_rules_json=_as_dict(data.get("prose_rules"), fallback.prose_rules_json),
        worldview_json=_as_dict(data.get("worldview"), fallback.worldview_json),
        evidence_json=_as_list_of_dicts(data.get("evidence"), fallback.evidence_json),
        confidence_json=_as_dict(data.get("confidence"), fallback.confidence_json),
        persona_prompt=str(data.get("persona_prompt") or fallback.persona_prompt),
        created_at="now",
    )


def _match_tags(text: str, rules: dict[str, tuple[str, ...]]) -> list[str]:
    matched: list[str] = []
    for tag, words in rules.items():
        if any(word in text for word in words):
            matched.append(tag)
    return matched


def _title_hint(text: str) -> str:
    first = re.split(r"[\n。！？!?]", text, maxsplit=1)[0].strip()
    return first[:24]


def _self_schema(text: str, tags: list[str]) -> dict[str, Any]:
    return {
        "selfWorth": "fragile" if any(tag in tags for tag in ("shame", "outsider")) else "guarded",
        "attachment": "approach_avoidant" if "distance_intimacy" in tags else "observant",
        "memoryBias": "nostalgic" if "nostalgia" in tags else "present",
        "narrativePosition": "edge_of_crowd" if "outsider" in tags else "inside_scene",
    }


def _fragment_confidence(tags: list[str], emotions: list[str]) -> float:
    return round(min(0.95, 0.35 + 0.12 * len(tags) + 0.08 * len(emotions)), 3)


def _dominant(counter: Counter[str], prefs: tuple[str, ...]) -> str:
    for key in prefs:
        if counter.get(key):
            return key
    return counter.most_common(1)[0][0] if counter else ""


def _weight(counter: Counter[str], key: str, *, fallback: float) -> float:
    total = sum(counter.values())
    if total <= 0:
        return fallback
    return min(0.95, 0.4 + 0.6 * (counter.get(key, 0) / total))


def _top_evidence(fragments: list[AuthorExperienceFragment]) -> list[dict[str, Any]]:
    ranked = sorted(fragments, key=lambda item: (item.confidence, len(item.tags_json), len(item.text)), reverse=True)
    return [
        {
            "fragmentIndex": frag.fragment_index,
            "why": "、".join(frag.tags_json[:4]) or "作者自我暴露",
            "excerpt": frag.text[:160],
        }
        for frag in ranked[:6]
    ]


def _as_dict(value: Any, fallback: dict[str, Any]) -> dict[str, Any]:
    return value if isinstance(value, dict) else fallback


def _as_list_of_dicts(value: Any, fallback: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if isinstance(value, list):
        out = [item for item in value if isinstance(item, dict)]
        if out:
            return out
    return fallback
