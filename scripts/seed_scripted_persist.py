# -*- coding: utf-8 -*-
"""把 scripted 模式写的《孤岛》落成**真实服务器项目**，使前端「阅读」能读到。

流程：建真实项目 DB（server/.data/projects/<pid>.db）→ 同 server 锁定流程 →
scripted Director 写 N 章 → 注册进 projects.json（status=writing）。
跑完**重启后端**即可在前端看到「孤岛·scripted」。
跑法：PYTHONUTF8=1 ACCEPT_CHAPTERS=2 python scripts/seed_scripted_persist.py
"""
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from novel_engine.config import LLMConfig
from novel_engine.llm import build_client
from novel_engine.director import Director
from novel_engine.planner import Planner
from novel_engine.worldsmith import WorldSmith
from novel_engine.tone import build_tone_profile
from novel_engine.casting import (cast_named_characters, ensure_cards_for_personas,
                                  infer_and_store_genders, lock_motif_canon,
                                  lock_hidden_identities, lock_aliases)
from novel_engine.narration.scene_writer import SceneWriter
from novel_engine.narration.fact_extractor import FactExtractor
from novel_engine.narration.controller import Controller
from server import seedbuilder
from scripts.try_generate3 import DRAFT

DATA_DIR = ROOT / "server" / ".data"
PROJECTS_DIR = DATA_DIR / "projects"
INDEX_PATH = DATA_DIR / "projects.json"
CFG = json.loads((DATA_DIR / "config.json").read_text(encoding="utf-8"))
_RAW_LLM = build_client(LLMConfig(provider="deepseek", model=CFG["modelName"],
                                   base_url=CFG["baseUrl"], api_key=CFG["llmApiKey"]))
N = int(os.environ.get("ACCEPT_CHAPTERS", "2"))


def main():
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    pid = f"proj_{uuid.uuid4().hex[:8]}"
    db_path = str(PROJECTS_DIR / f"{pid}.db")
    wb = DRAFT["worldBible"]
    t0 = time.time()

    repo = seedbuilder.build_repo_from_draft(DRAFT, db_path=db_path)
    # 包装 LLM 以记录全量对话日志
    from novel_engine.llm.logging_wrapper import LoggingLLMClient
    LLM = LoggingLLMClient(_RAW_LLM, repo.conn, caller="seed_scripted")
    print(f"[1] 项目 {pid} DB 落地 → {db_path}")
    build_tone_profile(repo, llm=LLM, genre=wb["genre"], theme=wb["theme"],
                       setting_hint=wb["settingCore"][:200])
    ensure_cards_for_personas(repo)
    cast_named_characters(repo, "\n".join([wb["settingCore"], wb["geography"], wb["culture"]]),
                          wb["protagonistWant"], llm=LLM)
    infer_and_store_genders(repo, llm=LLM)
    lock_motif_canon(repo, llm=LLM)
    lock_aliases(repo, wb.get('protagonistWant',''), wb.get('settingCore',''), wb.get('theme',''))
    lock_hidden_identities(repo, llm=LLM)
    from novel_engine.worldbible import (build_factions, build_geography,
                                          build_world_skill, lock_canonical_geography)
    build_world_skill(repo, llm=LLM, theme=wb.get('theme',''))  # W1 World Skill（两级+校验回路）
    # W0 设定保真闸门：固化 canon 地名（镜像 server.projects 锁定流程，须在 build_master 前）
    canon = lock_canonical_geography(repo, llm=LLM)
    print(f"[1b] canon 地名固化：{len(canon)} 个 → "
          + "、".join(e.name for e in repo.list_entities()
                      if e.type == 'location' and e.attributes.get('canon')))
    # W2 地理层：对 canon 地点做厚做权威（两级+风土人情+层级+校验回路）
    geo_stat = build_geography(repo, llm=LLM, theme=wb.get('theme', ''))
    print(f"[1c] W2 地理：{geo_stat}")
    # W3 势力系统：3-5 个独特势力（一等实体）+ 关系图 + 核心成员落卡
    fac_stat = build_factions(repo, llm=LLM, theme=wb.get('theme', ''))
    print(f"[1d] W3 势力：{fac_stat}")
    # W4 分层人物卡：主角极详 + 主配加厚
    from novel_engine.casting import enrich_character_cards
    char_stat = enrich_character_cards(repo, llm=LLM, theme=wb.get('theme', ''))
    print(f"[1e] W4 人物：{char_stat}")
    # W5 知识图谱·静态边（纯本地，无 LLM）
    from novel_engine.worldbible import build_static_graph
    graph_stat = build_static_graph(repo)
    print(f"[1f] W5 静态边：{graph_stat}")

    planner = Planner(repo, llm=LLM, theme=wb["theme"],
                      worldsmith=WorldSmith(repo, llm=LLM, theme=wb["theme"]))
    planner.build_master(part_count=2, arcs_per_part=1, chapter_scenes=3)
    # 惰性大纲（镜像 server）：全 Arc 骨架 + 首部章纲 → 前端「大纲」可见多章 POV 分布与 canon 地点
    planner.build_lazy_outline()
    print(f"[2] 大纲就绪：{len(repo.list_chapter_plans())} 章")

    director = Director(repo, worldsmith=WorldSmith(repo, llm=LLM, theme=wb["theme"]),
                        planner=planner, writer=SceneWriter(repo, llm=LLM),
                        extractor=FactExtractor(repo, llm=LLM),
                        controller=Controller(repo, llm=LLM), mode="scripted")
    done = 0
    for i in range(120):
        step = director.step()
        if step.chapter_done:
            done += 1
            ch = repo.get_chapter_plan(step.chapter_id)
            # 写满一章即取章名（与 server.step_once 同）
            try:
                planner.name_chapter(step.chapter_id)
            except Exception:
                pass
            print(f"    第{ch.sequence_order if ch else '?'}章 done（{len(repo.list_scenes())} 场）")
            if done >= N:
                break
    repo.conn.commit()
    print(f"[3] scripted 写满 {done} 章 / {len(repo.list_scenes())} 场，用时 {time.time()-t0:.0f}s")

    # —— 注册进 projects.json（status=writing → 前端列出、阅读可读）——
    now = datetime.now(timezone.utc).isoformat()
    snap = {"id": pid, "title": "孤岛·scripted", "status": "writing",
            "createdAt": now, "updatedAt": now, "chat": [], "draft": DRAFT}
    snaps = []
    if INDEX_PATH.exists():
        try:
            snaps = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
        except Exception:
            snaps = []
    snaps.append(snap)
    INDEX_PATH.write_text(json.dumps(snaps, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[4] 已注册项目「孤岛·scripted」({pid})。**重启后端**后前端可见，进「阅读」查看正文。")


if __name__ == "__main__":
    main()
