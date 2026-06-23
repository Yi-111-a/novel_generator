# -*- coding: utf-8 -*-
"""亲手排诸天售后【卷1】详细章纲 ch1-13（§5）。

写法纪律（§3）：POV 全程锁林默；reveal_gate 留空（密谋只作征兆，不靠引擎注入）；
target_words=3300-3500；只用世界已落库的人物/地点/势力（互助会搅局用世界自带的
编号007"霸气哥"，不造帝无忧）。arc1=ch1-9，arc2=ch10-13。

用法：python scripts/write_zhutian_vol1_outline.py <proj_id>
幂等：先删卷1两个 arc 的旧 chapter_plans。
"""
import json
import os
import sqlite3
import sys
import uuid

LINMO = "p_linmo"
ZHOUMIN = "p_zhoumin"
AOTIAN = "p_aotian"
SYS00 = "named_06cc8b"      # 系统-00 冷酷上司
LAOZHOU = "named_97be61"    # 老周 技术组长
AQI = "named_b87b5c"        # 阿七 回收专员
BAQIGE = "named_38ac81"     # 编号007"霸气哥" 互助协会领袖·原龙傲天模板持有者

L_CHUZU = "loc_8e9f03"      # 临江市出租屋
L_GONGWEI = "loc_fc4a51"    # 旧工位
L_ZHUANZHAN = "loc_a18c9c"  # 诸天售后·中转站后台
L_DATING = "loc_5f9e1e"     # 数据洪流大厅
L_HUNYAN = "loc_1b27e0"     # 赘婿世界·龙王婚宴
L_KUFANG = "loc_c88401"     # 婚宴库房（后厨杂物间）
L_CANGKU = "loc_5be8b4"     # 回收仓库

ARC1 = None  # filled at runtime (vol1 arc seq1)
ARC2 = None  # vol1 arc seq2


def C(seq, title, cast, locs, beats, summary, dq, hook, exit_state, conflict,
      role, tension, words, hook_type="new_question", resolution_predicate=""):
    return dict(seq=seq, title=title, cast=cast, locs=locs, beats=beats,
                summary=summary, dq=dq, hook=hook, exit_state=exit_state,
                conflict=conflict, role=role, tension=tension, words=words,
                hook_type=hook_type, resolution_predicate=resolution_predicate)


def chapters(arc1, arc2):
    return [
        (arc1, C(1, "末位淘汰", [LINMO, ZHOUMIN], [L_CHUZU],
            ["林默在失业当晚收齐坏消息——按最低赔偿被裁、房东催租，他二十几岁、总在不该较真的地方较真，回到临江市出租屋走投无路，对着空房间念叨赔偿条款里的不合理。",
             "手机自动装上卸不掉的App「诸天售后」，弹出强制录用通知：岗位『诸天售后工程师』，负责维修/升级/强制回收各世界失控或逾期的金手指，违约即注销；周敏以冷主管口吻交代KPI、考勤、末位淘汰=注销。",
             "林默在『拒绝就死』和『上班保命』之间被逼着默认入职、工号激活；他下意识摩挲那张被裁时没还的旧工牌，裂缝里一行陌生工号一闪而过，他没看清。"],
            "失业当晚，林默被诸天售后强制录用，违约即注销的死线把他逼上岗。",
            "林默会不会接下这份『拒绝就被注销』的工作？",
            "App弹窗：工单已派发。目标副本——赘婿世界。倒计时开始。",
            "林默被违约即注销逼着默认入职、工号激活，接到第一张工单。",
            "入职/抉择", "setup", 0.30, 3200, resolution_predicate="decision_made(p_linmo)")),

        (arc1, C(2, "第一张工单", [LINMO, ZHOUMIN, SYS00], [L_GONGWEI, L_ZHUANZHAN],
            ["林默第一次登入诸天售后中转站后台，在落灰的旧工位前摊开正式工单：赘婿世界龙傲天『神级医术系统』逾期三年，需强制回收；系统-00与周敏冷冷交代服务条款与7天死线。",
             "工单附带的系统面板当场闪现一次冻结、僵住的异常画面；系统-00把它归因为『用户违规使用』，林默却觉得这冻结的样子透着不对劲。",
             "林默被要求即刻下副本，临行前在回收仓库领到一套售后工具和一段临时权限——他没有金手指，只有工具和一张嘴。"],
            "林默接下首张正式工单：回收龙傲天那枚逾期三年的神级医术系统。",
            "林默能看懂这枚『逾期系统』到底出了什么问题吗？",
            "传送光幕亮起，目标——龙傲天的赘婿婚宴现场。",
            "林默拿到首单工单与临时权限，准备下赘婿世界副本。",
            "接单/准备", "setup", 0.35, 3200)),

        (arc1, C(3, "工位与看板", [LINMO, ZHOUMIN, LAOZHOU, AQI], [L_ZHUANZHAN, L_DATING, L_CANGKU],
            ["中转站工位区与数据洪流大厅的工单看板铺开：林默看清自己排在末位淘汰边缘，同期老员工老周的好评话术、阿七的回收手段都让他压力倍增。",
             "林默啃龙傲天这单的服务条款，确认逾期三年本可直接回收，但系统状态古怪——绑定异常、隐隐有外泄迹象；他决定亲自下副本查清再结单，而不是无脑回收。",
             "7天死线的钟点压顶，林默带着检测仪与临时权限踏进传送光幕。"],
            "工位看板把末位淘汰压力摊到林默面前，他选择下副本查清再结单。",
            "林默能在7天死线内既保住绩效又把这单的古怪查清吗？",
            "婚宴大厅的喧嚣扑面而来，龙傲天正负手大笑。",
            "林默带着对系统异常的怀疑，踏入赘婿婚宴副本。",
            "准备/潜入", "rising", 0.40, 3300)),

        (arc1, C(4, "龙王归来", [LINMO, AOTIAN], [L_HUNYAN],
            ["林默以杂役身份潜入龙王婚宴，正面看龙傲天掏出银针、负手大笑三声、当众亮明『神级医术系统』持有者身份。",
             "龙傲天用临时授权一桌桌打脸轻慢过他的岳家：当场治好瘫痪的岳父、羞辱连襟，满堂从嗤笑转为屏息——把他装逼正面铺足。",
             "林默在角落用检测仪暗扫，读数异常：这枚系统的授权竟是『临时』的、还带着外泄痕迹；他不动声色记下。"],
            "林默潜入婚宴，正面看龙傲天靠系统碾压岳家、装逼开场。",
            "龙傲天这场装逼，会装到哪一步才露馅？",
            "龙傲天嗓门更高：『你可知我是谁？』——他面板角落一个红字警告一闪即灭。",
            "林默看够龙傲天装逼，并暗测出系统被外泄的征兆。",
            "潜入/观察", "rising", 0.50, 3400)),

        (arc1, C(5, "满堂屏息", [LINMO, AOTIAN], [L_HUNYAN],
            ["龙傲天装逼到顶：当众悬丝诊脉、起死回生，把岳家与宾客踩进尘埃，享受满堂的敬畏与恐惧。",
             "林默的检测仪暗扫出系统被魔改/外泄的确凿征兆——这不是普通逾期，这枚神级医术系统被人动过手脚、绑定了别处的灵力来源（具体绑定到谁，他还没查到）。",
             "林默意识到系统在这种高负载下随时会当众崩断、出大事；他想上前，又被婚宴秩序和自己『没有金手指』的处境卡住。"],
            "龙傲天装逼到最高潮，林默确认系统被魔改、随时会崩。",
            "系统会在什么时候、当着多少人崩断？",
            "龙傲天把系统催到极限的瞬间，面板的红字警告这次没有消失。",
            "林默确认系统被魔改外泄、濒临崩断，却暂时无法出手。",
            "观察/危机迫近", "rising", 0.62, 3400)),

        (arc1, C(6, "当众404", [LINMO, AOTIAN], [L_HUNYAN],
            ["龙傲天在最高潮处催动系统要给岳家『最后一击』，系统却在满堂注视下当众崩断、面板弹出404，他僵在悬针的姿势上、动弹不得（正面渲染翻车名场面）。",
             "满堂从敬畏转为哗然与嘲弄，龙傲天色厉内荏、底牌尽失——他最大的依仗只是个能被当场关停的过期产品。",
             "林默不得不在这一刻现身，以诸天售后工程师身份接管现场，第一次正面与崩断的系统打照面。"],
            "龙傲天装逼到顶时系统当众404崩断，林默被迫现身接管。",
            "系统当众崩断后，谁来收这个烂摊子？",
            "林默亮出工牌：『您好，您的金手指已逾期，我是来做售后的。』",
            "系统当众崩断，林默以售后工程师身份接管龙傲天这单。",
            "翻车/接管", "turn", 0.82, 3500)),

        (arc1, C(7, "后厨杂物间", [LINMO, AOTIAN], [L_KUFANG],
            ["林默把僵住的龙傲天与崩断的系统挪进婚宴库房（后厨杂物间）做售后诊断；龙傲天从嚣张转为色厉内荏地求他别回收。",
             "林默拆解发现死结：这枚系统被魔改后，把新娘的灵力库当成了外挂电源——强行按常规回收会反噬、伤及无辜新娘。",
             "林默卡在『按条款该回收』和『回收会伤人』之间，较真的毛病又上来，他决定找一条不伤新娘的关停路径。"],
            "林默诊断出系统被绑定到新娘灵力库，回收两难。",
            "林默能找到既结单又不伤新娘的关停办法吗？",
            "库房门被人从外面推开——不是岳家，是一群打着横幅的『天命之子』。",
            "林默查明绑定困局，卡在回收与不伤人之间。",
            "诊断/两难", "rising", 0.60, 3400)),

        (arc1, C(8, "拒绝内卷", [LINMO, AOTIAN, BAQIGE], [L_KUFANG, L_HUNYAN],
            ["天命之子互助协会的编号007『霸气哥』带人搅局，打出『拒绝内卷，从回收龙王开始』的横幅，要抢在售后前替会员『反向回收』这枚系统。",
             "编号007自曝是被回收过的前龙傲天模板持有者，对这套系统恨之入骨；他与林默争夺这单处置权，把局面搅成三方僵持。",
             "林默用售后条款逐条拆解『反向回收』在程序上行不通，同时反而摸清了关停系统又不伤新娘的关键——必须走正规售后流程。"],
            "互助协会搅局争回收权，林默反在争执里摸清关停办法。",
            "三方争这枚系统，最后该由谁、按什么规矩处置？",
            "编号007冷笑：『你以为你在修系统？这破公司迟早把你也回收了。』甩下没头没尾一句就退场。",
            "林默压住互助会搅局、确定走正规售后关停路径。",
            "三方争夺", "rising", 0.72, 3400)),

        (arc1, C(9, "一纸条款", [LINMO, AOTIAN], [L_HUNYAN, L_CANGKU],
            ["林默回到婚宴大厅，当着满堂的面以售后工程师身份念出服务条款，一条条把龙傲天的『神级医术』还原成『逾期三年、违规外挂、当事人未续费』的过期产品。",
             "林默用条款解除系统与新娘灵力库的违规绑定、安全关停回收——不伤新娘，龙傲天的底牌当众清零、被打回原形的赘婿（爽点反转）。",
             "工单闭环、首单五星结算，回收的系统模块入回收仓库、攒出一点基础权限；末位淘汰的弦暂时松了，林默长出一口气。"],
            "林默一纸条款当众关停系统、结单五星，完成首单售后反转。",
            "林默能不能干净利落地结掉这第一单？",
            "结算面板弹出一行小字附注，落款是一个他不认识的陌生工号。",
            "首单闭环五星结算，林默拿到基础权限；陌生工号附注浮现。",
            "结单/爽点反转", "climax", 0.85, 3500, resolution_predicate="unit_closed(longaotian)")),

        # ===== arc2 诡异工单 ch10-13 =====
        (arc2, C(10, "陌生工号", [LINMO, ZHOUMIN], [L_ZHUANZHAN, L_GONGWEI],
            ["林默回中转站交单，追问结算附注里那个陌生工号是谁；周敏明显守口，用KPI和流程把话题岔开。",
             "林默在旧工牌的裂缝里再次瞥见同一个陌生工号，心里存下疑问——只作疑问，不点破。",
             "周敏一边敲考勤一边给林默派下一张工单：一张被标了差评、措辞古怪的诡异工单。"],
            "结单余波：陌生工号征兆浮面、周敏守口，新诡异工单派下。",
            "那个反复出现的陌生工号，到底是谁？",
            "工单备注栏只有一句：『客户已死，但差评还在更新。』",
            "林默带着陌生工号的疑问，接下一张诡异差评工单。",
            "余波/埋钩", "resolution", 0.40, 3300)),

        (arc2, C(11, "差评还在更新", [LINMO, ZHOUMIN], [L_ZHUANZHAN, L_DATING],
            ["林默研究这张诡异工单：一个金手指用户的差评在其『已死』之后仍在持续更新，评分被人为压到末位。",
             "林默在数据洪流大厅顺着差评流追，发现这条差评的措辞、节奏不像真用户，倒像被人批量操纵。",
             "他越查越觉得这不是孤例——好几张工单的差评都透着同一股人为的味道。"],
            "林默发现死人差评仍在更新，嗅到差评被批量操纵的气味。",
            "一个死了的用户，差评怎么还在更新？",
            "一条新差评跳出来，矛头直指林默本人：『工程师林默，办事不力，建议回收。』",
            "林默嗅到刷差评的人为痕迹，并被差评反咬。",
            "调查/异常", "setup", 0.50, 3300)),

        (arc2, C(12, "刷差评的人", [LINMO, AQI], [L_ZHUANZHAN, L_CANGKU],
            ["林默向老员工阿七旁敲侧击差评机制，套出『差评能压绩效、能撬动回收权』的潜规则，嗅到一条刷差评产业链的气味。",
             "针对他本人的差评持续叠加，林默的绩效被拖向末位淘汰红线——有人在用差评定向构陷他。",
             "林默决定固定证据、反查差评来源，第一次主动跟这套系统的阴暗面较劲。"],
            "林默套出差评潜规则、被定向刷差评构陷，决意反查源头。",
            "是谁在用差评定向构陷林默？",
            "反查的线索指向一个他暂时够不到的地方——差评的源头在公司内部。",
            "林默坐实刷差评产业链存在，线索指向公司内部。",
            "调查/被构陷", "rising", 0.58, 3400)),

        (arc2, C(13, "风暴前夜", [LINMO, ZHOUMIN], [L_ZHUANZHAN, L_CHUZU],
            ["林默把刷差评产业链的初步证据摆到周敏面前；周敏脸色一变，既不否认也不许他再查，只丢下一句『别碰差评这条线』。",
             "林默意识到这张诡异工单只是冰山一角，一场针对售后工程师的差评风暴正在酝酿，而他已被卷进去。",
             "回到临江市出租屋，林默摩挲旧工牌裂缝里的陌生工号，第一次认真想：自己被裁、被强制入职，会不会本就是被安排的？——只作疑问，停在动作上。"],
            "周敏封口，林默察觉差评风暴将至、自己疑似被安排，卷末起钩。",
            "差评这条线背后，到底藏着多大的东西？",
            "手机一震，新工单与一片新的差评同时弹出——卷2『差评风暴』。",
            "林默被周敏封口，卷末钩向差评风暴；陌生工号疑问加深。",
            "收束/起钩", "turn", 0.70, 3400)),
    ]


def main():
    pid = sys.argv[1] if len(sys.argv) > 1 else open(
        os.path.join(os.path.dirname(__file__), ".zhutian_new_pid")).read().strip()
    db = os.path.join("server", ".data", "projects", f"{pid}.db")
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    arcs = {r["sequence_order"]: r["arc_id"] for r in cur.execute(
        "select a.arc_id,a.sequence_order from arcs a join parts p on a.part_id=p.part_id where p.sequence_order=1 order by a.sequence_order")}
    arc1, arc2 = arcs[1], arcs[2]

    # 幂等：清卷1两个 arc 的旧 chapter_plans
    cur.execute("delete from chapter_plans where arc_id in (?,?)", (arc1, arc2))

    rows = chapters(arc1, arc2)
    for arc_id, c in rows:
        beat_povs = [LINMO] * len(c["beats"])
        cur.execute(
            "insert into chapter_plans (chapter_id, arc_id, sequence_order, title, cast, location_ids,"
            " beat_goals, summary, reveal_gate, target_scenes, role, target_tension, dramatic_question,"
            " resolution_predicate, min_scenes, target_words, ending_hook, hook_type, pov_agent, exit_state,"
            " conflict_type, beat_povs, status, package_version)"
            " values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"ch_{uuid.uuid4().hex[:6]}", arc_id, c["seq"], c["title"],
             json.dumps(c["cast"], ensure_ascii=False), json.dumps(c["locs"], ensure_ascii=False),
             json.dumps(c["beats"], ensure_ascii=False), c["summary"], json.dumps([], ensure_ascii=False),
             len(c["beats"]) + 1, c["role"], c["tension"], c["dq"], c["resolution_predicate"],
             max(2, len(c["beats"])), c["words"], c["hook"], c["hook_type"], LINMO, c["exit_state"],
             c["conflict"], json.dumps(beat_povs, ensure_ascii=False), "planned", 1),
        )
    con.commit()

    print(f"inserted {len(rows)} chapter plans for vol1 (arc1={arc1} ch1-9, arc2={arc2} ch10-13)")
    for r in cur.execute(
        "select cp.sequence_order,cp.title,cp.role,cp.target_tension,cp.target_words,cp.pov_agent"
        " from chapter_plans cp join arcs a on cp.arc_id=a.arc_id join parts p on a.part_id=p.part_id"
        " where p.sequence_order=1 order by cp.sequence_order"):
        print(f"  ch{r['sequence_order']:>2} {r['title']:<10} [{r['role']:<10}] T={r['target_tension']} W={r['target_words']} POV={r['pov_agent']}")
    con.close()


if __name__ == "__main__":
    main()
