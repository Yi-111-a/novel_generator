# -*- coding: utf-8 -*-
"""新题材「深渊学院」：全链生成 → 注册到前端可见。
跑法：PYTHONUTF8=1 ACCEPT_CHAPTERS=2 python scripts/seed_abyss.py
"""
import json, os, sys, time, uuid
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
                                  lock_hidden_identities, lock_aliases,
                                  enrich_character_cards)
from novel_engine.worldbible import (build_factions, build_geography,
                                      build_world_skill, lock_canonical_geography,
                                      build_static_graph)
from novel_engine.narration.scene_writer import SceneWriter
from novel_engine.narration.fact_extractor import FactExtractor
from novel_engine.narration.controller import Controller
from novel_engine.llm.logging_wrapper import LoggingLLMClient
from server import seedbuilder

DATA_DIR = ROOT / "server" / ".data"
PROJECTS_DIR = DATA_DIR / "projects"
INDEX_PATH = DATA_DIR / "projects.json"
CFG = json.loads((DATA_DIR / "config.json").read_text(encoding="utf-8"))
_RAW_LLM = build_client(LLMConfig(provider="deepseek", model=CFG["modelName"],
                                   base_url=CFG["baseUrl"], api_key=CFG["llmApiKey"]))
N = int(os.environ.get("ACCEPT_CHAPTERS", "2"))

DRAFT = {
    "worldBible": {
        "genre": "dark_fantasy",
        "settingCore": (
            "大陆尽头有一道贯穿地壳的裂缝，名为「深渊」，裂缝边缘建着唯一一所探索者学院——"
            "「渊临学府」。学府招收拥有「回声」天赋的少年：他们能听见深渊中回荡的远古低语，"
            "以此感知遗迹方位、解读铭文。每届学员必须在三年内完成一次「渊潜」——深入裂缝"
            "带回一件远古遗器——方可毕业。未归者，学府只在名册上画一道墨线，不设衣冠冢。"
        ),
        "geography": (
            "渊临学府：建在悬崖边的石造建筑群，常年笼罩在从裂缝升起的冷雾中。"
            "回声塔：学府最高处，学员在此静坐聆听深渊低语，训练定位能力。"
            "第一层·苔壁走廊：裂缝入口往下百米，布满磷光苔藓，是新手训练场。"
            "第三层·骨桥：横跨地下暗河的巨兽脊骨，两侧是黑暗，坠落即死。"
            "第七层·沉默图书馆：一座倒悬的远古建筑，内有大量铭文石板，但空气中弥漫着"
            "让人遗忘语言的迷雾。深渊之眼：裂缝最深处已知的极限，据传那里有一扇从未被打开的门。"
        ),
        "culture": (
            "学员按「渊潜」深度分级：苔阶（第1-2层）、骨阶（第3-4层）、墨阶（第5层以下）。"
            "墨阶学员极少，且多数性情古怪，被称为「半个深渊人」。学府铁律：禁止独自渊潜，"
            "至少三人结队。但私下里，以独自渊潜为最高荣耀。回声天赋用多了会侵蚀记忆——"
            "学员们管这叫「褪色」，严重者会忘掉自己的名字。教官中流传一句话：深渊不杀人，"
            "是人自己走进去不肯回头。"
        ),
        "physicsRules": [
            "回声天赋每次使用都会消耗一段记忆——越深层的感知，消耗越珍贵的记忆。",
            "深渊中的远古遗器一旦离开裂缝超过七日，就会变成普通石头，除非用回声者的记忆「喂养」。",
            "裂缝越深，时间流速越慢——第七层待一天，外面过去一周。",
            "深渊低语能被回声者听见，但听太久会被「同化」——开始无意识地往深处走。",
        ],
        "protagonistWant": (
            "苔阶学员陆沉发现自己的回声天赋与众不同——他听到的不是远古低语，而是一个女人的声音，"
            "在反复说同一句话：「别下来找我。」他认出那是三年前独自渊潜后失踪的姐姐陆鸢的声音。"
            "他要找到姐姐，活着把她带回来。"
        ),
        "theme": "记忆是人之为人的锚，为了不忘记重要的人，你愿意用什么去交换？",
        "candidateEndings": [
            {"id": "end_return", "summary": (
                "陆沉在第七层找到了姐姐——她已经「褪色」到只记得一句话和一个模糊的脸。"
                "他用自己关于姐姐的全部记忆「喂养」深渊，换来姐姐片刻的清醒。"
                "姐姐认出了他，说了他的名字，然后推他离开。他回到地面，"
                "不记得自己为什么哭，只知道手里握着一枚陌生的骨质发簪。"),
             "themeExpression": "有些记忆消失了，爱还留在身体里。",
             "requiredConditions": ["陆沉到达第七层", "陆沉找到陆鸢"], "activeWeight": 0.55},
            {"id": "end_stay", "summary": (
                "陆沉在深渊之眼前找到了那扇门，发现门后是所有被深渊吞噬的回声者的记忆汇聚之地。"
                "姐姐就在其中，但她已经成为深渊的一部分。陆沉选择留下，用自己的回声天赋"
                "为所有失落者守住最后一丝「自己是谁」的记忆。他成了新的深渊低语。"),
             "themeExpression": "最深的记忆不是关于自己的，而是关于你不肯放手的那个人。",
             "requiredConditions": ["陆沉到达深渊之眼", "陆沉了解深渊真相"], "activeWeight": 0.45},
        ],
    },
    "personas": [
        {"id": "p_luchen", "name": "陆沉", "gender": "男",
         "want": "找到三年前失踪的姐姐陆鸢，活着带她回来",
         "values": [{"name": "亲情", "weight": 0.9}, {"name": "不愿遗忘", "weight": 0.8}],
         "fatalFlaw": "为了姐姐可以不顾一切，包括队友的安全和自己的记忆",
         "obstacles": ["苔阶实力不足以深入", "回声天赋的代价", "学府禁止私自渊潜"],
         "costThreshold": "愿意付出一切记忆，但害怕忘记姐姐的脸——那是他最后的底线",
         "voice": "沉默寡言，但一开口就直指要害，不兜圈子",
         "mannerisms": ["不自觉地摸右耳——姐姐以前总揪他耳朵", "记事全靠一本写满细节的旧笔记"],
         "motifObjects": ["obj_hairpin"], "arcState": "", "costLedger": []},
        {"id": "p_wenqing", "name": "温晴", "gender": "女",
         "want": "成为第一个到达深渊之眼并活着回来的墨阶探索者，洗刷家族的耻辱",
         "values": [{"name": "证明自己", "weight": 0.85}, {"name": "理性", "weight": 0.7}],
         "fatalFlaw": "把一切都当成可计算的成本，直到发现有些东西算不清",
         "obstacles": ["家族被逐出学府的历史", "对队友的不信任", "深渊第五层以下的未知"],
         "costThreshold": "可以牺牲记忆、名声甚至队友的信任，但无法接受自己变成一个「算不清账」的人",
         "voice": "冷静、精确，像在写实验报告，但偶尔会冒出一句刻薄的俏皮话",
         "mannerisms": ["随身带着一本记录褪色程度的小册子", "紧张时咬笔帽"],
         "motifObjects": ["obj_notebook"], "arcState": "", "costLedger": []},
        {"id": "p_guyan", "name": "顾砚", "gender": "男",
         "want": "守住教官的职责——不再让任何学员死在深渊里",
         "values": [{"name": "责任", "weight": 0.9}, {"name": "赎罪", "weight": 0.75}],
         "fatalFlaw": "十年前他从第七层独自回来，搭档没有——他至今不肯说那一层发生了什么",
         "obstacles": ["学府高层隐瞒的真相", "自己严重褪色的记忆", "陆沉让他想起当年的搭档"],
         "costThreshold": "宁愿再下一次深渊也不愿说出真相，因为真相会摧毁学员们最后的信念",
         "voice": "温和但疏离，从不谈过去，转移话题的技巧炉火纯青",
         "mannerisms": ["讲课时总站在窗边看裂缝", "左手无名指上有一道环形旧伤"],
         "motifObjects": ["obj_ring_scar"], "arcState": "", "costLedger": []},
    ],
}


def main():
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    pid = f"proj_{uuid.uuid4().hex[:8]}"
    db_path = str(PROJECTS_DIR / f"{pid}.db")
    wb = DRAFT["worldBible"]
    t0 = time.time()

    repo = seedbuilder.build_repo_from_draft(DRAFT, db_path=db_path)
    LLM = LoggingLLMClient(_RAW_LLM, repo.conn, caller="seed_abyss")
    print(f"[1] 项目 {pid} DB 落地 → {db_path}")

    build_tone_profile(repo, llm=LLM, genre=wb["genre"], theme=wb["theme"],
                       setting_hint=wb["settingCore"][:200])
    ensure_cards_for_personas(repo)
    cast_named_characters(repo, "\n".join([wb["settingCore"], wb["geography"], wb["culture"]]),
                          wb["protagonistWant"], llm=LLM)
    infer_and_store_genders(repo, llm=LLM)
    lock_motif_canon(repo, llm=LLM)
    lock_aliases(repo, wb.get('protagonistWant', ''), wb.get('settingCore', ''), wb.get('theme', ''))
    lock_hidden_identities(repo, llm=LLM)
    print(f"[1a] 角色基础完成")

    build_world_skill(repo, llm=LLM, theme=wb.get('theme', ''))
    canon = lock_canonical_geography(repo, llm=LLM)
    print(f"[1b] W1+W0 完成，canon 地名 {len(canon)} 个")

    geo_stat = build_geography(repo, llm=LLM, theme=wb.get('theme', ''))
    print(f"[1c] W2 地理：{geo_stat}")

    fac_stat = build_factions(repo, llm=LLM, theme=wb.get('theme', ''))
    print(f"[1d] W3 势力：{fac_stat}")

    char_stat = enrich_character_cards(repo, llm=LLM, theme=wb.get('theme', ''))
    print(f"[1e] W4 人物：{char_stat}")

    graph_stat = build_static_graph(repo)
    print(f"[1f] W5 静态边：{graph_stat}")

    planner = Planner(repo, llm=LLM, theme=wb["theme"],
                      worldsmith=WorldSmith(repo, llm=LLM, theme=wb["theme"]))
    planner.build_master(part_count=2, arcs_per_part=1, chapter_scenes=3)
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
            try:
                planner.name_chapter(step.chapter_id)
            except Exception:
                pass
            print(f"    第{ch.sequence_order if ch else '?'}章 done（{len(repo.list_scenes())} 场）")
            if done >= N:
                break
    repo.conn.commit()
    print(f"[3] 写满 {done} 章 / {len(repo.list_scenes())} 场，用时 {time.time()-t0:.0f}s")

    now = datetime.now(timezone.utc).isoformat()
    snap = {"id": pid, "title": "深渊学院", "status": "writing",
            "createdAt": now, "updatedAt": now, "chat": [], "draft": DRAFT}
    snaps = []
    if INDEX_PATH.exists():
        try:
            snaps = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
        except Exception:
            snaps = []
    snaps.append(snap)
    INDEX_PATH.write_text(json.dumps(snaps, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[4] 已注册「深渊学院」({pid})。重启后端后前端可见。")


if __name__ == "__main__":
    main()
