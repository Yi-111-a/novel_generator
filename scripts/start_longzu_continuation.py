from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from server import config_store
from server.projects import ProjectManager, set_config_provider
from scripts.distill_longzu_project import (
    build_safe_sheet,
    extract_epub_text,
    project_llm_from_server_config,
    punct_stats,
    sentence_distribution,
)
from novel_engine.llm.keypool import KeyPoolClient
from novel_engine.style.author_sheet import derive_style_profile
from novel_engine.style.sheet_distiller import distill_author_sheet
from novel_engine.style_skill import compute_style_metrics


# 净化后的续写定位：只描述续写场景方向 / 体裁，不命名任何作者或作品风格元素。
# 之前版本含"路明非/燃中带丧/卡塞尔/衰小孩/双声道/命运感短句/爱而不得"——那是 prompt 作弊，
# 等于直接告诉模型"扮江南写龙族"。要验证文风蒸馏真的有效，hint 必须先去掉这些命名。
DEFAULT_HINT = (
    "这是一部都市奇幻长篇的续写。主角是看似平庸的大学生，"
    "在表面安稳的学院生活与一个秘密世界之间被夹住。"
    "第一章要让平静的日常忽然裂开一道缝，让一个未解的旧事物的余波重新出现在主角面前。"
    "节奏由场景与对话推动，避免直接的设定说明，避免英雄宣言。"
)


def _prepare_source_text(raw_text: str) -> str:
    markers = [
        "在你最孤单最无望的时候",
        "When you feel most lonely and desperate",
        "序章",
        "开篇",
    ]
    starts = [pos for marker in markers if (pos := raw_text.find(marker)) >= 0]
    text = raw_text[min(starts):] if starts else raw_text
    return text.strip()


def _safe_rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _build_keypool_llm(cfg: dict) -> KeyPoolClient | None:
    """优先用 KeyPool（多 key 并发）；只有单 key 时也走 KeyPool（n=1）以统一路径。"""
    keys = config_store.list_keys(cfg)
    if not keys:
        return None
    return KeyPoolClient(
        keys=keys,
        model=cfg.get("modelName") or "deepseek-chat",
        base_url=cfg.get("baseUrl") or "https://api.deepseek.com",
        per_key_concurrency=4,
    )


def build_style_profile(text_path: Path, llm: KeyPoolClient, max_workers: int):
    """读 text → 真 AWS 蒸馏（并发）→ derive_style_profile → 返回 (text, sheet, profile)。"""
    text = text_path.read_text(encoding="utf-8")
    metrics = compute_style_metrics(text)
    stats = punct_stats(text)
    dist = sentence_distribution(text)

    def _on_progress(i: int, n: int) -> None:
        if i == 0 or i == n or i % max(1, n // 10) == 0:
            print(f"  [distill] segment {i}/{n}", flush=True)

    sheet = distill_author_sheet(
        llm,
        text,
        name="龙族·火之晨曦 文风画像",
        genre="都市奇幻青春",
        on_progress=_on_progress,
        max_workers=max_workers,
    )
    profile = derive_style_profile(sheet, text[:4000], llm=llm)
    profile.name = "龙族·火之晨曦 文风画像"
    profile.source = f"龙族1.txt AWS 真蒸馏（max_workers={max_workers}）"
    profile.metrics = metrics
    return text, sheet, profile


def build_safe_style_profile(text_path: Path):
    """无 key 兜底：用确定性指标。质量低，仅用于结构验证。"""
    text = text_path.read_text(encoding="utf-8")
    metrics = compute_style_metrics(text)
    stats = punct_stats(text)
    dist = sentence_distribution(text)
    sheet = build_safe_sheet(metrics, stats, dist)
    profile = derive_style_profile(sheet, text[:4000], llm=None)
    profile.name = "龙族·火之晨曦 安全画像（确定性 fallback）"
    profile.source = "确定性指标，未做 LLM 对照蒸馏"
    profile.metrics = metrics
    return text, sheet, profile


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--epub", default="龙族全套+共七册.epub")
    parser.add_argument("--experience-epub", default="龙与少年游：江南随笔精选+(江南)+(Z-Library).epub")
    parser.add_argument("--text-file", default="龙族1.txt",
                        help="蒸馏用的纯文本文件（默认龙族1.txt）。导入项目时仍用 --epub 指定的 epub")
    parser.add_argument("--title", default="龙族 VIII")
    parser.add_argument("--mode", choices=["continue_current_book", "new_series_book"], default="continue_current_book")
    parser.add_argument("--target-words", type=int, default=2600)
    parser.add_argument("--accept", action="store_true")
    parser.add_argument("--max-workers", type=int, default=56,
                        help="AWS 蒸馏并发度。14 key × 4 并发 = 56；单 key 用 1")
    parser.add_argument("--no-distill", action="store_true",
                        help="跳过真 AWS 蒸馏，用确定性 fallback（调试用）")
    parser.add_argument("--outdir", default="outputs/longzu_continuation_run")
    args = parser.parse_args()

    set_config_provider(config_store.load_config)
    cfg = config_store.load_config()
    keys = config_store.list_keys(cfg)
    if not keys and not args.no_distill:
        raise SystemExit("server/.data/config.json 中没有任何 LLM key。")

    text_path = (ROOT / args.text_file).resolve() if args.text_file else None
    if text_path is None or not text_path.exists():
        raise SystemExit(f"text file not found: {text_path}")

    epub_path = Path(args.epub).resolve() if args.epub else next(ROOT.glob("*.epub")).resolve()
    experience_path = (ROOT / args.experience_epub).resolve() if args.experience_epub else None

    # --- 真 AWS 蒸馏（并发）---
    if args.no_distill:
        print("[mode] 安全 fallback：使用确定性指标 sheet", flush=True)
        text, sheet, profile = build_safe_style_profile(text_path)
        llm = None
    else:
        max_workers = max(1, args.max_workers)
        print(f"[mode] 真 AWS 蒸馏 / keys={len(keys)} / max_workers={max_workers}", flush=True)
        llm = _build_keypool_llm(cfg)
        if llm is None:
            raise SystemExit("KeyPool 构造失败。")
        text, sheet, profile = build_style_profile(text_path, llm, max_workers)
        print(f"[stage] distill done. sheet: plot={len(sheet.plot)}, creativity={len(sheet.creativity)}, "
              f"development={len(sheet.development)}, language={len(sheet.language)}", flush=True)
        print(f"[stats] {llm.stats()}", flush=True)

    print("[stage] create project", flush=True)
    manager = ProjectManager()
    project = manager.create(args.title, project_type="continuation")
    project.ensure_writing_repo()
    assert project.repo is not None

    print("[stage] import source", flush=True)
    project.import_source_text({"filePath": str(epub_path), "filename": epub_path.name})
    sheet_id = project.repo.save_author_sheet(sheet)
    project.repo.set_style_skill(profile)

    continuation_hint = DEFAULT_HINT
    if args.mode == "continue_current_book":
        continuation_hint += " 续点：承接原书末尾未完结的局面，第一章从新的紧迫消息切入。"
    else:
        continuation_hint += " 续点：同宇宙新主线开篇，主角是新登场的大学生，但保留原作的世界规则。"

    project.set_continuation_settings(
        {
            "writeMode": args.mode,
            "sourceBookTitle": "龙族（本地全集语料）",
            "currentBookTitle": args.title,
            "bookIndex": 1,
            "timePosition": "未定",
            "protagonistStrategy": "大学生主角，旁观式介入超自然事件",
            "inheritUnresolvedThreads": True,
            "continuationHint": continuation_hint,
            "experienceLayerEnabled": bool(experience_path and experience_path.exists()),
            "experienceLayerMode": "essay_plus_text",
            "experienceSourcePath": str(experience_path) if experience_path and experience_path.exists() else "",
            "experienceStyleLevel": "max",
        }
    )
    print("[stage] start_continuation_distill (phase walker)", flush=True)
    project.start_continuation_distill(
        {
            "sampleMode": "full",
            "graphDetail": "medium",
            "styleSampleSegments": 8,
            "generateAws": True,
            "enableStyleSkill": True,
            "extractUnresolvedThreads": True,
            "extractCharacterEndings": True,
            "extractFactionState": True,
            "extractExpandableRegions": True,
        }
    )
    print("[stage] lock_continuation", flush=True)
    lock_payload = project.lock_continuation()

    # 起稿仍走 KeyPool（如果有），否则回落到老的单 key client
    if llm is None:
        llm = project_llm_from_server_config()
    if llm is None:
        raise SystemExit("无法从 server 配置构造 LLM 客户端。")
    project._gen_llm = llm
    print("[stage] create_chapter_draft", flush=True)
    guidance = (
        "承接原书世界与人物，不要写成番外，也不要把设定总结当正文。"
        "开场从一个日常表面下的细小裂口切入，让危机慢慢逼近主角；"
        "中段以场景和对话推进，不直接解释全部设定；"
        "结尾留下一个非常具体、可继续追索的悬念物。"
    )
    draft = project.create_chapter_draft(
        {
            "guidance": guidance,
            "targetWords": args.target_words,
            "outlineOnly": False,
            "mode": "manual",
        }
    )
    accepted = None
    if args.accept and draft.get("id"):
        accepted = project.accept_chapter_draft(int(draft["id"]))

    manager.persist()
    diagnostics = project.style_diagnostics()
    story_bible = project.get_story_bible()
    continuation_settings = project.get_continuation_settings()

    outdir = (ROOT / args.outdir).resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "draft.json").write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
    (outdir / "snapshot.json").write_text(json.dumps(lock_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (outdir / "style_diagnostics.json").write_text(json.dumps(diagnostics, ensure_ascii=False, indent=2), encoding="utf-8")
    (outdir / "story_bible.json").write_text(json.dumps(story_bible, ensure_ascii=False, indent=2), encoding="utf-8")
    (outdir / "continuation_settings.json").write_text(json.dumps(continuation_settings, ensure_ascii=False, indent=2), encoding="utf-8")
    (outdir / "sheet.json").write_text(
        json.dumps(
            {
                "name": sheet.name,
                "source_genre": sheet.source_genre,
                "n_segments": sheet.n_segments,
                "plot": [c.__dict__ for c in sheet.plot],
                "creativity": [c.__dict__ for c in sheet.creativity],
                "development": [c.__dict__ for c in sheet.development],
                "language": [c.__dict__ for c in sheet.language],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (outdir / "meta.json").write_text(
        json.dumps(
            {
                "projectId": project.id,
                "title": project.title,
                "epub": _safe_rel(epub_path),
                "experienceEpub": _safe_rel(experience_path) if experience_path and experience_path.exists() else "",
                "textFile": _safe_rel(text_path),
                "sheetId": sheet_id,
                "accepted": accepted,
                "mode": args.mode,
                "noDistill": args.no_distill,
                "maxWorkers": args.max_workers,
                "lifeModelPresent": bool((diagnostics.get("corpus", {}) or {}).get("lifeModel")),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    if draft.get("prose"):
        (outdir / "chapter_01.md").write_text(
            f"# {draft.get('title') or '新章'}\n\n{draft['prose']}",
            encoding="utf-8",
        )

    print(
        json.dumps(
            {
                "ok": True,
                "projectId": project.id,
                "title": project.title,
                "outdir": str(outdir),
                "accepted": accepted is not None,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    import traceback
    try:
        main()
    except SystemExit:
        raise
    except BaseException as _exc:
        print("\n[FATAL] uncaught exception in main():", flush=True)
        traceback.print_exc()
        raise
