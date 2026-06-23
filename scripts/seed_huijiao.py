# -*- coding: utf-8 -*-
"""一键建项目 →《灰礁灯塔》哥特悬疑·恐怖 → 锁定 → 打印新功能验证摘要。

直接构造完整种子草稿（绕开逐轮聊天），PUT 后 lock，验证：
  文风契约(tone_profile) / World Bible 全量 / 揭示链多节点 / 地点一等实体 /
  全量章纲(戏剧问题去重·章末钩子·道具台账·provisional)。
"""
import json
import sys
import time
import urllib.request

BASE = "http://localhost:8000/api"
BUILD_TIMEOUT_SECONDS = 1800


def call(method, path, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=BUILD_TIMEOUT_SECONDS) as r:
        return json.loads(r.read().decode("utf-8"))


DRAFT = {
    "worldBible": {
        "genre": "horror",
        "settingCore": (
            "北境雾港「灰礁」，终年浓雾不散。每隔七年，便有一人在雾起之夜无故失踪，"
            "尸骨无存；港里人讳莫如深，称之为「还潮」。灯塔孤悬礁上，守塔人代代相替，"
            "却没人活着说清雾里到底有什么。"
        ),
        "geography": (
            "灰礁港：背山面海的渔镇，石屋依崖而建，唯一码头常年泊着归不了航的破船。"
            "灰礁灯塔：港外三里礁石上的孤塔，铁梯七十二级，塔顶棱镜终年被盐雾蚀得发白。"
            "夜莺号残骸海域：四十年前沉没的客轮，退潮时桅杆刺出水面，渔民绕行不入。"
            "礁背渔村：港背阴面的小村，与港镇隔一道断崖，靠一条潮汐栈道相连，涨潮即断。"
            "圣婴堂地窖：港镇老教堂的地下，藏着历年「还潮」失踪者的名册与一口铜钟。"
        ),
        "culture": (
            "渔民信奉「潮神」，忌在雾夜点灯、忌直呼失踪者之名、忌打捞夜莺号的遗物。"
            "教会与渔民暗中对立：神父收着名册却不肯公开，渔民信旧俗而排外。外来者一律不受欢迎。"
        ),
        "physicsRules": [
            "雾起之时罗盘失灵，方向感尽失，离塔光超过三十步即可能迷失。",
            "海底的钟声只有将死或已被「标记」之人能听见。",
            "「还潮」带走的人，七年后会以另一副面孔、另一个名字回到港里，自己也未必记得。",
            "灯塔的光一旦熄灭，雾会越过礁线漫上岸。",
        ],
        "protagonistWant": "守塔人沈砚要查清七年前那个雾夜，妹妹沈晚究竟是怎么消失的。",
        "theme": "在浓雾与沉默里，真相的代价是认出——自己也是让她消失的共谋之一。",
        "candidateEndings": [
            {"id": "end_truth", "summary": "沈砚撞破名册与铜钟的秘密，认出「林晚」就是七年前还潮归来的妹妹，"
             "却也认出当年自己为保灯塔默许了献祭。他敲响铜钟，以自己换她出雾。",
             "themeExpression": "知道真相的人必须付出与真相等重的代价。",
             "requiredConditions": ["沈砚获知献祭真相", "沈砚认出林晚身份"], "activeWeight": 0.6},
            {"id": "end_loop", "summary": "沈砚选择重燃灯塔、封口名册，让港镇照旧运转。雾散了，"
             "但他从此能听见海底的钟声——下一个七年，轮到他。",
             "themeExpression": "沉默能保住小镇，却保不住敲钟的人。",
             "requiredConditions": ["沈砚封存名册", "灯塔重燃"], "activeWeight": 0.4},
        ],
    },
    "personas": [
        {"id": "p_shenyan", "name": "沈砚", "want": "查清妹妹沈晚七年前失踪的真相",
         "values": [{"name": "守诺", "weight": 0.8}, {"name": "赎罪", "weight": 0.7}],
         "fatalFlaw": "为守住灯塔与小镇，他习惯性地把疑问咽回去、把灯重新点亮",
         "obstacles": ["港里人的集体沉默", "神父扣着的名册", "雾里听不清的钟声"],
         "costThreshold": "愿以性命换妹妹出雾，但迟迟不敢承认自己当年的默许",
         "voice": "话少、克制，问句多于陈述，像随时怕惊动什么",
         "mannerisms": ["摩挲塔灯的铜钥", "在开口前先望一眼海面"],
         "motifObjects": ["obj_brass_key"], "arcState": "", "costLedger": []},
        {"id": "p_chen", "name": "陈阿公", "want": "把「还潮」的旧俗连同秘密一起带进棺材",
         "values": [{"name": "护乡", "weight": 0.9}, {"name": "守旧", "weight": 0.6}],
         "fatalFlaw": "深信牺牲少数能保全全港，于是对每一次献祭都视而不见",
         "obstacles": ["年迈与悔意", "沈砚的追问", "神父的名册"],
         "costThreshold": "宁可背负骂名也不肯说破，除非亲眼见到沈砚重蹈覆辙",
         "voice": "苍老、绕弯子，爱用渔谚把话头岔开",
         "mannerisms": ["敲烟斗磕船舷", "提到夜莺号就压低声音"],
         "motifObjects": ["obj_ledger"], "arcState": "", "costLedger": []},
        {"id": "p_linwan", "name": "林晚", "want": "弄清自己是谁、为何对灰礁的一切既陌生又熟悉",
         "values": [{"name": "求真", "weight": 0.8}, {"name": "自保", "weight": 0.5}],
         "fatalFlaw": "用一个借来的名字活着，越接近真相越害怕认出镜中的脸",
         "obstacles": ["残缺的记忆", "港里人异样的目光", "海底越来越清晰的钟声"],
         "costThreshold": "愿冒着想起一切的痛去问到底，哪怕真相会抹掉「林晚」",
         "voice": "轻、试探，常重复别人最后一句话像在确认",
         "mannerisms": ["无意识抚摸锁骨下的旧疤", "听到铃铛声会出神"],
         "motifObjects": ["obj_bell_charm"], "arcState": "", "costLedger": []},
    ],
}


def main():
    pid = call("POST", "/projects", {"title": "灰礁灯塔"})["id"]
    print(f"[1] 建项目 {pid}")

    call("PUT", f"/projects/{pid}/seed/draft", DRAFT)
    draft = call("GET", f"/projects/{pid}/seed/draft")
    ready = draft["completeness"]["ready"]
    print(f"[2] 草稿完成度 ready={ready}：",
          "、".join(f"{c['label']}={'✓' if c['done'] else '✗'}"
                   for c in draft["completeness"]["checklist"]))
    if not ready:
        print("草稿未就绪，终止。"); sys.exit(1)

    print("[3] 锁定并构建世界（生成文风契约/总纲/全量章纲/地点，可能需要一会儿）…")
    t0 = time.time()
    call("POST", f"/projects/{pid}/seed/lock")
    print(f"    锁定完成，用时 {time.time()-t0:.1f}s")

    plan = call("GET", f"/projects/{pid}/plan")
    tp = plan.get("toneProfile") or {}
    print("\n========= 验证摘要 =========")
    print(f"项目：{pid}（在浏览器 http://localhost:5173 → 选「灰礁灯塔」→ 大纲）")
    print(f"\n【§16 文风契约】genre={tp.get('genre')} 主效果={tp.get('primaryEffect')} "
          f"语域={tp.get('register')} 禁忌={tp.get('dictionDont')}")
    print(f"  定调样例：{(tp.get('toneReference') or '')[:60]}")
    print(f"\n【§12 World Bible 全量】分节 {len(plan.get('bibleSections',[]))} 节："
          + "、".join(s['section'] for s in plan.get('bibleSections', [])))
    print(f"\n【§12.3 地点一等实体】共 {len(plan.get('locations',[]))} 个：")
    for l in plan.get("locations", [])[:8]:
        print(f"  - {l['name']}（{l.get('controllingFaction') or '无势力'}）"
              f" 固有道具{len(l.get('notableItems',[]))} | {l['geoFull'][:36]}…")
    rc = plan.get("revealChain", [])
    truths = [n for n in rc if n["kind"] == "truth"]
    subs = [n for n in rc if "副线" in n.get("description", "")]
    print(f"\n【§13.4 揭示链】节点 {len(rc)}（真相 {len(truths)} / 副线 {len(subs)}）")
    chs = sorted(plan.get("chapters", []), key=lambda c: c["sequenceOrder"])
    prov = sum(1 for c in chs if c.get("provisional"))
    print(f"\n【§11 全量章纲】共 {len(chs)} 章（provisional 预演稿 {prov}）。前 6 章：")
    for c in chs[:6]:
        print(f"  第{c['sequenceOrder']}章[{c['role']}] Q:{c.get('dramaticQuestion','')[:24]}"
              f" | 钩子:{(c.get('endingHook') or '')[:22]} | 道具{len(c.get('itemsPresent',[]))}")
    qs = [c.get("dramaticQuestion", "") for c in chs]
    print(f"\n  戏剧问题去重检查：{len(set(qs))}/{len(qs)} 个不同")


if __name__ == "__main__":
    main()
