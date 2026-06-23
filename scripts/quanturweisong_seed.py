"""创建《劝徒为怂》原创项目并直接灌种子锁定。不走 SeedWorkshop 共创。"""
from __future__ import annotations
import json
import sys
import time
import urllib.request

BASE = "http://localhost:8000"


def _req(method: str, path: str, body=None, timeout: int = 600):
    data = None
    headers = {"Content-Type": "application/json"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


SEED = {
    "worldBible": {
        "settingCore": (
            "中古玄幻仙侠世界。九大正派盘踞十州，统称「明序」。"
            "明序之下是「魔修」——多是被正派污名化的散修、出身不洁的修士、犯过禁忌的逃犯；"
            "「正魔之分」实质是「有没有靠山」。"
            "主角原是青衍宗内门弟子，因功法被人栽赃为「沾魔气」逐出师门，下山时几乎饿死。"
            "另有「反派养成器」系统，强制把主角绑定为某位未来魔头的师父，按「黑化值」结算飞升。"
        ),
        "geography": (
            "故事核心在南荒——明序眼里的化外之地。气候湿热、瘴气弥漫、灵气稀薄但杂质多。"
            "核心地点：①漏风崖（主角的破洞修真观，原师门遗忘的偏崖）；"
            "②乌涂泽（魔修聚居的烂泥水寨）；"
            "③七井城（南荒最大集市，正魔暗中交易之所，茶馆苏蘅在此）；"
            "④骨原（更远处荒原，传说葬过上古魔神，徒弟灭门真相之地）；"
            "再北越十州出南荒就是明序九大正派的腹地。"
        ),
        "culture": (
            "修真界讲辈分、宗门、传承。"
            "正派以「剑光朗朗」自居，行事却比魔修更阴——内斗、栽赃、灭口皆有。"
            "魔修反而讲江湖义气，因为他们没有宗门保护，只能靠口碑活下去。"
            "市井气浓重：茶馆、戏台、跑商、镖局俱全。"
            "七井城是正魔暗中通气的口岸；南荒口音轻、爱用「那个」「我觉得吧」开头打圆场。"
        ),
        "physicsRules": [
            "修真分九境，主角凝气境（最低）。功法纯度决定上限。",
            "「怂功」（出自禁书《九返长生录》）可无穷续命——主角偏偏只会这种。",
            "「反派养成器」是真神物，黑化值真实结算；但进度条机制可被规则漏洞钻——这是全书爽点之源。",
            "死了的人不能复活，无金手指改命。",
            "魔头黑化值满 100% 主角飞升；养成失败则按系统判定给次优奖励。",
        ],
        "protagonistWant": "让自己活到 60 岁、徒弟活到 30 岁，师徒俩在七井城开个茶馆养老。",
        "theme": "真正的强者不是站着打赢的人，是坐着活到最后的人。怂不是弱，是另一种选择。",
        "genre": "玄幻仙侠 / 反传统反派养成 / 爽文+黑色幽默",
        "candidateEndings": [
            {
                "id": "end_a",
                "summary": (
                    "顾长夜在魔头进度条 99% 时主动停下——他想清楚仇恨是父亲种下的。"
                    "系统判定「养成失败」，给主角发了「次优奖励」：不能飞升，但获得自然寿命 +30 年。"
                    "师徒最后在七井城旁开了茶馆，苏蘅入伙，谢君白偶尔下山喝茶。"
                ),
                "themeExpression": "活下去就是最大的胜利",
                "requiredConditions": [
                    "顾长夜揭开父亲灭门的真相",
                    "萧守拙说出禁书《九返长生录》的秘密换徒弟信任",
                    "苏蘅的明序眼线身份被赦免",
                ],
                "activeWeight": 0.45,
            },
            {
                "id": "end_b",
                "summary": (
                    "顾长夜真的失控黑化到 99%，正派围杀。"
                    "萧守拙用禁书功法把所有的魔气转嫁到自己身上替徒弟洗白，"
                    "自己被钉死在骨原的石柱上——但没死，禁书第十一章「千劫不绝」启动。"
                    "徒弟以为他死了，被苏蘅带走。十年后茶馆来了一个白发老者，徒弟没认出他。"
                ),
                "themeExpression": "有些代价你能扛，但扛了就再也不一样",
                "requiredConditions": [
                    "顾长夜的黑化值真的撞到 99%",
                    "萧守拙在徒弟面前公开过《九返长生录》某一招",
                ],
                "activeWeight": 0.30,
            },
            {
                "id": "end_c",
                "summary": (
                    "师徒在结尾发现「反派养成器」不是天道意志，而是上古一位飞升者留下的实验。"
                    "他们决定反向养成：以「故意不黑化」的方式打爆系统，让系统进入死循环。"
                    "系统崩溃那一刻，明序九大正派同时损失了一道隐藏的「正气护宗符」——"
                    "原来正派的「正」也是这个系统给的，明序大乱。"
                ),
                "themeExpression": "所谓正与魔都是系统的题面，写题人是同一个",
                "requiredConditions": [
                    "师徒找到飞升者的遗物",
                    "顾长夜主动配合反向养成",
                    "萧守拙放弃飞升奖励",
                ],
                "activeWeight": 0.25,
            },
        ],
    },
    "personas": [
        {
            "id": "p_xiao",
            "name": "萧守拙",
            "gender": "男",
            "want": "自己活到 60 岁、徒弟活到 30 岁、师徒一起开茶馆养老",
            "values": [
                {"name": "自己的命", "weight": 0.95},
                {"name": "徒弟的命", "weight": 0.85},
                {"name": "一日三餐", "weight": 0.70},
                {"name": "大义", "weight": 0.05},
            ],
            "fatalFlaw": "怂到极致——见血腿软、被骂会哭；但算账极快，永远先算「会不会死」",
            "obstacles": [
                "系统强制绑定，必须把 13 岁孤儿养成「未来毁灭世界的大魔头」",
                "青衍宗弃徒身份背着「沾魔气」污名，被正魔两边都盯着",
                "凝气境（九境最低），打不过任何一个正经修士",
                "徒弟一根筋要复仇，他得反复劝怂",
                "禁书《九返长生录》是真凭据，一旦暴露就是死罪",
            ],
            "costThreshold": "只要不死、不让徒弟死，肉痛、丢面子、被人当怂包嘲笑都可接受；要他赌命换大义，免谈",
            "voice": (
                "低半度的南荒口音，习惯用「那个……我觉得吧……」开口；内心独白极多，"
                "常用打折扣式自嘲（如「主角光环？我连配角光环都不配」）；"
                "对徒弟单独相处时变啰嗦像老母亲；和正派/外人说话永远后退半步、笑着先认怂"
            ),
            "mannerisms": [
                "出门必带三件套：黄符（吓人）、迷烟（跑路）、干粮（吃）",
                "每次打架前先精确报价：「打完这场要花我 12 颗续命丹，徒弟你赔得起吗？」",
                "睡前必盘点账本",
                "听见「飞升」「大义」「为门派」这类词会下意识捂钱袋",
            ],
            "motifObjects": ["obj_huangfu", "obj_miyan", "obj_ganliang", "obj_zhangben"],
            "arcState": "起点是「怂到自卑」，中段被迫站到台前（被徒弟拖出来），终点是「明白怂不是弱、是另一种选择」",
            "costLedger": [],
        },
        {
            "id": "p_gu",
            "name": "顾长夜",
            "gender": "男",
            "want": "成为强者，把当年灭门的「明序」碾碎",
            "values": [
                {"name": "复仇", "weight": 0.80},
                {"name": "师父（嘴上不认）", "weight": 0.85},
                {"name": "力量", "weight": 0.90},
                {"name": "守诺", "weight": 0.60},
            ],
            "fatalFlaw": "强迫症式直性——认准的事一根筋干到底，撞墙不回头；这正是「魔头进度条」飞涨的核心",
            "obstacles": [
                "13 岁孤儿，无修真功底，骨子里却恨意极重",
                "对师父的「怂功」教学全程怀疑、对抗、又依赖",
                "灭门记忆被秘术压住，越渴望复仇内心越虚",
                "黑化值由系统暗中读取，他不知道自己一举一动都在被结算",
            ],
            "costThreshold": "为复仇可舍命，但底线是不愿牵连师父；越靠近真相越怕「自己不该报这个仇」",
            "voice": (
                "13 岁时话少、冷、看人不看眼睛；满口「师父我要——」开头的宣言；"
                "15 岁后开始模仿师父的「那个……我觉得吧……」，每次模仿都被识破"
            ),
            "mannerisms": [
                "写日记记仇账，被师父逼着另开一本记「恩账」",
                "练剑不喊不叫",
                "吃东西极快，因为小时候挨过饿",
                "听到「父亲」二字会瞬间没表情",
            ],
            "motifObjects": ["obj_choujian", "obj_riji_chouzhang", "obj_riji_enzhang"],
            "arcState": "从「复仇机器」→「被师父反复劝怂的别扭少年」→「逐渐明白复仇对象是父亲」→「魔头进度 99% 时主动停下」",
            "costLedger": [],
        },
        {
            "id": "p_su",
            "name": "苏蘅",
            "gender": "女",
            "want": "保住七井城的茶馆和手下八个小厮的命",
            "values": [
                {"name": "手下人的命", "weight": 0.90},
                {"name": "茶馆生意", "weight": 0.80},
                {"name": "见过的世面", "weight": 0.50},
                {"name": "道义", "weight": 0.40},
            ],
            "fatalFlaw": "会心软——每次心软都要花更多钱填坑",
            "obstacles": [
                "明序某派暗插的旧眼线身份未消，旧主早晚要她交差",
                "三年没汇报真情报，已在「叛变」边缘；脱身需要一个干净的机会",
                "茶馆位于正魔暗市枢纽，每周都有正派密探试探",
                "与萧守拙互相欣赏却不能动情——动情就是把对方拖进自己的烂账",
            ],
            "costThreshold": "为了八个小厮的命，茶馆烧了也认；但她不会为「正派的大义」赔命",
            "voice": "南荒口音、算账时声音变高；笑起来眼角有褶；和萧守拙说话像跟同行打趣，从不动情",
            "mannerisms": [
                "一直擦不干净桌子",
                "账本随身",
                "对客人热情、对手下狠",
                "数硬币时下颌微抬",
            ],
            "motifObjects": ["obj_zhangben_su", "obj_chamogun"],
            "arcState": "起点是「藏在茶馆里苟着」，中段被旧主逼到台前，终点是「彻底切割旧身份、入伙师徒茶馆」",
            "costLedger": [],
        },
        {
            "id": "p_xie",
            "name": "谢君白",
            "gender": "男",
            "want": "找到能继承青衍宗道统的人，哪怕这个人是被逐出的萧守拙",
            "values": [
                {"name": "宗门", "weight": 0.90},
                {"name": "师恩", "weight": 0.85},
                {"name": "对错", "weight": 0.60},
            ],
            "fatalFlaw": "太相信「正统」，看不到正统已经烂了",
            "obstacles": [
                "青衍宗当代大师兄身份让他动弹不得",
                "当年逐出萧守拙的决议他投了反对票但没敢说出来——这桩心债压了他六年",
                "宗门高层正在内斗，他被夹在中间",
                "下山打听萧守拙下落，必须避开师叔们的耳目",
            ],
            "costThreshold": "为宗门道统可以下跪；但要他主动陷害无辜，他做不到",
            "voice": "标准官话，文绉绉，每句话像在讲法理；偶尔露出小师弟那种笨拙",
            "mannerisms": [
                "剑不离身但很少出鞘",
                "写信用极工整的小楷",
                "喝茶必先闻三遍",
                "走路总是走在队伍最后压阵",
            ],
            "motifObjects": ["obj_changjian_qingyan", "obj_xiaokai_xinjian"],
            "arcState": "从「以为自己是好人」→「逐渐看见宗门腐败」→「终于明白当年的萧守拙是被栽赃的」→「选择不站队，转身护住小辈」",
            "costLedger": [],
        },
    ],
}


def main():
    print("==> 创建项目 ...", flush=True)
    meta = _req("POST", "/api/projects", {"title": "劝徒为怂", "type": "original"})
    pid = meta["id"]
    print(f"   project_id = {pid}", flush=True)

    print("==> PUT 种子草稿 ...", flush=True)
    _req("PUT", f"/api/projects/{pid}/seed/draft", SEED)
    draft = _req("GET", f"/api/projects/{pid}/seed/draft")
    cl = draft.get("completeness", {})
    print("   completeness:", json.dumps(cl, ensure_ascii=False), flush=True)
    if not cl.get("ready"):
        print("   ❌ 完整性未通过，不能 lock。", flush=True)
        sys.exit(2)

    print("==> POST seed/lock —— 启动 lock_and_build（W0-W5 + build_master + lazy outline）...", flush=True)
    t0 = time.time()
    _req("POST", f"/api/projects/{pid}/seed/lock", {}, timeout=1800)
    print(f"   lock_and_build 完成，耗时 {time.time() - t0:.1f}s", flush=True)

    print("==> 拉一次 plan() 看大纲是否有 parts/arcs/chapters ...", flush=True)
    plan = _req("GET", f"/api/projects/{pid}/plan")
    parts = plan.get("parts") or []
    chapters = plan.get("chapters") or []
    arcs = []
    for p in parts:
        arcs.extend(p.get("arcs") or [])
    print(f"   parts={len(parts)} arcs={len(arcs)} chapters={len(chapters)}", flush=True)
    for p in parts:
        print(f"   · {p.get('partId')} {p.get('title')} arcs={len(p.get('arcs') or [])}", flush=True)
    print(f"\nPROJECT_ID = {pid}")


if __name__ == "__main__":
    main()
