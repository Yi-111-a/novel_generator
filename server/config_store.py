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
    "baseUrl": "https://api.deepseek.com",
    "modelName": "deepseek-v4-flash",
    "memoryKey": "",
    "autoResume": False,  # 重启恢复写作中项目时是否自动继续播放
}


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
