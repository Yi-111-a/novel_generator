# -*- coding: utf-8 -*-
"""新题材验证：民国谍战（1939 孤岛上海）。进程内端到端，与 server 同一套引擎。"""
import io, re, sys, json, time
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT))
if __name__ == "__main__":
    try: sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    except Exception: pass

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
from novel_engine.casting import cast_named_characters, ensure_cards_for_personas, infer_and_store_genders, lock_motif_canon
from server import seedbuilder

CFG = json.loads((ROOT / "server" / ".data" / "config.json").read_text(encoding="utf-8"))
LLM = build_client(LLMConfig(provider="deepseek", model=CFG["modelName"],
                             base_url=CFG["baseUrl"], api_key=CFG["llmApiKey"]))

DRAFT = {
    "worldBible": {
        "genre": "thriller",
        "settingCore": (
            "1939 年的上海，租界孤悬于沦陷区之中，号称「孤岛」。霓虹与暗杀并存，舞厅的爵士乐盖不住"
            "弄堂里的枪声。三股势力在同一张牌桌上：潜伏的地下党、阴鸷的军统、新挂牌的汪伪特工总部"
            "「七十六号」。每个人都有两张脸，名字底下还压着另一个名字。"
        ),
        "geography": (
            "法租界·霞飞路：梧桐成荫的洋派街区，咖啡馆与书店是接头的暗角。"
            "七十六号：极司菲尔路的汪伪特工总部，铁门内是审讯室与刑具，进去的人少有囫囵出来。"
            "百乐门舞厅：纸醉金迷的销金窟，也是各方眼线交汇、情报易手之地。"
            "苏州河北岸·闸北废墟：日军占领后的焦土，无人区，唯一不被任何一方监控的死角。"
            "永安里亭子间：地下党的秘密联络点，一盏台灯的明灭就是暗号。"
        ),
        "culture": (
            "孤岛人信奉「各扫门前雪」，忌打听邻人来历、忌深夜亮灯、忌在舞厅里认出熟人却开口。"
            "三方互相渗透：今日的同志可能是明日的叛徒，递烟的招待或许就是对方的耳目。忠诚比命金贵，也比命短。"
        ),
        "physicsRules": [
            "暴露身份即死——无论被哪一方识破，活着走出去的概率几近于零。",
            "传递的情报一旦经手第三人，必失真或被掉包，除非当面交验暗号。",
            "在闸北废墟里，任何一方的人都不敢久留，是唯一能安全说真话的地方。",
            "假身份用得越久，破绽越深——一个旧相识的眼神就能让七年的伪装崩塌。",
        ],
        "protagonistWant": (
            "潜伏在七十六号的地下党员沈砚（化名「秦书白」）要把一份日军「清乡」计划的名单送出去，"
            "同时查清三个月前同一条线上的交通员、他的妻子林婉为何突然失联、是死是叛。"
        ),
        "theme": "在人人都戴假面的年代，忠诚的代价是连自己都快认不出自己。",
        "candidateEndings": [
            {"id": "end_send", "summary": "秦书白识破身边的招待赵九就是军统钉子，借他的手把名单的假本递出去引开追查，"
             "真名单经闸北废墟亲手交给接头人。他得知林婉早已牺牲、失联是为护他周全，他烧掉她最后的纸条，继续做秦书白。",
             "themeExpression": "守住信念的人，必须先亲手埋葬那个有名有姓的自己。",
             "requiredConditions": ["秦书白送出真名单", "秦书白查明林婉下落"], "activeWeight": 0.6},
            {"id": "end_fall", "summary": "秦书白为查林婉下落，动用了一次本不该用的关系，露了破绽。名单送出，"
             "但他被七十六号盯上，最后一面假身份也保不住。他在闸北废墟点了最后一支烟，等天亮。",
             "themeExpression": "想做回那个有名字的人，往往就是伪装崩塌的开始。",
             "requiredConditions": ["秦书白暴露身份", "名单已送出"], "activeWeight": 0.4},
        ],
    },
    "personas": [
        {"id": "p_shenyan", "name": "沈砚", "gender": "男", "want": "把清乡名单送出去，并查清妻子林婉的下落",
         "values": [{"name": "信念", "weight": 0.85}, {"name": "护住所爱", "weight": 0.7}],
         "fatalFlaw": "一旦牵涉林婉，他就会违背纪律去查，亲手在滴水不漏的伪装上凿出破绽",
         "obstacles": ["七十六号的内部猜忌", "军统钉子的盯梢", "三个月断了的那条线"],
         "costThreshold": "愿以命换名单送达，却迟迟不敢面对林婉可能已死、且死于护他的事实",
         "voice": "克制、字斟句酌，公开场合一口官腔，独处时才露真声",
         "mannerisms": ["在开口前先掸一下并不存在的烟灰", "用钢笔帽轻叩桌沿三下"],
         "motifObjects": ["obj_pen"], "arcState": "", "costLedger": []},
        {"id": "p_zhaojiu", "name": "赵九", "gender": "男", "want": "在三方夹缝里替自己挣一条活路，谁给的筹码大就替谁办事",
         "values": [{"name": "自保", "weight": 0.9}, {"name": "江湖义气", "weight": 0.45}],
         "fatalFlaw": "贪小利、好赌，关键时刻总被一笔横财或一条把柄牵着鼻子走",
         "obstacles": ["军统的把柄攥在别人手里", "七十六号的监视", "欠下的赌债"],
         "costThreshold": "什么都能卖，唯独不肯卖了自己的命；可一旦赌红了眼，连这条底线也守不住",
         "voice": "油滑、爱插科打诨，话里三分真七分诨，越紧张越贫嘴",
         "mannerisms": ["搓手指像在数钱", "笑的时候眼睛不笑"],
         "motifObjects": ["obj_dice"], "arcState": "", "costLedger": []},
        {"id": "p_sujing", "name": "苏静", "gender": "女", "want": "查出谁是潜伏的地下党，向七十六号请功上位，洗掉自己的旧档案",
         "values": [{"name": "向上爬", "weight": 0.85}, {"name": "不留活口的谨慎", "weight": 0.6}],
         "fatalFlaw": "疑心病重到容不得任何破绽，反而常因为追查得太急、太狠而打草惊蛇",
         "obstacles": ["自己也有一段见不得光的旧历", "上司的猜忌", "查不到实证的潜伏者"],
         "costThreshold": "可以牺牲任何同僚换取上位，但绝不让自己的旧档案见光",
         "voice": "冷、慢、每句话都像在审讯，笑里带刀",
         "mannerisms": ["说话时盯着对方的手而非眼睛", "用指甲轻刮茶杯沿"],
         "motifObjects": ["obj_file"], "arcState": "", "costLedger": []},
    ],
}


def main():
    t0 = time.time()
    wb = DRAFT["worldBible"]
    repo = seedbuilder.build_repo_from_draft(DRAFT, db_path=":memory:")
    print("[1] 世界落地")
    build_tone_profile(repo, llm=LLM, genre=wb["genre"], theme=wb["theme"], setting_hint=wb["settingCore"][:200])
    tp = repo.get_tone_profile()
    print(f"[2] 文风：{tp.genre} / {tp.primary_effect} / pacing={tp.pacing[:30]}")
    ensure_cards_for_personas(repo)
    named = cast_named_characters(repo, "\n".join([wb["settingCore"], wb["geography"], wb["culture"]]),
                                  wb["protagonistWant"], llm=LLM)
    genders = infer_and_store_genders(repo, llm=LLM)
    canon = lock_motif_canon(repo, llm=LLM)
    chars = [(e.entity_id, e.name) for e in repo.list_entities() if e.type == "character"]
    print(f"[3] 缺席核心人物：{named or '无'}；角色性别：{ {n: genders.get(i,'?') for i,n in chars} }")
    print(f"    道具固定设定 canon: { {repo.get_entity(o).name: c[:30] for o,c in canon.items()} }")

    planner = Planner(repo, llm=LLM, theme=wb["theme"], worldsmith=WorldSmith(repo, llm=LLM, theme=wb["theme"]))
    planner.build_master(part_count=1, arcs_per_part=1, chapter_scenes=2)
    planner.plan_next_arc()
    ch = planner.next_chapter()
    print(f"[4] 首章 role={ch.role} cast={[dict(chars).get(c,c) for c in ch.cast]}")
    for i, b in enumerate(ch.beat_goals):
        print(f"    beat[{i}]: {b[:70]}")

    director = Director(repo, DilemmaGenerator(repo, llm=LLM, theme=wb["theme"]),
                        CharacterAgent(repo, LLM), Validator(repo),
                        Monitors(repo, flaw_max_free=2), planner=planner)
    chapters_done = 0
    for i in range(120):
        if director.step().chapter_done:
            chapters_done += 1
            if chapters_done >= 3:  # 写 3 章，足够看跨章刻字一致 + 意象禁令(≥3场)
                print(f"[5] 写满 {chapters_done} 章，用 {i+1} 拍"); break

    name_of = {e.entity_id: e.name for e in repo.list_entities()}
    Editor(repo, llm=LLM, theme=wb["theme"], threshold=0.4,
           reveal_budget=1, max_rewrites=2).render_incremental(set(), max_new=30)
    scenes = repo.list_scenes()
    out = ROOT / "scripts" / "out_sample3.txt"
    with out.open("w", encoding="utf-8") as f:
        for s in scenes:
            f.write(f"\n--- 场 {s.discourse_order}（POV={name_of.get(s.pov,s.pov)}，{len(s.source_events)} 源事件）---\n{s.prose_text}\n")
    print(f"[正文] 写入 {out}（{len(scenes)} 场）")

    all_prose = "\n".join(s.prose_text for s in scenes)
    leaked = re.findall(r'\b(?:obj|p|cast|loc|ev|named)_[A-Za-z0-9_]+', all_prose)
    print("\n========= 自动检查 =========")
    print(f"[id-leak] {'FAIL '+str(set(leaked)) if leaked else 'OK'}")
    print(f"[english-leak] {re.findall(r'[a-zA-Z]{4,}', all_prose)[:6] or 'OK'}")
    # 问题2：氛围意象刷屏度（每个意象在多少场出现）
    from novel_engine.narration.narrator import _IMAGERY_CANDIDATES
    per_scene = [s.prose_text for s in scenes]
    hot = {w: sum(1 for t in per_scene if w in t) for w in _IMAGERY_CANDIDATES}
    hot = {w:c for w,c in sorted(hot.items(),key=lambda x:-x[1]) if c>=3}
    print(f"[意象刷屏(出现≥3场)] {hot or '无'}  (共{len(scenes)}场)")
    print(f"用时 {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
