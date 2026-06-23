from __future__ import annotations
import json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from server import config_store
from server.projects import ProjectManager, set_config_provider
from novel_engine.llm.keypool import KeyPoolClient

set_config_provider(config_store.load_config)
cfg = config_store.load_config()
keys = config_store.list_keys(cfg)
if not keys:
    raise SystemExit("no keys")

llm = KeyPoolClient(
    keys=keys,
    model=cfg.get("modelName") or "deepseek-chat",
    base_url=cfg.get("baseUrl") or "https://api.deepseek.com",
    per_key_concurrency=4,
)

manager = ProjectManager()
project = manager.get("proj_fd017e4f")
if project is None:
    raise SystemExit("project not found")
project.ensure_writing_repo()
project._gen_llm = llm

guidance = (
    "承接原书世界与人物，不要写成番外，也不要把设定总结当正文。"
    "开场从一个日常表面下的细小裂口切入，让危机慢慢逼近主角；"
    "中段以场景和对话推进，不直接解释全部设定；"
    "结尾留下一个非常具体、可继续追索的悬念物。"
)
print("[stage] create_chapter_draft", flush=True)
draft = project.create_chapter_draft(
    {"guidance": guidance, "targetWords": 2600, "outlineOnly": False, "mode": "manual"}
)
print(f"[stage] keypool stats: {llm.stats()}", flush=True)

accepted = None
if draft.get("id"):
    accepted = project.accept_chapter_draft(int(draft["id"]))
manager.persist()

outdir = ROOT / "outputs" / "longzu1_new_series_run"
outdir.mkdir(parents=True, exist_ok=True)
(outdir / "draft.json").write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
if draft.get("prose"):
    (outdir / "chapter_01.md").write_text(
        f"# {draft.get('title') or '新章'}\n\n{draft['prose']}", encoding="utf-8"
    )
print(json.dumps({"ok": True, "draftId": draft.get("id"), "accepted": accepted is not None,
                  "wordCount": len(draft.get("prose","") )}, ensure_ascii=False))
