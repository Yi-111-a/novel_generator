# -*- coding: utf-8 -*-
"""全新题材验证：赛博朋克·义体记忆。复用 try_generate 的进程内端到端流程，
换一套完全不同的世界种子（科幻/义体/记忆黑市），检验引擎在非恐怖题材下的表现。
"""
import io
import re
import sys
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass

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
from novel_engine.casting import cast_named_characters, ensure_cards_for_personas, infer_and_store_genders
from server import seedbuilder

CFG = json.loads((ROOT / "server" / ".data" / "config.json").read_text(encoding="utf-8"))
LLM = build_client(LLMConfig(provider="deepseek", model=CFG["modelName"],
                             base_url=CFG["baseUrl"], api_key=CFG["llmApiKey"]))

DRAFT = {
    "worldBible": {
        "genre": "scifi",
        "settingCore": (
            "2089 年的霓港，一座建在旧海堤上的层叠之城。义体改造早已普及，记忆可以被提取、"
            "剪辑、买卖与覆写。底层人靠典当记忆换取续命的义体维护费，上层人则收购他人的人生片段"
            "当作消遣。一家叫「拾忆」的黑市作坊，专做记忆的删改与赎回，门口挂着一句话：你不要的，"
            "我们高价回收；你想找回的，未必还是原来的样子。"
        ),
        "geography": (
            "霓港·下城：终年不见天日的堤底贫民窟，霓虹管线密布，雨水混着机油从上城滴落。"
            "拾忆作坊：下城七巷尽头的记忆作坊，地下藏着一台老式神经剪辑机「织梦者」。"
            "上城·云端区：悬浮平台上的富人区，空气是付费的，记忆交易所设在这里。"
            "断电带：一片信号屏蔽的废弃工业区，义体在此失灵，是唯一无法被监控、也无法读取记忆的地方。"
            "记忆交易所：上城中央的拍卖场，珍稀人生片段在此竞价，成交记忆会从卖家脑中永久抹除。"
        ),
        "culture": (
            "下城人信奉「记得就还在」，忌讳卖掉关于至亲的记忆；上城人推崇「体验即拥有」，"
            "买来的记忆与亲历无异。义体公司与记忆掮客暗中勾连：公司放贷、掮客回收，债务以记忆抵偿。"
            "警方「神经稽查队」名义上打击非法记忆交易，实则替交易所清理碍事的人。"
        ),
        "physicsRules": [
            "记忆一旦被提取出售，原主脑中即永久抹除，除非有备份芯片。",
            "覆写的假记忆与真记忆在主观上无法区分，唯一破绽是假记忆里没有痛觉。",
            "在断电带内，所有义体与神经接口失灵，记忆既不能被读取也不能被篡改。",
            "同一段记忆被复制超过三次后会开始失真、出现不属于原主的杂讯人影。",
        ],
        "protagonistWant": (
            "记忆掮客林澈要找回七年前被自己亲手卖掉的、关于妻子苏窈的全部记忆——他如今"
            "连她的脸都想不起来，只记得自己曾爱过一个人。"
        ),
        "theme": "当记忆可以伪造，证明你爱过一个人的唯一方式，是愿意为找回她而失去你自己。",
        "candidateEndings": [
            {"id": "end_real", "summary": "林澈在断电带里凭无法造假的痛觉认出，交易所拍卖的那段"
             "'苏窈'记忆是伪造的诱饵，真正的记忆早被司岚备份私藏。他用自己全部剩余记忆做赌注换回备份，"
             "记起了她——代价是从此不再记得自己是谁。",
             "themeExpression": "认出真爱的代价，是甘愿不再认识自己。",
             "requiredConditions": ["林澈识破伪造记忆", "林澈取回苏窈备份"], "activeWeight": 0.6},
            {"id": "end_fake", "summary": "林澈买下交易所那段被三次复制、已然失真的'苏窈'，"
             "明知掺了杂讯人影仍选择相信。他抱着一个或许从未真实存在的妻子活下去，下城再无人卖记忆。",
             "themeExpression": "有时我们爱的，是自己愿意记住的版本。",
             "requiredConditions": ["林澈买下失真记忆", "林澈放弃追查真相"], "activeWeight": 0.4},
        ],
    },
    "personas": [
        {"id": "p_linche", "name": "林澈", "want": "找回七年前亲手卖掉的、关于妻子苏窈的全部记忆",
         "values": [{"name": "赎回", "weight": 0.8}, {"name": "诚实面对自己", "weight": 0.6}],
         "fatalFlaw": "每当真相逼近，他就用'再卖一段记忆换线索'来逃避，亲手让自己越来越空",
         "obstacles": ["想不起妻子的脸", "欠义体公司的记忆债", "稽查队的盯梢"],
         "costThreshold": "愿意搭上自己是谁的记忆，但迟迟不敢面对当年是自己签字卖了她",
         "voice": "干、冷、爱用反问，像在审讯自己",
         "mannerisms": ["用拇指摩挲左腕的旧接口疤", "说谎前会先笑一下"],
         "motifObjects": ["obj_backup_chip"], "arcState": "", "costLedger": []},
        {"id": "p_sigang", "name": "司岚", "want": "把'拾忆'作坊和林澈的债一起攥在手里，让他永远赎不清",
         "values": [{"name": "掌控", "weight": 0.9}, {"name": "旧情未了", "weight": 0.5}],
         "fatalFlaw": "她私藏着苏窈的真备份，却用它当筹码而非归还，连自己都骗说这是生意",
         "obstacles": ["上城交易所的施压", "林澈逼近备份的真相", "自己对林澈未消的旧情"],
         "costThreshold": "可以放弃整间作坊，但不肯承认自己一直在等林澈记起的不是苏窈而是她",
         "voice": "慵懒、绵里藏针，话里永远留一半",
         "mannerisms": ["转着指间的记忆芯片", "提到苏窈就改口叫'那段货'"],
         "motifObjects": ["obj_su_backup"], "arcState": "", "costLedger": []},
        {"id": "p_axu", "name": "阿絮", "want": "凑够记忆赎回上城拍卖掉的母亲，别让她从自己脑里消失",
         "values": [{"name": "记得", "weight": 0.8}, {"name": "义气", "weight": 0.6}],
         "fatalFlaw": "太信'记得就还在'，宁可卖掉自己的童年也要留住一个或许已不爱她的母亲",
         "obstacles": ["人微言轻", "母亲的记忆已被复制三次开始失真", "被掮客当跑腿利用"],
         "costThreshold": "愿卖掉自己的过去，但受不了发现留住的是个失真的赝品",
         "voice": "急、亮、藏不住事，一紧张就语速翻倍",
         "mannerisms": ["攥紧脖子上的空记忆挂坠", "数硬币似的数自己还剩几段记忆"],
         "motifObjects": ["obj_locket"], "arcState": "", "costLedger": []},
    ],
}


def main():
    t0 = time.time()
    wb = DRAFT["worldBible"]
    repo = seedbuilder.build_repo_from_draft(DRAFT, db_path=":memory:")
    print("[1] 世界落地完成")
    build_tone_profile(repo, llm=LLM, genre=wb["genre"], theme=wb["theme"],
                       setting_hint=wb["settingCore"][:200])
    print("[2] 文风契约完成")
    ensure_cards_for_personas(repo)
    named = cast_named_characters(
        repo, bible_text="\n".join([wb["settingCore"], wb["geography"], wb["culture"]]),
        want_text=wb["protagonistWant"], llm=LLM)
    print(f"[3] P4a 抽取缺席核心人物：{named or '（无）'}")
    genders = infer_and_store_genders(repo, llm=LLM)
    chars = [(e.entity_id, e.name) for e in repo.list_entities() if e.type == "character"]
    name_g = {n: genders.get(i, '?') for i, n in chars}
    print(f"    现有角色（性别）：{name_g}")

    planner = Planner(repo, llm=LLM, theme=wb["theme"],
                      worldsmith=WorldSmith(repo, llm=LLM, theme=wb["theme"]))
    planner.build_master(part_count=1, arcs_per_part=1, chapter_scenes=2)
    planner.plan_next_arc()
    ch = planner.next_chapter()
    print(f"[4] 首章 role={ch.role} cast={[ (dict(chars)).get(c, c) for c in ch.cast ]}")
    print(f"    beats={ch.beat_goals}")

    director = Director(repo, DilemmaGenerator(repo, llm=LLM, theme=wb["theme"]),
                        CharacterAgent(repo, LLM), Validator(repo),
                        Monitors(repo, flaw_max_free=2), planner=planner)
    for i in range(40):
        step = director.step()
        if step.chapter_done:
            print(f"[5] 第 1 章写满，用 {i+1} 拍")
            break

    ch = repo.get_chapter_plan(ch.chapter_id)
    evs = repo.events_for_beat(ch.chapter_id)
    name_of = {e.entity_id: e.name for e in repo.list_entities()}
    Editor(repo, llm=LLM, theme=wb["theme"], threshold=0.4,
           reveal_budget=1, max_rewrites=2).render_incremental(set(), max_new=10)
    scenes = repo.list_scenes()
    out = ROOT / "scripts" / "out_sample2.txt"
    with out.open("w", encoding="utf-8") as f:
        for s in scenes:
            f.write(f"\n--- 场 {s.discourse_order}（POV={name_of.get(s.pov, s.pov)}，"
                    f"{len(s.source_events)} 源事件）---\n{s.prose_text}\n")
    print(f"[正文] 已写入 {out}（{len(scenes)} 场）")

    all_prose = "\n".join(s.prose_text for s in scenes)
    leaked = re.findall(r'\b(?:obj|p|cast|loc|ev|named)_[A-Za-z0-9_]+', all_prose)
    cast_names = set(name_of.get(a, a) for a in ch.cast)
    actor_names = set(name_of.get(a, a) for e in evs for a in e.actors)
    print("\n========= 自动检查 =========")
    print(f"[id-leak] {'FAIL ' + str(set(leaked)) if leaked else 'OK'}")
    print(f"[placeholder] {'FAIL' if '无名' in all_prose else 'OK'}")
    print(f"[multi-actor] {actor_names}  {'OK' if len(actor_names)>=2 else 'FAIL'}")
    print(f"[3-layer] {'OK' if actor_names <= cast_names else 'FAIL ' + str(actor_names - cast_names)}")
    print(f"[grouping] {sum(1 for s in scenes if len(s.source_events)>=2)}/{len(scenes)} multi-event scenes")
    print(f"[named] {named}")
    print(f"用时 {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
