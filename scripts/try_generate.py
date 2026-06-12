# -*- coding: utf-8 -*-
"""进程内端到端小样：精简规模（1 部·1 Arc·每章 2 场）用真实 LLM 写出 1 章正文，
用于检验多角色对手戏(P1)、id 清洗(P4d)、道具转交(P5)、取名(P4b/c)、缺席人物(P4a)。

直接复用 server 的 DRAFT 与 config，绕开 HTTP 与全量章纲生成（只写 1 章）。
"""
import io
import json
import re
import sys
import time
from pathlib import Path

# PowerShell 控制台默认 GBK，强制 UTF-8 输出避免特殊字符崩溃
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from novel_engine.config import LLMConfig
from novel_engine.llm import build_client
from novel_engine.agent import CharacterAgent
from novel_engine.dilemma import DilemmaGenerator
from novel_engine.director import Director
from novel_engine.monitors import Monitors
from novel_engine.validator import Validator
from novel_engine.planner import Planner
from novel_engine.worldsmith import WorldSmith
from novel_engine.tone import build_tone_profile
from novel_engine.narration.editor import Editor
from novel_engine.casting import cast_named_characters, ensure_cards_for_personas
from server import seedbuilder
from scripts.seed_huijiao import DRAFT

CFG = json.loads((ROOT / "server" / ".data" / "config.json").read_text(encoding="utf-8"))
LLM = build_client(LLMConfig(provider="deepseek", model=CFG["modelName"],
                             base_url=CFG["baseUrl"], api_key=CFG["llmApiKey"]))


def main():
    t0 = time.time()
    wb = DRAFT["worldBible"]
    repo = seedbuilder.build_repo_from_draft(DRAFT, db_path=":memory:")
    print("[1] 世界落地完成")

    build_tone_profile(repo, llm=LLM, genre=wb.get("genre", ""),
                       theme=wb.get("theme", ""), setting_hint=wb.get("settingCore", "")[:200])
    print("[2] 文风契约完成")

    ensure_cards_for_personas(repo)
    named = cast_named_characters(
        repo,
        bible_text="\n".join([wb.get("settingCore", ""), wb.get("geography", ""), wb.get("culture", "")]),
        want_text=wb.get("protagonistWant", ""), llm=LLM)
    print(f"[3] P4a 抽取缺席核心人物：{named or '（无）'}")
    chars = [(e.entity_id, e.name) for e in repo.list_entities() if e.type == "character"]
    print(f"    现有角色：{[n for _, n in chars]}")

    planner = Planner(repo, llm=LLM, theme=wb.get("theme", ""),
                      worldsmith=WorldSmith(repo, llm=LLM, theme=wb.get("theme", "")))
    planner.build_master(part_count=1, arcs_per_part=1, chapter_scenes=2)
    planner.plan_next_arc()
    ch = planner.next_chapter()
    print(f"[4] 首章：{ch.chapter_id} role={ch.role} cast={ch.cast}")
    print(f"    beats={ch.beat_goals}")
    print(f"    道具台账 present={ch.items_present} available={ch.available_items}")

    director = Director(repo, DilemmaGenerator(repo, llm=LLM, theme=wb.get("theme", "")),
                        CharacterAgent(repo, LLM), Validator(repo),
                        Monitors(repo, flaw_max_free=2), planner=planner)

    # 写完 1 章
    done = False
    for i in range(40):
        step = director.step()
        if step.chapter_done:
            done = True
            print(f"[5] 第 1 章写满，用 {i+1} 拍")
            break
    if not done:
        print("[5] 警告：40 拍内未收束")

    ch = repo.get_chapter_plan(ch.chapter_id)
    evs = repo.events_for_beat(ch.chapter_id)
    print(f"\n=== 事件流（{len(evs)} 个）===")
    name_of = {e.entity_id: e.name for e in repo.list_entities()}
    for e in evs:
        p = e.payload or {}
        actor = name_of.get(e.actors[0], e.actors[0]) if e.actors else "?"
        print(f"  · [{actor}] {e.action_type} → tgt={p.get('target')} "
              f"对白={p.get('dialogue','')[:30]} val={p.get('chosen_value','')[:14]}")

    # 渲染正文
    Editor(repo, llm=LLM, theme=wb.get("theme", ""), threshold=0.4,
           reveal_budget=1, max_rewrites=1).render_incremental(set(), max_new=10)
    scenes = repo.list_scenes()

    # 正文 dump 到文件（避免控制台编码问题）
    out = ROOT / "scripts" / "out_sample.txt"
    with out.open("w", encoding="utf-8") as f:
        for s in scenes:
            f.write(f"\n--- 场 {s.discourse_order}（POV={name_of.get(s.pov, s.pov)}，"
                    f"{len(s.source_events)} 个源事件）---\n")
            f.write(s.prose_text + "\n")
    print(f"\n[正文] 已写入 {out}（{len(scenes)} 场）")

    # ===== 自动检查 =====
    print("\n========= 自动检查 =========")
    all_prose = "\n".join(s.prose_text for s in scenes)
    leaked = re.findall(r'\b(?:obj|p|cast|loc|ev|named)_[A-Za-z0-9_]+', all_prose)
    print(f"[id-leak]  {'FAIL ' + str(set(leaked)) if leaked else 'OK'}")
    placeholder = [n for _, n in chars if "无名" in n]
    print(f"[no-placeholder] {'FAIL ' + str(placeholder) if placeholder else 'OK'}")
    cast_names = set(name_of.get(a, a) for a in ch.cast)
    actor_names = set(name_of.get(a, a) for e in evs for a in e.actors)
    print(f"[multi-actor] actors={actor_names}  {'OK' if len(actor_names) >= 2 else 'FAIL only-one'}")
    print(f"[3-layer-consistent] cast={cast_names}  {'OK' if actor_names <= cast_names else 'FAIL actor-outside-cast'}")
    multi = [s for s in scenes if len(s.source_events) >= 2]
    print(f"[scene-grouping] {len(multi)}/{len(scenes)} scenes have >=2 source events")
    # 正文出场角色名 vs cast
    appearing = sorted(n for _, n in chars if n in all_prose)
    print(f"[appearing-in-prose] {appearing}")
    inv = [(name_of.get(i.object_id, i.object_id), name_of.get(i.holder_agent_id, i.holder_agent_id), i.status)
           for i in repo.list_inventory()]
    print(f"[inventory] {inv}")
    print(f"[named-extract] {named}")
    print(f"\n用时 {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
