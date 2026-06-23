"""全局 ApiConfig 持久化（JSON 文件）。Key 只在服务端保管。"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DATA_DIR = Path(os.getenv("NOVEL_ENGINE_DATA", Path(__file__).resolve().parent / ".data"))
CONFIG_PATH = DATA_DIR / "config.json"

DEFAULT: dict[str, Any] = {
    "llmApiKey": "",
    "llmApiKeys": [],  # 可选 key 池；存在时 KeyPoolClient 走 round-robin 并发
    "baseUrl": "https://api.deepseek.com",
    "modelName": "deepseek-v4-flash",
    "memoryKey": "",
    "autoResume": False,  # 重启恢复写作中项目时是否自动继续播放
}


def list_keys(cfg: dict[str, Any] | None = None) -> list[str]:
    """统一取 key 池：优先 llmApiKeys，没填则回落到单 key llmApiKey。"""
    cfg = cfg if cfg is not None else load_config()
    keys = [k for k in (cfg.get("llmApiKeys") or []) if isinstance(k, str) and k.strip()]
    if not keys:
        single = (cfg.get("llmApiKey") or "").strip()
        if single:
            keys = [single]
    return keys


def load_config() -> dict[str, Any]:
    if CONFIG_PATH.exists():
        try:
            return {**DEFAULT, **json.loads(CONFIG_PATH.read_text(encoding="utf-8"))}
        except Exception:
            pass
    return dict(DEFAULT)


def save_config(cfg: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    merged = {**DEFAULT, **cfg}
    CONFIG_PATH.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
