"""LLM 客户端抽象接口。

所有后端只需实现 complete(system, user) -> str，返回模型的纯文本回复。
这样 agent 层不关心具体是 DeepSeek 还是 mock。
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class LLMClient(ABC):
    @abstractmethod
    def complete(self, system: str, user: str) -> str:
        """给定 system / user 提示，返回模型回复文本。"""
        raise NotImplementedError

    def complete_at(self, system: str, user: str, temperature: float | None = None) -> str:
        """B1 双层解码：带可选采样温度的生成。

        默认实现**忽略 temperature、委托 complete**——这样所有现有子类/测试无需改动即可工作。
        支持温控的后端（如 DeepSeek）可重写本方法：创作层（SceneWriter）用高温（~0.9）抓灵气，
        约束层（FactExtractor/审计）用 0.0 求确定。
        """
        return self.complete(system, user)

    @property
    def name(self) -> str:
        return self.__class__.__name__
