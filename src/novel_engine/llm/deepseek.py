"""DeepSeek 后端：通过 OpenAI 兼容端点调用。

DeepSeek 的 API 与 OpenAI Chat Completions 兼容，仅 base_url / model 不同。
"""
from __future__ import annotations

import os
import sys
import threading

from .base import LLMClient


class _CacheStats:
    """进程级 DeepSeek 前缀缓存命中统计（用于量化 KV-cache 优化效果）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.calls = 0
        self.prompt_tokens = 0
        self.cache_hit_tokens = 0
        self.cache_miss_tokens = 0

    def record(self, usage: object) -> None:
        hit = int(getattr(usage, "prompt_cache_hit_tokens", 0) or 0)
        miss = int(getattr(usage, "prompt_cache_miss_tokens", 0) or 0)
        prompt = int(getattr(usage, "prompt_tokens", 0) or 0)
        with self._lock:
            self.calls += 1
            self.prompt_tokens += prompt
            self.cache_hit_tokens += hit
            self.cache_miss_tokens += miss
        if os.environ.get("NOVEL_CACHE_LOG"):
            denom = hit + miss or prompt or 1
            print(f"[cache] hit={hit} miss={miss} ratio={hit / denom:.0%}", file=sys.stderr)

    def summary(self) -> dict:
        with self._lock:
            denom = self.cache_hit_tokens + self.cache_miss_tokens or self.prompt_tokens or 1
            return {
                "calls": self.calls,
                "prompt_tokens": self.prompt_tokens,
                "cache_hit_tokens": self.cache_hit_tokens,
                "cache_miss_tokens": self.cache_miss_tokens,
                "hit_ratio": self.cache_hit_tokens / denom,
            }


CACHE_STATS = _CacheStats()


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
        if getattr(resp, "usage", None) is not None:
            CACHE_STATS.record(resp.usage)
        return resp.choices[0].message.content or ""

    @property
    def name(self) -> str:
        return f"DeepSeek({self._model})"
