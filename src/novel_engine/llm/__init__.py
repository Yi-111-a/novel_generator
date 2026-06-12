"""LLM 通用接口与后端实现。"""
from .base import LLMClient
from .factory import build_client
from .mock import MockClient

__all__ = ["LLMClient", "build_client", "MockClient"]
