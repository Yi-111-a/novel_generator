"""KeyPoolClient: 多 API key 轮询 + 每 key 限流 + 自动退避切换。

设计要点：
- 暴露同步 complete(system, user) 接口，兼容现有所有 LLMClient 调用方
- 内部用 threading.Semaphore 控制每个 key 的并发上限
- 调用时 round-robin 选 key，命中限流（429/503/超时）就标记冷却 + 顺延下一个
- 全部 key 都在冷却时，等待最早可用的那个
- 不是 async 库，省一层 asyncio 改造成本；并发由调用方（如 ThreadPoolExecutor）触发，
  本客户端只保证"多线程下 N 个 key 能被并行用满"
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Iterable

from .base import LLMClient


@dataclass
class _KeySlot:
    key: str
    semaphore: threading.Semaphore
    cooldown_until: float = 0.0
    used: int = 0
    failed: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)


class KeyPoolClient(LLMClient):
    """OpenAI-compatible HTTP 客户端，但 key 是一个轮询池。

    单 key 用法（兼容 MinimalOpenAICompatibleClient）：
        KeyPoolClient(["sk-xxx"], model="deepseek-chat")

    多 key 并发用法（在 ThreadPoolExecutor 里调）：
        client = KeyPoolClient([14 个 key], model="...", per_key_concurrency=5)
        with ThreadPoolExecutor(max_workers=14*5) as ex:
            futures = [ex.submit(client.complete, sys, usr) for ...]
    """

    def __init__(
        self,
        keys: Iterable[str],
        model: str,
        base_url: str = "https://api.deepseek.com",
        per_key_concurrency: int = 4,
        cooldown_seconds: float = 8.0,
        request_timeout: float = 120.0,
        max_retries: int = 3,
    ) -> None:
        slots: list[_KeySlot] = []
        for k in keys:
            k = (k or "").strip()
            if k:
                slots.append(_KeySlot(key=k, semaphore=threading.Semaphore(per_key_concurrency)))
        if not slots:
            raise ValueError("KeyPoolClient: no keys")
        self._slots = slots
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._cooldown = cooldown_seconds
        self._timeout = request_timeout
        self._max_retries = max_retries
        self._rr_lock = threading.Lock()
        self._rr_idx = 0

    @property
    def name(self) -> str:
        return f"KeyPoolClient(n_keys={len(self._slots)},model={self._model})"

    # ---- 内部：挑一个可用 slot ----
    def _next_slot(self) -> _KeySlot:
        """选择下一个非冷却 slot；全冷却时阻塞等待。"""
        n = len(self._slots)
        while True:
            now = time.time()
            with self._rr_lock:
                # 从当前 rr 位置扫一圈
                for offset in range(n):
                    idx = (self._rr_idx + offset) % n
                    s = self._slots[idx]
                    if s.cooldown_until <= now:
                        self._rr_idx = (idx + 1) % n
                        return s
                # 全在冷却 → 找最早能用的
                soonest = min(self._slots, key=lambda x: x.cooldown_until)
                wait = max(0.05, soonest.cooldown_until - now)
            time.sleep(min(wait, 1.0))

    def _mark_cooldown(self, slot: _KeySlot, extra: float = 0.0) -> None:
        with slot.lock:
            slot.cooldown_until = time.time() + self._cooldown + extra
            slot.failed += 1

    # ---- 单次 HTTP 调用 ----
    def _call(self, slot: _KeySlot, system: str, user: str, temperature: float) -> str:
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
        }
        if "json" in (system + user).lower():
            payload["response_format"] = {"type": "json_object"}
        req = urllib.request.Request(
            self._base_url + "/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {slot.key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        with slot.lock:
            slot.used += 1
        return data["choices"][0]["message"]["content"] or ""

    # ---- 对外接口 ----
    def complete(self, system: str, user: str) -> str:
        return self._complete_with_temp(system, user, temperature=0.6)

    def complete_at(self, system: str, user: str, temperature: float | None = None) -> str:
        t = 0.6 if temperature is None else float(temperature)
        return self._complete_with_temp(system, user, temperature=t)

    def _complete_with_temp(self, system: str, user: str, temperature: float) -> str:
        last_err: Exception | None = None
        for attempt in range(self._max_retries):
            slot = self._next_slot()
            slot.semaphore.acquire()
            try:
                return self._call(slot, system, user, temperature)
            except urllib.error.HTTPError as exc:
                body = ""
                try:
                    body = exc.read().decode("utf-8", errors="replace")[:300]
                except Exception:
                    pass
                last_err = RuntimeError(f"HTTP {exc.code}: {body}")
                if exc.code in (429, 503):
                    self._mark_cooldown(slot, extra=4.0)
                    continue
                # 4xx (非限流) 一般是配置/请求问题，没必要换 key 重试
                if 400 <= exc.code < 500 and exc.code not in (408, 425, 429):
                    raise last_err from exc
                self._mark_cooldown(slot)
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                last_err = exc
                self._mark_cooldown(slot)
            except Exception as exc:
                # 瞬时网络/解码错误：http.client.IncompleteRead, json.JSONDecodeError,
                # socket.timeout, BrokenPipeError 等。换 key 重试。
                last_err = exc
                self._mark_cooldown(slot)
            finally:
                slot.semaphore.release()
        raise last_err if last_err else RuntimeError("KeyPoolClient: exhausted retries")

    def stats(self) -> list[dict]:
        return [
            {
                "key_tail": s.key[-6:],
                "used": s.used,
                "failed": s.failed,
                "cooldown_remaining": max(0.0, s.cooldown_until - time.time()),
            }
            for s in self._slots
        ]
