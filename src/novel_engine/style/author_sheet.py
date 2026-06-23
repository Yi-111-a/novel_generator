# -*- coding: utf-8 -*-
"""AuthorWritingSheet 工具：AWS → StyleProfile/persona 派生。

AWS 是上位结构（四维 Claim-Evidence），StyleProfile 是它的扁平投影。
派生后现有注入路径（style_skill_prompt）、6 维指纹、style_metric_gate 零改动。
"""
from __future__ import annotations

import json

from ..llm.base import LLMClient
from ..models import AuthorWritingSheet, StyleClaim, StyleProfile
from ..style_skill import compute_style_metrics


def derive_style_profile(sheet: AuthorWritingSheet, sample_text: str = "",
                         llm: LLMClient | None = None) -> StyleProfile:
    """从 AWS 派生出 StyleProfile（含 persona_md + 6 维指纹）。"""
    lang_claims = [c.claim for c in sheet.language]
    crea_claims = [c.claim for c in sheet.creativity]

    register = ""
    rhythm = ""
    devices: list[str] = []
    diction_do: list[str] = []
    diction_dont: list[str] = []
    motifs: list[str] = []

    for claim in lang_claims:
        low = claim.lower()
        if any(k in low for k in ("语域", "腔调", "语气", "语调", "风格")):
            register = register or claim
        elif any(k in low for k in ("节奏", "句式", "句法", "长短")):
            rhythm = rhythm or claim
        elif any(k in low for k in ("禁忌", "避免", "不用", "不使用")):
            diction_dont.append(claim)
        else:
            diction_do.append(claim)

    for claim in crea_claims:
        low = claim.lower()
        if any(k in low for k in ("手法", "技巧", "修辞", "隐喻", "比喻")):
            devices.append(claim)
        elif any(k in low for k in ("意象", "母题", "符号")):
            motifs.append(claim)
        else:
            devices.append(claim)

    samples = [c.evidence for c in (sheet.language + sheet.creativity) if c.evidence][:4]
    metrics = compute_style_metrics(sample_text) if sample_text else {}
    persona_md = generate_persona(sheet, llm)

    return StyleProfile(
        name=sheet.name, source=sheet.source_genre,
        register=register[:200], rhythm=rhythm[:200],
        devices=devices[:8], diction_do=diction_do[:8], diction_dont=diction_dont[:8],
        motifs=motifs[:8], samples=[s[:150] for s in samples],
        metrics=metrics, persona_md=persona_md, enabled=True,
    )


def generate_persona(sheet: AuthorWritingSheet, llm: LLMClient | None = None) -> str:
    """从 AWS 生成二人称 persona description（注入 system prompt）。"""
    if not sheet.is_set():
        return ""

    all_claims: list[str] = []
    for dim, label in [("language", "语言运用"), ("creativity", "创意手法"),
                       ("plot", "情节偏好"), ("development", "发展节奏")]:
        claims = getattr(sheet, dim, [])
        if claims:
            top = [c.claim for c in claims[:5]]
            all_claims.append(f"【{label}】" + "；".join(top))

    claims_block = "\n".join(all_claims)

    if llm is None:
        return (
            f"[作者文风画像 · {sheet.name or '未命名'}]\n"
            f"你是一位具有以下写作特征的作者：\n{claims_block}\n"
            "请以上述特征写作，保持文风一致性。"
        )

    system = (
        "你是文风分析专家。根据下面的 Author Writing Sheet（作者写作画像），"
        "生成一段 200 字以内的二人称 persona description，用于注入小说生成的 system prompt。"
        "格式：「你是一位……的作者。你的写作特点是……」。聚焦可执行的风格特征，不要空泛赞美。"
    )
    user = f"作者名/体裁：{sheet.name} / {sheet.source_genre}\n\n{claims_block}"
    try:
        return llm.complete(system, user).strip()
    except Exception:
        return (
            f"[作者文风画像 · {sheet.name or '未命名'}]\n"
            f"你是一位具有以下写作特征的作者：\n{claims_block}\n"
            "请以上述特征写作，保持文风一致性。"
        )
