"""清掉 proj_363eb19a 已有的 chapter_plans/parts/arcs，重跑 C3 多级大纲。

蒸馏数据全保留（events/snapshots/codex/foreshadow/personas/locations/factions/threads）。
"""
from __future__ import annotations
import sys, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from server import config_store
from server.projects import ProjectManager, set_config_provider
from novel_engine.llm.keypool import KeyPoolClient
from novel_engine.continuation import build_continuation_outline

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
repo = project.repo

# 清掉旧的扁平大纲（之前的 6 章 cont_outline_*）以及任何 parts/arcs
# 注意保留已 accepted 的章节（前面写了 3 章）
print('[before]', flush=True)
print(f'  parts={len(repo.list_parts())} arcs={len(repo.list_arcs())} chapter_plans={len(repo.list_chapter_plans())} accepted={len(repo.list_accepted_chapters())}', flush=True)

# 只删未写完的 chapter_plans（保留 status=done 的，因 accepted 章节绑定它们）
accepted_seqs = {a.chapter_no for a in repo.list_accepted_chapters()}
to_delete = [c.chapter_id for c in repo.list_chapter_plans()
             if c.sequence_order not in accepted_seqs]
print(f'[clean] deleting {len(to_delete)} pending chapter_plans', flush=True)
for cid in to_delete:
    repo.conn.execute("DELETE FROM chapter_plans WHERE chapter_id=?", (cid,))
# 清 parts/arcs（无外键引用）
repo.conn.execute("DELETE FROM parts")
repo.conn.execute("DELETE FROM arcs")
repo.conn.commit()

print('[stage] build_continuation_outline (multilevel)', flush=True)
result = build_continuation_outline(
    repo, llm, n_parts=4, arcs_per_part=3, chapters_per_arc=5,
)
print(f'[result] {json.dumps(result, ensure_ascii=False)}', flush=True)

print('[after]', flush=True)
print(f'  parts={len(repo.list_parts())} arcs={len(repo.list_arcs())} chapter_plans={len(repo.list_chapter_plans())}', flush=True)
for p in repo.list_parts():
    print(f'  Part {p.sequence_order}: {p.title} ({p.region})', flush=True)
    for a in [x for x in repo.list_arcs() if x.part_id == p.part_id]:
        chs = [c for c in repo.list_chapter_plans() if c.arc_id == a.arc_id]
        print(f'    Arc {a.sequence_order}: {a.title} ({len(chs)} ch)', flush=True)
print(f'[keys] {llm.stats()}', flush=True)
