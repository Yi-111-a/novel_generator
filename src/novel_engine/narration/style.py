"""风格圣经 + 反抽象校验（设计文档 §4.5）。

硬规则：情绪只能经由动作、物件、感官细节呈现；**禁止形容词直接点名情绪**
（"她很伤心"判不通过）。渲染后跑反抽象校验：抽象情绪词密度超阈值 → 打回重写。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

STYLE_BIBLE = (
    "限制性第三人称。情绪只能经由动作、物件、感官细节呈现，"
    "禁止用形容词/状态词直接点名情绪（如'伤心''愤怒''害怕''高兴'）。"
    "优先复用该角色的习惯动作与关联意象，让母题自然生长。"
)

# 直接点名情绪的词（命中即视为"抽象点名"）
BANNED_EMOTION_WORDS = [
    "悲伤", "伤心", "难过", "痛苦", "愤怒", "生气", "恼怒", "暴怒",
    "害怕", "恐惧", "惊恐", "畏惧", "高兴", "开心", "快乐", "喜悦",
    "兴奋", "激动", "紧张", "焦虑", "绝望", "失望", "嫉妒", "羞愧",
    "孤独", "忧伤", "欣喜", "愉快",
]


@dataclass
class StyleResult:
    ok: bool
    density: float
    hits: list[str] = field(default_factory=list)

    def summary(self) -> str:
        if self.ok:
            return f"PASS（抽象情绪词密度 {self.density}）"
        return f"FAIL：直接点名情绪 {self.hits}（密度 {self.density} 超阈值）"


class AntiAbstractValidator:
    """抽象情绪词密度 = 命中词总字数 / 文本字数。超阈值 → 打回。"""

    def __init__(self, max_density: float = 0.0, banned: list[str] | None = None) -> None:
        # 默认 0.0：一旦出现直接点名情绪即判不通过（最严格，契合"禁止"）
        self.max_density = max_density
        self.banned = banned or BANNED_EMOTION_WORDS

    def check(self, prose: str) -> StyleResult:
        text = re.sub(r"\s+", "", prose)
        if not text:
            return StyleResult(ok=False, density=0.0, hits=[])
        hits: list[str] = []
        hit_chars = 0
        for w in self.banned:
            cnt = prose.count(w)
            if cnt:
                hits.append(w)
                hit_chars += cnt * len(w)
        density = round(hit_chars / len(text), 4)
        return StyleResult(ok=density <= self.max_density, density=density, hits=hits)
