# -*- coding: utf-8 -*-
"""四批修复·统一验收：民国谍战（1939 孤岛上海）重新锁定生成前 5 章，自动核验 6 个问题。

问题1 场景停滞 / 问题2 意象刷屏 / 问题3 钢笔刻字矛盾 / 问题4 推进慢 /
问题5 身份揭示突兀 / 问题6 POV 单一。与 server 同一套引擎、同一套锁定流程。
"""
import io, re, sys, json, time
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT))

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
from novel_engine.casting import (cast_named_characters, ensure_cards_for_personas,
                                  infer_and_store_genders, lock_motif_canon,
                                  lock_hidden_identities)
from server import seedbuilder

# 复用 try_generate3 的谍战草稿（沈砚/赵九/苏静，钢笔/骰子/卷宗意象）
from scripts.try_generate3 import DRAFT

CFG = json.loads((ROOT / "server" / ".data" / "config.json").read_text(encoding="utf-8"))
LLM = build_client(LLMConfig(provider="deepseek", model=CFG["modelName"],
                             base_url=CFG["baseUrl"], api_key=CFG["llmApiKey"]))

TARGET_CHAPTERS = 5
PEN_MATERIALS = ["犀飞利", "派克", "万宝龙", "英雄", "金笔", "钢笔", "自来水笔"]


def main():
    t0 = time.time()
    wb = DRAFT["worldBible"]
    repo = seedbuilder.build_repo_from_draft(DRAFT, db_path=":memory:")
    print("[1] 世界落地")
    # ---- 锁定流程：与 server.projects.lock_and_build 同序 ----
    build_tone_profile(repo, llm=LLM, genre=wb["genre"], theme=wb["theme"],
                       setting_hint=wb["settingCore"][:200])
    ensure_cards_for_personas(repo)
    named = cast_named_characters(repo, "\n".join([wb["settingCore"], wb["geography"], wb["culture"]]),
                                  wb["protagonistWant"], llm=LLM)
    infer_and_store_genders(repo, llm=LLM)
    canon = lock_motif_canon(repo, llm=LLM)
    identities = lock_hidden_identities(repo, llm=LLM)   # 问题5
    name_of = {e.entity_id: e.name for e in repo.list_entities()}
    print(f"[2] 缺席核心人物：{named or '无'}")
    print(f"    道具 canon: { {name_of.get(o): c[:34] for o,c in canon.items()} }")
    print(f"    隐藏身份: { {name_of.get(a): (d['public'],'→',d['true']) for a,d in identities.items()} }")

    planner = Planner(repo, llm=LLM, theme=wb["theme"],
                      worldsmith=WorldSmith(repo, llm=LLM, theme=wb["theme"]))
    planner.build_master(part_count=2, arcs_per_part=1, chapter_scenes=2)
    planner.plan_next_arc()
    planner.next_chapter()

    director = Director(repo, DilemmaGenerator(repo, llm=LLM, theme=wb["theme"]),
                        CharacterAgent(repo, LLM), Validator(repo),
                        Monitors(repo, flaw_max_free=2), planner=planner)
    done = 0
    for i in range(240):
        if director.step().chapter_done:
            done += 1
            if done >= TARGET_CHAPTERS:
                print(f"[3] 写满 {done} 章，用 {i+1} 拍"); break

    Editor(repo, llm=LLM, theme=wb["theme"], threshold=0.4,
           reveal_budget=1, max_rewrites=2).render_incremental(set(), max_new=40)
    scenes = repo.list_scenes()
    out = ROOT / "scripts" / "out_accept_five.txt"
    with out.open("w", encoding="utf-8") as f:
        for s in scenes:
            f.write(f"\n--- 场 {s.discourse_order}（POV={name_of.get(s.pov,s.pov)}）---\n{s.prose_text}\n")
    print(f"[正文] {len(scenes)} 场 → {out}")

    # ---------- 自动核验 ----------
    chapters = sorted(repo.list_chapter_plans(), key=lambda c: c.sequence_order)[:TARGET_CHAPTERS]
    ev_chap = {}   # event_id -> chapter seq
    for c in chapters:
        for e in repo.events_for_beat(c.chapter_id):
            ev_chap[e.event_id] = c.sequence_order
    per_scene = [s.prose_text for s in scenes]
    all_prose = "\n".join(per_scene)

    print("\n========= 统一验收（前 5 章） =========")
    verdicts = {}

    # 基础：无 id 泄漏 / 无英文残留
    leaked = sorted(set(re.findall(r'\b(?:obj|p|cast|loc|ev|named|f|rv)_[A-Za-z0-9_]+', all_prose)))
    verdicts["id-leak"] = not leaked
    print(f"[基础] id 泄漏：{'OK' if not leaked else 'FAIL '+str(leaked)}")

    # 问题6 POV 单一 → 应有 ≥2 个不同 POV
    pov_seq = [name_of.get(c.pov_agent, c.pov_agent) for c in chapters]
    distinct_pov = sorted(set(p for p in pov_seq if p))
    verdicts["问题6 POV"] = len(distinct_pov) >= 2
    print(f"[问题6] 各章 POV：{pov_seq} → 不同视角 {len(distinct_pov)} 个 "
          f"{'OK' if len(distinct_pov)>=2 else 'FAIL(仍是独角)'}")

    # 问题1 场景停滞 → 5 章应换过场（地点 ≥2）
    loc_seq = [name_of.get(c.location_ids[0], c.location_ids[0]) if c.location_ids else "?" for c in chapters]
    distinct_loc = sorted(set(loc_seq))
    verdicts["问题1 换场"] = len(distinct_loc) >= 2
    print(f"[问题1] 各章地点：{loc_seq} → 不同地点 {len(distinct_loc)} 个 "
          f"{'OK' if len(distinct_loc)>=2 else 'FAIL(卡在一处)'}")

    # 问题4 推进慢 → 每章须有"实质推进"(exit_state 在册 + 关键抉择/揭示/道具易手)
    adv = []
    for c in chapters:
        ok = director._chapter_advanced(c)
        adv.append(ok)
    verdicts["问题4 推进"] = all(adv) and all(c.exit_state for c in chapters)
    print(f"[问题4] 各章实质推进：{adv}；exit_state 齐备："
          f"{all(c.exit_state for c in chapters)} {'OK' if verdicts['问题4 推进'] else 'WARN'}")

    # 问题2 意象刷屏 → 没有意象在 ≥3 场反复
    from novel_engine.narration.narrator import _IMAGERY_CANDIDATES
    hot = {w: sum(1 for t in per_scene if w in t) for w in _IMAGERY_CANDIDATES}
    hot = {w: c for w, c in sorted(hot.items(), key=lambda x: -x[1]) if c >= 3}
    verdicts["问题2 意象"] = not hot
    print(f"[问题2] 意象刷屏(≥3场)：{hot or '无'} {'OK' if not hot else 'WARN'}  (共{len(scenes)}场)")

    # 问题3 钢笔刻字矛盾 → 全书只能出现一种材质 + 一种刻字
    mats = sorted({m for m in PEN_MATERIALS if m in all_prose and m not in ("钢笔",)})
    engraved = sorted(set(re.findall(r'刻[着了]?[『「《]?(.)[』」》]?字', all_prose)))
    verdicts["问题3 刻字"] = len(mats) <= 1 and len(engraved) <= 1
    print(f"[问题3] 钢笔材质词：{mats or '（仅泛称钢笔）'}；刻字：{engraved or '无'} "
          f"{'OK' if verdicts['问题3 刻字'] else 'FAIL(前后矛盾)'}")

    # 问题5 身份揭示突兀 → 真实头衔不得出现在揭示章之前
    p5_ok = True
    if identities:
        nodes = repo.list_reveal_nodes()
        for aid, d in identities.items():
            true_app = d["true"]
            fid = d["fact_id"]
            rnode = next((n for n in nodes if n.fact_id == fid), None)
            reveal_chap = rnode.discovered_chapter if (rnode and rnode.discovered) else None
            # 找真实头衔在正文里首次出现的章
            first_chap = None
            for s in scenes:
                if true_app in s.prose_text:
                    cseq = next((ev_chap.get(e) for e in s.source_events if ev_chap.get(e)), None)
                    first_chap = cseq; break
            status = "未出现" if first_chap is None else f"首现于第{first_chap}章"
            rv = "未揭示" if reveal_chap is None else f"第{reveal_chap}章揭示"
            premature = first_chap is not None and (reveal_chap is None or first_chap < reveal_chap)
            if premature:
                p5_ok = False
            print(f"[问题5] {name_of.get(aid)} 头衔「{true_app}」：{status}，{rv} "
                  f"{'FAIL(过早)' if premature else 'OK'}")
    else:
        print("[问题5] 本次未抽出隐藏身份（LLM 判定无需隐藏的特殊身份）")
    verdicts["问题5 身份"] = p5_ok

    print("\n--------- 汇总 ---------")
    for k, v in verdicts.items():
        print(f"  {k}: {'PASS' if v else 'CHECK'}")
    print(f"用时 {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
