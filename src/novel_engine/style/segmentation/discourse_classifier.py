from __future__ import annotations

import re

_DIALOGUE_RE = re.compile(r"[“\"「『].+?[”\"」』]")
_ACTION_HINTS = ("走", "冲", "抓", "抬", "看", "推", "拉", "站", "坐", "转身", "开门", "扣", "撞")
_REFLECTION_HINTS = ("觉得", "想", "意识到", "明白", "忽然", "仿佛", "命运", "像是", "也许", "如果")
_TRANSITION_HINTS = ("然后", "随后", "片刻后", "第二天", "不久", "与此同时", "那天晚上", "过了一会儿")
_DESCRIPTION_HINTS = ("夜", "风", "灯", "雨", "光", "影", "楼", "街", "气味", "潮湿", "灰尘", "月光")
_INTERIOR_HINTS = ("我想", "我知道", "他想", "她想", "心里", "脑子里", "忍不住", "忽然觉得")
_FREE_INDIRECT_HINTS = ("像是", "原来", "竟然", "怎么会", "难道", "未免")
_EMOTION_HINTS = {
    "suppressed_anger": ("怒", "火", "烦", "暴", "咬牙"),
    "sadness": ("难过", "空", "冷", "坠", "失落"),
    "fear": ("怕", "慌", "惊", "颤", "抖"),
    "defensive_humor": ("笑", "吐槽", "耸肩", "玩笑"),
    "awe": ("震", "神圣", "宏大", "龙", "星空"),
}


def classify_discourse_type(text: str) -> str:
    if _DIALOGUE_RE.search(text):
        return "dialogue"
    if any(hint in text for hint in _INTERIOR_HINTS):
        return "interior"
    if any(hint in text for hint in _TRANSITION_HINTS):
        return "transition"
    if any(hint in text for hint in _REFLECTION_HINTS):
        return "reflection"
    if any(hint in text for hint in _ACTION_HINTS):
        return "action"
    if any(hint in text for hint in _FREE_INDIRECT_HINTS):
        return "free_indirect"
    if any(token in text for token in _DESCRIPTION_HINTS):
        return "description"
    return "narration"


def classify_voice_type(text: str, discourse_type: str) -> str:
    if discourse_type == "dialogue":
        return "character"
    if discourse_type in {"interior", "free_indirect"}:
        return "mixed"
    if "我" in text or "自己" in text:
        return "mixed"
    return "narrator"


def classify_scene_type(text: str, discourse_type: str) -> str:
    if discourse_type == "dialogue" and any(token in text for token in ("问", "答", "笑", "沉默")):
        return "conversation"
    if any(token in text for token in ("打", "冲", "血", "枪", "刀")):
        return "confrontation"
    if any(token in text for token in ("秘密", "线索", "发现", "怀疑", "试探", "隐瞒")):
        return "investigation"
    if discourse_type in {"reflection", "interior", "free_indirect"}:
        return "introspection"
    return "general"


def classify_emotion(text: str) -> list[str]:
    out: list[str] = []
    for label, hints in _EMOTION_HINTS.items():
        if any(hint in text for hint in hints):
            out.append(label)
    return out or ["neutral"]


def classify_register(text: str, discourse_type: str) -> str:
    if discourse_type == "dialogue":
        return "colloquial"
    if any(token in text for token in ("命运", "世界", "龙", "夜空", "神圣")):
        return "elevated"
    return "neutral"


def estimate_annotation_confidence(text: str, discourse_type: str, voice_type: str) -> float:
    confidence = 0.45
    if len(text) >= 120:
        confidence += 0.15
    if discourse_type in {"dialogue", "action", "reflection", "interior"}:
        confidence += 0.15
    if voice_type != "mixed":
        confidence += 0.1
    if len(re.findall(r"[。！？!?]", text)) >= 1:
        confidence += 0.05
    return round(min(0.95, confidence), 3)


def guess_character_id(text: str, known_characters: dict[str, str]) -> str | None:
    for agent_id, name in known_characters.items():
        if name and name in text:
            return agent_id
    return None
