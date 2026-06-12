"""运行配置：从环境变量读取 LLM 后端设置。

优先加载项目根目录的 .env（若安装了 python-dotenv）。
"""
from __future__ import annotations

import os
from dataclasses import dataclass

try:  # python-dotenv 可选
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - 缺依赖时静默跳过
    pass


@dataclass(frozen=True)
class LLMConfig:
    provider: str = "deepseek"
    model: str = "deepseek-chat"
    base_url: str = "https://api.deepseek.com"
    api_key: str | None = None

    @property
    def has_key(self) -> bool:
        return bool(self.api_key)


def load_llm_config() -> LLMConfig:
    """按环境变量构造 LLM 配置。"""
    return LLMConfig(
        provider=os.getenv("LLM_PROVIDER", "deepseek").strip().lower(),
        model=os.getenv("LLM_MODEL", "deepseek-chat").strip(),
        base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com").strip(),
        api_key=os.getenv("DEEPSEEK_API_KEY") or os.getenv("LLM_API_KEY"),
    )
