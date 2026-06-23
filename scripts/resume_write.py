"""恢复脚本：项目已蒸馏锁定，只跑写 N 章。"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from server import config_store
from server.projects import ProjectManager, set_config_provider
from novel_engine.llm.keypool import KeyPoolClient
from novel_engine.llm.logging_wrapper import LoggingLLMClient


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--num-chapters", type=int, default=3)
    ap.add_argument("--target-words", type=int, default=2400)
    ap.add_argument("--outdir", default="outputs/full_distill_run")
    args = ap.parse_args()

    set_config_provider(config_store.load_config)
    cfg = config_store.load_config()
    keys = config_store.list_keys(cfg)
    llm = KeyPoolClient(keys=keys, model=cfg.get("modelName") or "deepseek-chat",
                       base_url=cfg.get("baseUrl") or "https://api.deepseek.com",
                       per_key_concurrency=4)

    manager = ProjectManager()
    project = manager.get(args.project)
    if project is None:
        raise SystemExit(f"project not found: {args.project}")
    project.ensure_writing_repo()
    # 透明日志：包一层 LoggingLLMClient，所有 LLM 调用落 llm_logs 表
    project._gen_llm = LoggingLLMClient(llm, project.repo.conn, caller="continuation_writing")

    outdir = (ROOT / args.outdir).resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    written = []
    accepted_count_before = len(project.repo.list_accepted_chapters())
    for i in range(args.num_chapters):
        print(f"[ch {i+1}/{args.num_chapters}] starting", flush=True)
        # 包外重试 1 次（防止偶发 HTTP IncompleteRead 把整章扔掉）
        last_err = None
        for attempt in range(2):
            try:
                draft = project.create_chapter_draft({
                    "guidance": "", "targetWords": args.target_words,
                    "outlineOnly": False, "mode": "manual",
                })
                last_err = None
                break
            except Exception as e:
                last_err = e
                print(f"  attempt {attempt+1} failed: {e!r} -- retry", flush=True)
        if last_err:
            print(f"  ch {i+1} hard failed, skipping: {last_err!r}", flush=True)
            continue
        if draft.get("id"):
            project.accept_chapter_draft(int(draft["id"]))
        written.append(draft)
        cn = draft.get("chapterNo")
        prose = draft.get("prose", "")
        print(f"  -> ch{cn}: {draft.get('title','')} | {len(prose)} chars", flush=True)
        path = outdir / f"chapter_{cn:02d}.md" if isinstance(cn, int) else outdir / f"chapter_x_{i}.md"
        path.write_text(f"# 第{cn}章 {draft.get('title','')}\n\n{prose}", encoding="utf-8")

    manager.persist()
    (outdir / "resume_summary.json").write_text(json.dumps({
        "projectId": project.id,
        "writtenChaptersThisRun": len(written),
        "totalAccepted": len(project.repo.list_accepted_chapters()),
        "chapters": [{"no": d.get("chapterNo"), "title": d.get("title"), "len": len(d.get("prose",""))} for d in written],
        "keypoolStats": llm.stats(),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[done] wrote {len(written)} new chapters; total accepted={len(project.repo.list_accepted_chapters())}", flush=True)


if __name__ == "__main__":
    main()
