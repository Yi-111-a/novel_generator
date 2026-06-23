"""清掉旧扁平大纲那 3 章已写产物，重排剩余 68 章从 1 开始。"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from server import config_store
from server.projects import ProjectManager, set_config_provider

PROJECT_ID = "proj_363eb19a"
set_config_provider(config_store.load_config)
manager = ProjectManager()
project = manager.get(PROJECT_ID)
project.ensure_writing_repo()
repo = project.repo
c = repo.conn

print("[before]")
print(f"  accepted={len(repo.list_accepted_chapters())} drafts={len(repo.list_chapter_drafts())} plans={len(repo.list_chapter_plans())}")

# 1) 删除已写 3 章及它们的 draft（accepted_chapters → chapter_drafts）
c.execute("DELETE FROM accepted_chapters")
c.execute("DELETE FROM chapter_drafts")

# 2) 删除旧扁平 plan（cont_outline_* / cont_flat_*）
c.execute("DELETE FROM chapter_plans WHERE chapter_id LIKE 'cont_outline_%' OR chapter_id LIKE 'cont_flat_%'")

# 3) 把多级 plan(cont_ch_*) 按当前 sequence_order 升序重排成 1..N
rows = c.execute("SELECT chapter_id, sequence_order FROM chapter_plans "
                 "WHERE chapter_id LIKE 'cont_ch_%' ORDER BY sequence_order").fetchall()
print(f"  renumbering {len(rows)} multilevel plans → 1..{len(rows)}")

# 先给所有需要重排的临时偏移到 100000+ 避免主键碰撞（chapter_plans 主键是 chapter_id 不是 seq，但仍要稳）
for i, (cid, _) in enumerate(rows, 1):
    c.execute("UPDATE chapter_plans SET sequence_order=?, chapter_id=? WHERE chapter_id=?",
              (i, f"cont_ch_{i}", cid))
c.commit()

# 4) 重置续写 meta 的 chapter_start_no=1 + latest_source_chapter_no（new_series_book 不依赖此）
meta = repo.get_continuation_meta()
meta.chapter_start_no = 1
repo.set_continuation_meta(meta)

print("[after]")
print(f"  accepted={len(repo.list_accepted_chapters())} drafts={len(repo.list_chapter_drafts())} plans={len(repo.list_chapter_plans())}")
print(f"  first 5 chapters: {[(c.sequence_order, c.title) for c in repo.list_chapter_plans()[:5]]}")
manager.persist()
print("done")
