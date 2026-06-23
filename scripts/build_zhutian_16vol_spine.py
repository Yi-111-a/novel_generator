# -*- coding: utf-8 -*-
"""在【新建项目】上覆盖章纲层为诸天售后 §4 16卷脊梁。

用法：python scripts/build_zhutian_16vol_spine.py <proj_id>
（种子世界已由 seed/lock 重生，此脚本只重排卷/arc 结构层）

做的事：
- 清空 parts / arcs / chapter_plans（LLM 自动生成的 5-8 卷结构，丢弃）
- 合同 story_scale -> zhutian(14-18)，volume_blueprint -> §4 16条，active_unit 锁定卷1龙傲天单元
- 建 16 个 part + 每个 2 个 arc 骨架（卷1 arc1=9章 arc2=4章，其余默认6章）
"""
import json
import os
import sqlite3
import sys
import uuid

sys.path.insert(0, os.path.dirname(__file__))
from restructure_zhutian_16vol import VOLUMES, STORY_SCALE  # noqa: E402


def uid(p):
    return f"{p}_{uuid.uuid4().hex[:6]}"


# 卷1 龙傲天单元（arc1）章节阶梯 ch1-9（§5）
ACTIVE_UNIT = {
    "locked": True,
    "name": "赘婿世界·龙傲天「神级医术系统」（首单）",
    "unit_goal": "林默作为诸天售后实习工程师，正面铺足龙傲天靠临时授权装逼打脸岳家，临界点系统当众崩断，林默以售后条款关停结单、拿到首单五星。",
    "core_question": "一个被裁的客服，怎么靠念条款关掉仙帝魔神都得靠的金手指？",
    "forbidden_before_completion": [
        "公司根源/命数收割机真相", "董事会/运营中枢正面展开",
        "Ⅱ段未来世界专有名词", "陌生工号身份被点破",
    ],
    "chapter_steps": [
        "林默失业当晚被诸天售后强制录用，违约即注销的死线压顶（入职）",
        "摊开首张正式工单：赘婿世界龙傲天神级医术系统逾期，系统闪现冻结瞬间（接单）",
        "中转站工位区、KPI/末位淘汰=注销、7天死线，林默准备下副本（工位）",
        "潜入赘婿婚宴，龙傲天拿临时授权碾压岳家、一桌桌打脸（装逼·上）",
        "龙傲天装逼到顶、满堂屏息；检测仪暗扫出系统被魔改外泄（装逼·下+征兆）",
        "临界点：龙傲天装逼到最高潮，系统当众再次崩断、僵在姿势上（打脸最后一刻失败）",
        "后厨杂物间诊断困局：金手指绑定了新娘灵力库（售后·诊断）",
        "互助会帝无忧搅局、争夺这单回收权（售后·搅局）",
        "林默一纸条款漂亮关停、结单，拿到首单五星与基础权限（售后·结单·爽点）",
    ],
    "completion_signal": "龙傲天系统被关停结单、林默拿首单五星；结单余波接到差评相关的诡异工单，钩向卷2差评风暴。",
}

# 各卷 arc 目标章数（默认 [6,6]；卷1 按 §5 = [9,4]）
ARC_CHAPTERS = {1: [9, 4]}


def main():
    pid = sys.argv[1] if len(sys.argv) > 1 else open(
        os.path.join(os.path.dirname(__file__), ".zhutian_new_pid")).read().strip()
    db = os.path.join("server", ".data", "projects", f"{pid}.db")
    assert os.path.exists(db), db
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    # 0) 安全检查：不能有已发布章节
    acc = cur.execute("select count(*) from accepted_chapters").fetchone()[0]
    if acc:
        print(f"ABORT: {acc} accepted chapters exist, refuse to wipe.")
        return

    # 1) 清空章纲层
    for t in ("chapter_plans", "arcs", "parts"):
        cur.execute(f"delete from {t}")
    print("wiped parts/arcs/chapter_plans")

    # 2) 合同：scale + volume_blueprint + active_unit
    row = cur.execute(
        "select id, body_full from world_bible_sections where section='outline_contract' and source='contract' order by id"
    ).fetchall()
    if not row:
        print("ABORT: no outline_contract section yet (build not finished?)")
        return
    row = row[-1]
    contract = json.loads(row["body_full"])
    contract["story_scale"] = STORY_SCALE
    contract["volume_blueprint"] = VOLUMES
    contract["active_unit"] = ACTIVE_UNIT
    cur.execute("update world_bible_sections set body_full=? where id=?",
                (json.dumps(contract, ensure_ascii=False, indent=2), row["id"]))
    print("contract updated: scale=zhutian(14-18), blueprint=16, active_unit locked(vol1)")

    # 3) 建 16 part + 32 arc 骨架
    for seq in range(1, 17):
        v = VOLUMES[seq - 1]
        pid_part = uid("part")
        region = "本卷世界名到本卷再正式命名落库" if 4 <= seq <= 8 else ""
        status = "active" if seq == 1 else "planned"
        cur.execute(
            "insert into parts (part_id, sequence_order, title, goal, region, key_twist, new_crisis_hook, reveal_node_ids, status)"
            " values (?,?,?,?,?,?,?,?,?)",
            (pid_part, seq, v["title"], v["short_goal"], region, v["key_twist"], v["gain_and_hook"], "[]", status),
        )
        chs = ARC_CHAPTERS.get(seq, [6, 6])
        for a in (1, 2):
            cur.execute(
                "insert into arcs (arc_id, part_id, sequence_order, title, summary, target_chapters, focus_agents, status)"
                " values (?,?,?,?,?,?,?,?)",
                (uid("arc"), pid_part, a, f"{v['title']}·其{a}", v["short_goal"], chs[a - 1], "[]",
                 "active" if seq == 1 and a == 1 else "planned"),
            )
    con.commit()

    # 验证
    ac = con.cursor()
    print("\n=== 验证 ===")
    for r in cur.execute("select part_id,sequence_order,title,status from parts order by sequence_order"):
        arcs = ac.execute("select target_chapters from arcs where part_id=? order by sequence_order", (r["part_id"],)).fetchall()
        print(f"  卷{r['sequence_order']:>2} {r['title']:<14}[{r['status']}] arcs={[a['target_chapters'] for a in arcs]}")
    print("parts:", cur.execute("select count(*) from parts").fetchone()[0],
          "arcs:", cur.execute("select count(*) from arcs").fetchone()[0])
    con.close()


if __name__ == "__main__":
    main()
