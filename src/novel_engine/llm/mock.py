"""Mock 后端：脚本化输出，无需 key / 网络。

用途：
- 单元测试与骨架跑通（CI 友好、确定性）。
- 演示校验层时，可故意喂"幻觉"动作（引用账本外 fact / 不存在实体 / 违反物理）。

用法：用一组预设回复初始化；每次 complete() 弹出下一条。耗尽后重复最后一条。
"""
from __future__ import annotations

import json
from typing import Iterable

from .base import LLMClient


class MockClient(LLMClient):
    def __init__(self, responses: Iterable[str] | None = None) -> None:
        self._responses = list(responses or [])
        self._idx = 0

    @classmethod
    def from_actions(cls, actions: Iterable[dict]) -> "MockClient":
        """用一组动作 dict 构造，自动序列化为 JSON 字符串。"""
        return cls([json.dumps(a, ensure_ascii=False) for a in actions])

    def complete(self, system: str, user: str) -> str:
        if not self._responses:
            # 无脚本时给一个无害的默认动作
            return json.dumps(
                {
                    "intent": "observe",
                    "target": None,
                    "dialogue": "",
                    "inner_thought": "（沉默）",
                    "chosen_value": "",
                    "referenced_facts": [],
                    "referenced_entities": [],
                },
                ensure_ascii=False,
            )
        resp = self._responses[min(self._idx, len(self._responses) - 1)]
        self._idx += 1
        return resp

    @property
    def name(self) -> str:
        return "Mock"
