# -*- coding: utf-8 -*-
"""只重渲正文（不改种子、不重新模拟）：把已写章节的 prose 删掉，用第一批修复后的
narrator 重新渲染一遍，便于和旧稿对比。后端须先停掉（独占 DB）。

做的事：① 按新 _ROLE_WORDS 下调每章 target_words；② 清空 scenes + reader_knowledge；
③ Editor.render_incremental 从头重渲所有已有事件（同剧情、新文笔）。
"""
import io, sys, json, time
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT))
if __name__ == "__main__":
    try: sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    except Exception: pass

from novel_engine.config import LLMConfig
from novel_engine.llm import build_client
from novel_engine import db
from novel_engine.repository import Repository
from novel_engine.narration.editor import Editor
from novel_engine.planner import _ROLE_WORDS

PID = sys.argv[1] if len(sys.argv) > 1 else "proj_e80d985e"
DBP = ROOT / "server" / ".data" / "projects" / f"{PID}.db"
CFG = json.loads((ROOT / "server" / ".data" / "config.json").read_text(encoding="utf-8"))
LLM = build_client(LLMConfig(provider="deepseek", model=CFG["modelName"],
                             base_url=CFG["baseUrl"], api_key=CFG["llmApiKey"]))


def main():
    t0 = time.time()
    con = db.connect(str(DBP))
    repo = Repository(con)
    theme = ""  # 取不到主题不影响渲染
    # ① 下调 target_words（让"篇幅瘦身"在重渲生效）
    chs = sorted(repo.list_chapter_plans(), key=lambda c: c.sequence_order)
    for c in chs:
        new_w = _ROLE_WORDS.get(c.role, 1500)
        if c.target_words != new_w:
            c.target_words = new_w
            repo.upsert_chapter_plan(c)
    # ② 清空已成稿正文 + 读者账本（重渲会重新逐场提交揭示）
    con.execute("DELETE FROM scenes")
    con.execute("DELETE FROM reader_knowledge")
    con.commit()
    print(f"[准备] {PID}：{len(chs)} 章，已下调字数、清空旧稿")
    # ③ 从头重渲所有已有事件（同剧情、新文笔）
    ed = Editor(repo, llm=LLM, theme=theme, threshold=0.4, reveal_budget=1, max_rewrites=2)
    ed.render_incremental(set(), max_new=40)
    con.commit()
    scenes = repo.list_scenes()
    name_of = {e.entity_id: e.name for e in repo.list_entities()}
    out = ROOT / "scripts" / "out_rerender.txt"
    with out.open("w", encoding="utf-8") as f:
        for s in scenes:
            f.write(f"\n--- 场{s.discourse_order}  POV={name_of.get(s.pov,s.pov)}  {len(s.prose_text)}字 ---\n{s.prose_text}\n")
    lens = [len(s.prose_text) for s in scenes]
    print(f"[完成] 重渲 {len(scenes)} 场，单场字数 min/avg/max = "
          f"{min(lens) if lens else 0}/{sum(lens)//len(lens) if lens else 0}/{max(lens) if lens else 0}")
    print(f"[正文] → {out}")
    print(f"用时 {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
