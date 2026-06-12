"""LLM 日志包装器：透明记录所有 LLM 调用的 system/user/response 到 llm_logs 表。"""
from __future__ import annotations

import json
import time
from typing import Any

from .base import LLMClient


class LoggingLLMClient(LLMClient):
    """包在真实 LLMClient 外面，每次调用自动写 llm_logs。"""

    def __init__(self, inner: LLMClient, conn, caller: str = "") -> None:
        self._inner = inner
        self._conn = conn
        self._caller = caller

    def _log(self, system: str, user: str, response: str,
             temperature: float | None, elapsed_ms: int, meta: dict | None = None) -> None:
        try:
            self._conn.execute(
                """INSERT INTO llm_logs (ts, caller, system_msg, user_msg, response,
                   temperature, elapsed_ms, meta) VALUES (?,?,?,?,?,?,?,?)""",
                (time.time(), self._caller, system, user, response,
                 temperature, elapsed_ms, json.dumps(meta or {}, ensure_ascii=False)),
            )
            self._conn.commit()
        except Exception:
            pass

    def complete(self, system: str, user: str) -> str:
        t0 = time.time()
        resp = self._inner.complete(system, user)
        ms = int((time.time() - t0) * 1000)
        self._log(system, user, resp, None, ms)
        return resp

    def complete_at(self, system: str, user: str, temperature: float | None = None) -> str:
        t0 = time.time()
        resp = self._inner.complete_at(system, user, temperature)
        ms = int((time.time() - t0) * 1000)
        self._log(system, user, resp, temperature, ms)
        return resp

    @property
    def name(self) -> str:
        return f"Logging({self._inner.name})"

    def with_caller(self, caller: str) -> "LoggingLLMClient":
        """返回同 inner/conn 但不同 caller 标签的包装器。"""
        return LoggingLLMClient(self._inner, self._conn, caller)
