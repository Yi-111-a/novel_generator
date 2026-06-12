"""按配置选择 LLM 后端。

缺 key 或 provider=mock 时回退 MockClient，保证骨架始终可跑。
"""
from __future__ import annotations

from ..config import LLMConfig, load_llm_config
from .base import LLMClient
from .mock import MockClient


def build_client(config: LLMConfig | None = None) -> LLMClient:
    config = config or load_llm_config()

    if config.provider == "mock":
        return MockClient()

    if config.provider == "deepseek":
        if not config.has_key:
            print("[llm] 未检测到 DEEPSEEK_API_KEY，回退到 MockClient。")
            return MockClient()
        from .deepseek import DeepSeekClient

        return DeepSeekClient(
            api_key=config.api_key or "",
            model=config.model,
            base_url=config.base_url,
        )

    print(f"[llm] 未知 provider '{config.provider}'，回退到 MockClient。")
    return MockClient()
