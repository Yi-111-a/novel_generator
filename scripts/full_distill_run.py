"""端到端验证：完全蒸馏 + 写 3 章。

只用 龙族1.txt 作原作（防泄露二卷+），new_series_book 模式，
江南随笔做经历层（可选，未提供路径则关闭）。
"""
from __future__ import annotations
import argparse, json, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from server import config_store
from server.projects import ProjectManager, set_config_provider
from novel_engine.llm.keypool import KeyPoolClient


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="龙族1.txt")
    ap.add_argument("--experience", default="龙与少年游：江南随笔精选+(江南)+(Z-Library).epub",
                    help="经历层素材；不存在则自动跳过")
    ap.add_argument("--title", default="龙族·新章（完全蒸馏）")
    ap.add_argument("--mode", default="new_series_book")
    ap.add_argument("--num-chapters", type=int, default=3, help="写几章验证")
    ap.add_argument("--outline-chapters", type=int, default=6, help="C3 大纲规划几章")
    ap.add_argument("--target-words", type=int, default=2400)
    ap.add_argument("--distill-workers", type=int, default=8)
    ap.add_argument("--outdir", default="outputs/full_distill_run")
    args = ap.parse_args()

    set_config_provider(config_store.load_config)
    cfg = config_store.load_config()
    keys = config_store.list_keys(cfg)
    if not keys:
        raise SystemExit("无 LLM key，检查 server/.data/config.json")

    src = (ROOT / args.source).resolve()
    if not src.exists():
        raise SystemExit(f"找不到原作：{src}")
    exp = (ROOT / args.experience).resolve()
    exp_ok = exp.exists()

    llm = KeyPoolClient(
        keys=keys, model=cfg.get("modelName") or "deepseek-chat",
        base_url=cfg.get("baseUrl") or "https://api.deepseek.com",
        per_key_concurrency=4,
    )

    manager = ProjectManager()
    print(f"[stage] create project: {args.title}", flush=True)
    project = manager.create(args.title, project_type="continuation")
    project.ensure_writing_repo()
    project._gen_llm = llm

    print(f"[stage] import source: {src.name} (cleaning paratext...)", flush=True)
    project.import_source_text({"filePath": str(src), "filename": src.name})
    chapters = project.repo.list_source_chapters()
    print(f"[stage] -> {len(chapters)} 章导入：", flush=True)
    for c in chapters:
        print(f"  · 第{c.chapter_no}章 [{c.title[:20]}] {c.word_count}字", flush=True)

    project.set_continuation_settings({
        "writeMode": args.mode,
        "sourceBookTitle": "原作（仅一卷）",
        "currentBookTitle": args.title,
        "bookIndex": 1,
        "timePosition": "未定",
        "protagonistStrategy": "沿用原作世界规则，主角与起点可调",
        "inheritUnresolvedThreads": True,
        "continuationHint": ("续写一部都市奇幻长篇。主角是看似平庸的大学生，"
                             "在表面安稳的学院生活与一个秘密世界之间被夹住。"
                             "从日常缝隙切入，让危机慢慢逼近；不直接说设定，让场景与对话推进。"),
        "experienceLayerEnabled": exp_ok,
        "experienceLayerMode": "essay_plus_text" if exp_ok else "off",
        "experienceSourcePath": str(exp) if exp_ok else "",
        "experienceStyleLevel": "max" if exp_ok else "none",
    })

    print(f"[stage] start full distill (workers={args.distill_workers}, outline_chapters={args.outline_chapters})", flush=True)
    t0 = time.time()
    result = project.start_continuation_distill({
        "sampleMode": "full",
        "graphDetail": "medium",
        "styleSampleSegments": 8,
        "generateAws": True,
        "enableStyleSkill": True,
        "extractUnresolvedThreads": True,
        "extractCharacterEndings": True,
        "extractFactionState": True,
        "extractExpandableRegions": True,
        "distillWorkers": args.distill_workers,
        "outlineChapters": args.outline_chapters,
    })
    print(f"[stage] distill done in {time.time()-t0:.1f}s -> {result}", flush=True)

    # 检查蒸馏产物
    print("[stats] 蒸馏产物:", flush=True)
    print(f"  events={len(project.repo.list_source_events())}", flush=True)
    print(f"  snapshots={len(project.repo.list_character_snapshots())}", flush=True)
    print(f"  codex={len(project.repo.list_codex())}", flush=True)
    print(f"  arcs={len(project.repo.list_story_arcs())}", flush=True)
    print(f"  foreshadows={len(project.repo.list_foreshadows())}", flush=True)
    print(f"    · open={len(project.repo.list_foreshadows('open'))}", flush=True)
    print(f"    · paid={len(project.repo.list_foreshadows('paid'))}", flush=True)
    print(f"  characters={len(project.repo.list_personas())}", flush=True)
    print(f"  locations={len(project.repo.list_locations())}", flush=True)
    print(f"  factions={len(project.repo.list_factions())}", flush=True)
    print(f"  graph_edges={len(project.repo.list_edges())}", flush=True)
    print(f"  threads={len(project.repo.list_threads())}", flush=True)
    print(f"  chapter_plans (C3 大纲)={len(project.repo.list_chapter_plans())}", flush=True)

    print("[stage] lock continuation", flush=True)
    project.lock_continuation()

    outdir = (ROOT / args.outdir).resolve()
    outdir.mkdir(parents=True, exist_ok=True)

    print(f"[stage] write {args.num_chapters} chapters", flush=True)
    written = []
    for i in range(args.num_chapters):
        print(f"  -- ch {i+1}/{args.num_chapters} --", flush=True)
        draft = project.create_chapter_draft({
            "guidance": "",  # 大纲已经规划好，无需额外 guidance
            "targetWords": args.target_words,
            "outlineOnly": False, "mode": "manual",
        })
        if draft.get("id"):
            project.accept_chapter_draft(int(draft["id"]))
        written.append(draft)
        print(f"     wrote {len(draft.get('prose',''))} chars: {draft.get('title','')}", flush=True)

    manager.persist()

    # 写每章 markdown
    for d in written:
        cn = d.get("chapterNo") or "?"
        (outdir / f"chapter_{cn:02d}.md" if isinstance(cn, int) else outdir / f"chapter_x.md").write_text(
            f"# 第{cn}章 {d.get('title','')}\n\n{d.get('prose','')}", encoding="utf-8")
    (outdir / "summary.json").write_text(json.dumps({
        "projectId": project.id, "title": project.title,
        "chaptersImported": len(chapters),
        "experienceUsed": exp_ok,
        "writtenChapters": [{"no": d.get("chapterNo"), "title": d.get("title"), "len": len(d.get("prose",""))} for d in written],
        "keypoolStats": llm.stats(),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[done] projectId={project.id} -> {outdir}", flush=True)


if __name__ == "__main__":
    import traceback
    try:
        main()
    except SystemExit:
        raise
    except BaseException:
        print("\n[FATAL]")
        traceback.print_exc()
        raise
