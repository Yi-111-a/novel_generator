"""叙述/剪辑层（M3，设计文档 §4 + §6.3）。

独立于模拟运行：把世界库里的 events 重排、删选、渲染成好看的小说。
叙述者被刻意"变笨"——只能看 POV 账本 + 读者账本，看不到世界全貌。
"""
from .editor import Editor
from .exposition import Exposition
from .foreshadow import (
    ForeshadowLedger,
    backfill_payoff_beats,
    finalize_ending,
    honesty_gate,
)
from .style import AntiAbstractValidator, STYLE_BIBLE
from .tension import TensionScheduler

__all__ = [
    "Editor",
    "AntiAbstractValidator",
    "STYLE_BIBLE",
    "ForeshadowLedger",
    "honesty_gate",
    "backfill_payoff_beats",
    "finalize_ending",
    "Exposition",
    "TensionScheduler",
]
