# -*- coding: utf-8 -*-
"""把谍战《孤岛》种子通过 server API 建成真正的项目，使其出现在前端。
含本轮所有修复（gender 字段、缺席人物、调性约束、各级去重）。

用法：确保后端在 :8000 运行，然后 python scripts/seed_to_server.py
lock 会触发全量章纲生成（~10min+），HTTP 可能超时但服务端 worker 会跑完。
"""
import json
import sys
import time
import urllib.request

BASE = "http://localhost:8000/api"
BUILD_TIMEOUT_SECONDS = 1800


def call(method, path, body=None, timeout=120):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


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
         "motifObjects": ["钢笔"], "arcState": "", "costLedger": []},
        {"id": "p_zhaojiu", "name": "赵九", "gender": "男", "want": "在三方夹缝里替自己挣一条活路，谁给的筹码大就替谁办事",
         "values": [{"name": "自保", "weight": 0.9}, {"name": "江湖义气", "weight": 0.45}],
         "fatalFlaw": "贪小利、好赌，关键时刻总被一笔横财或一条把柄牵着鼻子走",
         "obstacles": ["军统的把柄攥在别人手里", "七十六号的监视", "欠下的赌债"],
         "costThreshold": "什么都能卖，唯独不肯卖了自己的命；可一旦赌红了眼，连这条底线也守不住",
         "voice": "油滑、爱插科打诨，话里三分真七分诨，越紧张越贫嘴",
         "mannerisms": ["搓手指像在数钱", "笑的时候眼睛不笑"],
         "motifObjects": ["骰子"], "arcState": "", "costLedger": []},
        {"id": "p_sujing", "name": "苏静", "gender": "女", "want": "查出谁是潜伏的地下党，向七十六号请功上位，洗掉自己的旧档案",
         "values": [{"name": "向上爬", "weight": 0.85}, {"name": "不留活口的谨慎", "weight": 0.6}],
         "fatalFlaw": "疑心病重到容不得任何破绽，反而常因为追查得太急、太狠而打草惊蛇",
         "obstacles": ["自己也有一段见不得光的旧历", "上司的猜忌", "查不到实证的潜伏者"],
         "costThreshold": "可以牺牲任何同僚换取上位，但绝不让自己的旧档案见光",
         "voice": "冷、慢、每句话都像在审讯，笑里带刀",
         "mannerisms": ["说话时盯着对方的手而非眼睛", "用指甲轻刮茶杯沿"],
         "motifObjects": ["档案袋"], "arcState": "", "costLedger": []},
    ],
}


def main():
    pid = call("POST", "/projects", {"title": "孤岛"})["id"]
    print(f"[1] 建项目 {pid}（前端刷新即可看到「孤岛」）")
    call("PUT", f"/projects/{pid}/seed/draft", DRAFT)
    draft = call("GET", f"/projects/{pid}/seed/draft")
    ready = draft["completeness"]["ready"]
    print(f"[2] 草稿就绪 ready={ready}")
    if not ready:
        print("   缺项：", [c["label"] for c in draft["completeness"]["checklist"] if not c["done"]])
        sys.exit(1)
    print("[3] 锁定并生成（最多等待 30min；中断后可按构建断点续跑）…")
    t0 = time.time()
    try:
        call("POST", f"/projects/{pid}/seed/lock", timeout=BUILD_TIMEOUT_SECONDS)
        print(f"   锁定完成，用时 {time.time()-t0:.0f}s")
    except Exception as e:
        print(f"   HTTP 超时/中断（{e}）——服务端 worker 仍在后台生成，稍后刷新前端即可。")
    print(f"\n完成。打开 http://localhost:5173 → 选「孤岛」查看大纲与正文（项目 {pid}）。")


if __name__ == "__main__":
    main()
