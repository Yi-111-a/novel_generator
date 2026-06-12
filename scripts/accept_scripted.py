# -*- coding: utf-8 -*-
"""B6 验收：scripted 模式（大纲驱动）端到端 DeepSeek 实测 + 自动核验。

与 server 同一套锁定流程；Director 走 scripted（SceneWriter 照 beat 写 → Controller 把关 →
FactExtractor 抽事实回写），不挂模拟核心。核验：隔离 / 揭示 / 收束 / 动作各异 / 不偏离大纲。
跑法：python scripts/accept_scripted.py
"""
import json
import sys
import time
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
from novel_engine.narration.narrator import _lead_clause, _lead_overlap
from server import seedbuilder
from scripts.try_generate3 import DRAFT

CFG = json.loads((ROOT / "server" / ".data" / "config.json").read_text(encoding="utf-8"))
LLM = build_client(LLMConfig(provider="deepseek", model=CFG["modelName"],
                             base_url=CFG["baseUrl"], api_key=CFG["llmApiKey"]))
import os as _os
TARGET_CHAPTERS = int(_os.environ.get("ACCEPT_CHAPTERS", "4"))
MAX_TICKS = 160


def main():
    t0 = time.time()
    wb = DRAFT["worldBible"]
    repo = seedbuilder.build_repo_from_draft(DRAFT, db_path=":memory:")
    print("[1] 世界落地")
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
    lock_canonical_geography(repo, llm=LLM)                     # W0 canon 固化
    build_geography(repo, llm=LLM, theme=wb.get('theme', ''))   # W2 地理层加厚
    build_factions(repo, llm=LLM, theme=wb.get('theme', ''))    # W3 势力系统
    from novel_engine.casting import enrich_character_cards
    enrich_character_cards(repo, llm=LLM, theme=wb.get('theme', ''))   # W4 人物卡加厚
    from novel_engine.worldbible import build_static_graph
    build_static_graph(repo)                                            # W5 静态边
    name_of = {e.entity_id: e.name for e in repo.list_entities()}

    planner = Planner(repo, llm=LLM, theme=wb["theme"],
                      worldsmith=WorldSmith(repo, llm=LLM, theme=wb["theme"]))
    planner.build_master(part_count=2, arcs_per_part=1, chapter_scenes=3)
    planner.plan_next_arc()
    planner.next_chapter()
    print(f"[2] 大纲就绪：{len(repo.list_chapter_plans())} 章在册")

    director = Director(repo, worldsmith=WorldSmith(repo, llm=LLM, theme=wb["theme"]),
                        planner=planner,
                        writer=SceneWriter(repo, llm=LLM),
                        extractor=FactExtractor(repo, llm=LLM),
                        controller=Controller(repo, llm=LLM), mode="scripted")
    assert director.engine is None and director.mode == "scripted"

    done = 0
    for i in range(MAX_TICKS):
        step = director.step()
        if step.chapter_done:
            done += 1
            ch = repo.get_chapter_plan(step.chapter_id)
            print(f"    第{ch.sequence_order if ch else '?'}章 done（{i+1} 拍，{len(repo.list_scenes())} 场）")
            if done >= TARGET_CHAPTERS:
                break
    print(f"[3] 写满 {done} 章，用时 {time.time()-t0:.0f}s")

    scenes = repo.list_scenes()
    out = ROOT / "scripts" / "out_accept_scripted.txt"
    with out.open("w", encoding="utf-8") as f:
        for s in scenes:
            f.write(f"\n--- 场 {s.discourse_order}（POV={name_of.get(s.pov, s.pov)}）---\n{s.prose_text}\n")
    print(f"[正文] {len(scenes)} 场 → {out}")

    chapters = sorted([c for c in repo.list_chapter_plans() if c.status == "done"],
                      key=lambda c: c.sequence_order)[:TARGET_CHAPTERS]
    per_scene = [s.prose_text for s in scenes]
    verdicts = {}

    # —— 隔离：非 cast 角色不得知道本章产出的事实 ——
    leak = []
    cast_of = {c.chapter_id: set(c.cast) for c in chapters}
    for c in chapters:
        cast = cast_of[c.chapter_id]
        for e in repo.events_for_beat(c.chapter_id):
            for fact in repo.get_facts_by_event(e.event_id):
                for p in repo.list_personas():
                    if p.agent_id not in cast and repo.agent_knows_fact(p.agent_id, fact.fact_id):
                        leak.append((name_of.get(p.agent_id, p.agent_id), fact.fact_id))
    verdicts["隔离"] = not leak
    print(f"[隔离] 非在场者越权知情：{leak[:3] or '无'} {'OK' if not leak else 'FAIL'}")

    # —— 收束：各章 done + exit_state 齐 + 实质推进 ——
    adv = [director._chapter_advanced(c) for c in chapters]
    exits = [bool(c.exit_state) for c in chapters]
    verdicts["收束"] = len(chapters) >= TARGET_CHAPTERS and all(exits)
    print(f"[收束] {len(chapters)} 章 done；exit_state 齐 {all(exits)}；实质推进 {adv} "
          f"{'OK' if verdicts['收束'] else 'WARN'}")

    # —— 揭示：reveal 节点按 prereq 顺序 discovered；读者账本受控 ——
    nodes = repo.list_reveal_nodes()
    bad_order = []
    disc = {n.node_id for n in nodes if n.discovered}
    for n in nodes:
        if n.discovered:
            for pid in (n.prereq_node_ids or []):
                if pid not in disc:
                    bad_order.append(n.node_id)
    verdicts["揭示有序"] = not bad_order
    print(f"[揭示] 已发现节点 {len(disc)}/{len(nodes)}；越级揭示 {bad_order or '无'} "
          f"读者已知 {len(repo.list_reader_knowledge())} 条 {'OK' if not bad_order else 'FAIL'}")

    # —— 动作各异：相邻场开场首分句不雷同 ——
    dup_open = []
    for a, b in zip(per_scene, per_scene[1:]):
        if _lead_overlap(a, b) >= 0.6:
            dup_open.append(_lead_clause(b))
    verdicts["开场各异"] = not dup_open
    print(f"[动作各异] 相邻场雷同开头：{dup_open[:3] or '无'} {'OK' if not dup_open else 'WARN'}")

    # —— 不偏离大纲（粗判 + 抽样供人工）：每章正文与其 beat 关键词有交集 ——
    import re as _re
    miss = []
    sc_by_beat = {}
    for s in scenes:
        for eid in s.source_events:
            ev = repo.get_event(eid)
            if ev and ev.beat_id:
                sc_by_beat.setdefault(ev.beat_id, []).append(s.prose_text)
                break
    for c in chapters:
        beat_text = "".join(c.beat_goals or [])
        kws = set(_re.findall(r"[一-龥]{2,4}", beat_text))
        body = "".join(sc_by_beat.get(c.chapter_id, []))
        hit = sum(1 for k in kws if k in body)
        if kws and hit == 0:
            miss.append(c.sequence_order)
    verdicts["在大纲上"] = not miss
    print(f"[不偏离大纲] 与 beat 关键词零交集的章：{miss or '无'} {'OK' if not miss else 'WARN'}")

    # —— 基础：无 id 泄漏 ——
    leaked_ids = sorted(set(_re.findall(r'\b(?:obj|p|cast|loc|ev|named|f|rv)_[A-Za-z0-9_]+',
                                        "\n".join(per_scene))))
    verdicts["无id泄漏"] = not leaked_ids
    print(f"[基础] id 泄漏：{leaked_ids[:5] or '无'} {'OK' if not leaked_ids else 'FAIL'}")

    print("\n========= 验收汇总 =========")
    for k, v in verdicts.items():
        print(f"  {k}: {'[PASS]' if v else '[FAIL]'}")
    hard = all(verdicts[k] for k in ("隔离", "揭示有序", "无id泄漏", "收束"))
    print(f"\n硬指标（隔离/揭示/收束/id）：{'✅ 通过' if hard else '❌ 未过'}")
    return 0 if hard else 1


if __name__ == "__main__":
    sys.exit(main())
