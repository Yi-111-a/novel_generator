"""把 proj_363eb19a 的 324 条 pending 伏笔分批配对（不重蒸）。"""
from __future__ import annotations
import sys, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from server import config_store
from server.projects import ProjectManager, set_config_provider
from novel_engine.llm.keypool import KeyPoolClient
from novel_engine.continuation.full_book_aggregate import pair_foreshadows

PROJECT_ID = "proj_363eb19a"

set_config_provider(config_store.load_config)
cfg = config_store.load_config()
keys = config_store.list_keys(cfg)
llm = KeyPoolClient(
    keys=keys, model=cfg.get("modelName") or "deepseek-chat",
    base_url=cfg.get("baseUrl") or "https://api.deepseek.com",
    per_key_concurrency=4,
)

manager = ProjectManager()
project = manager.get(PROJECT_ID)
project.ensure_writing_repo()

before = {r: len(project.repo.list_foreshadows(r)) for r in ("pending", "paid", "open", "discarded")}
print(f"[before] {before}", flush=True)

result = pair_foreshadows(project.repo, llm, batch_size=40, max_workers=6, only_pending=True)
print(f"[result] {result}", flush=True)

after = {r: len(project.repo.list_foreshadows(r)) for r in ("pending", "paid", "open", "discarded")}
print(f"[after] {after}", flush=True)
print(f"[keys] {llm.stats()}", flush=True)
