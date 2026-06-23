"""LLM 日志包装器：透明记录所有 LLM 调用的 system/user/response 到 llm_logs 表。"""
from __future__ import annotations

import json
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from .base import LLMClient


class LoggingLLMClient(LLMClient):
    """包在真实 LLMClient 外面，每次调用自动写 llm_logs。"""

    def __init__(self, inner: LLMClient, conn, caller: str = "") -> None:
        self._inner = inner
        self._conn = conn
        self._caller = caller
        self._scope_caller: ContextVar[str] = ContextVar(
            f"llm_caller_{id(self)}", default=""
        )
        self._scope_meta: ContextVar[dict[str, Any]] = ContextVar(
            f"llm_meta_{id(self)}", default={}
        )

    def _log(self, system: str, user: str, response: str,
             temperature: float | None, elapsed_ms: int, meta: dict | None = None) -> None:
        try:
            self._conn.execute(
                """INSERT INTO llm_logs (ts, caller, system_msg, user_msg, response,
                   temperature, elapsed_ms, meta) VALUES (?,?,?,?,?,?,?,?)""",
                (time.time(), self._scope_caller.get() or self._caller, system, user, response,
                 temperature, elapsed_ms, json.dumps(meta or {}, ensure_ascii=False)),
            )
            self._conn.commit()
        except Exception:
            pass

    def complete(self, system: str, user: str) -> str:
        t0 = time.time()
        resp = self._inner.complete(system, user)
        ms = int((time.time() - t0) * 1000)
        self._log(
            system,
            user,
            resp,
            None,
            ms,
            meta=self._scope_meta.get(),
        )
        return resp

    def complete_at(self, system: str, user: str, temperature: float | None = None) -> str:
        t0 = time.time()
        resp = self._inner.complete_at(system, user, temperature)
        ms = int((time.time() - t0) * 1000)
        self._log(
            system,
            user,
            resp,
            temperature,
            ms,
            meta=self._scope_meta.get(),
        )
        return resp

    @property
    def name(self) -> str:
        return f"Logging({self._inner.name})"

    def with_caller(self, caller: str) -> "LoggingLLMClient":
        """返回同 inner/conn 但不同 caller 标签的包装器。"""
        return LoggingLLMClient(self._inner, self._conn, caller)

    @contextmanager
    def scope(self, *, caller: str = "", meta: dict[str, Any] | None = None):
        """Temporarily attach a precise caller and structured trace metadata."""
        current_meta = dict(self._scope_meta.get() or {})
        current_meta.update(meta or {})
        caller_token = self._scope_caller.set(caller or self._scope_caller.get())
        meta_token = self._scope_meta.set(current_meta)
        try:
            yield self
        finally:
            self._scope_caller.reset(caller_token)
            self._scope_meta.reset(meta_token)
