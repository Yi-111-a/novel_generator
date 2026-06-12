"""DeepSeek 后端：通过 OpenAI 兼容端点调用。

DeepSeek 的 API 与 OpenAI Chat Completions 兼容，仅 base_url / model 不同。
"""
from __future__ import annotations

from .base import LLMClient


class DeepSeekClient(LLMClient):
    def __init__(
        self,
        api_key: str,
        model: str = "deepseek-chat",
        base_url: str = "https://api.deepseek.com",
        temperature: float = 0.8,
    ) -> None:
        if not api_key:
            raise ValueError("DeepSeekClient 需要 api_key")
        # 延迟导入，避免无 key 场景强依赖 openai
        from openai import OpenAI

        self._client = OpenAI(api_key=api_key, base_url=base_url)
        self._model = model
        self._temperature = temperature

    def complete(self, system: str, user: str) -> str:
        return self.complete_at(system, user, None)

    def complete_at(self, system: str, user: str, temperature: float | None = None) -> str:
        # 仅当提示词确实要求 JSON 时才启用 json_object 模式：
        # DeepSeek 规定该模式下提示里必须出现 "json" 字样，否则报 400；
        # 散文渲染（叙述者）等自由文本调用必须走普通模式。
        # B1 双层解码：temperature 给定即覆盖默认（创作层高温/约束层 0.0）。
        kwargs: dict = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self._temperature if temperature is None else temperature,
        }
        if "json" in (system + user).lower():
            kwargs["response_format"] = {"type": "json_object"}
        resp = self._client.chat.completions.create(**kwargs)
        return resp.choices[0].message.content or ""

    @property
    def name(self) -> str:
        return f"DeepSeek({self._model})"
