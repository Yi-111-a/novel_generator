"""初始化一个最小修真世界：world_bible + entities + 一个 persona + 初始 facts/账本。

供 demo 与测试复用。返回 Repository。
"""
from __future__ import annotations

import sqlite3

from . import db
from .models import Entity, Fact, KnowledgeItem, Persona, Thread
from .repository import Repository

PROTAGONIST_ID = "char_lin"
SENIOR_ID = "char_senior"
THEME = "真相的代价"


def seed(db_path: str = ":memory:") -> Repository:
    conn: sqlite3.Connection = db.connect(db_path)
    repo = Repository(conn)

    # 世界圣经：不可变层含物理法则（校验层据此拦截"用手机"等）
    repo.set_world_bible(
        setting_core="青冥修真界。灵气为尊，宗门林立。",
        physics_rules=[
            "修真世界没有手机、电话、电、互联网",
            "凡人没有飞行能力",
        ],
        protagonist_want="为枉死的师父查明真相",
        theme="真相的代价",
    )

    # 实体
    entities = [
        Entity(PROTAGONIST_ID, "character", "林晚", {"境界": "筑基初期"}),
        Entity("char_master", "character", "云子虚（师父）", {"状态": "已逝"}),
        Entity("char_senior", "character", "师兄·秦松", {"境界": "金丹"}),
        Entity("loc_qingming", "location", "青冥宗·后山药园", {}),
        Entity("obj_jade", "object", "师父的半枚玉佩", {}),
    ]
    for e in entities:
        repo.insert_entity(e)

    # persona
    repo.insert_persona(
        Persona(
            agent_id=PROTAGONIST_ID,
            name="林晚",
            want="为枉死的师父查明真相",
            values=[{"name": "对师父的忠义", "weight": 0.9}, {"name": "明哲保身", "weight": 0.4}],
            fatal_flaw="冲动，认定的事不计后果",
            obstacles=["人微言轻", "真凶身居高位"],
            voice="话不多，问到痛处才开口",
            mannerisms=["攥紧那半枚玉佩", "垂眼不与人对视"],
        )
    )

    # 初始 facts（canonical 真相）+ 写入主角账本
    seed_facts = [
        Fact(
            "fact_master_dead",
            "event",
            "云子虚在后山药园暴毙，宗门对外称'走火入魔'。",
            story_time=0,
            location_id="loc_qingming",
            involved_entities=["char_master", "loc_qingming"],
        ),
        Fact(
            "fact_jade_half",
            "state",
            "师父临终把半枚玉佩塞给林晚，另一半下落不明。",
            story_time=0,
            involved_entities=[PROTAGONIST_ID, "obj_jade", "char_master"],
        ),
    ]
    for f in seed_facts:
        repo.append_fact(f)
        repo.insert_knowledge(
            KnowledgeItem(
                agent_id=PROTAGONIST_ID,
                fact_id=f.fact_id,
                version_content=f.canonical_content,
                confidence=1.0,
                learned_tick=0,
            )
        )

    # 一条"隔离对照组"真相：存在于世界库，但**不**在林晚账本里
    # —— 若林晚的动作引用了它，校验层应判 unauthorized_fact。
    repo.append_fact(
        Fact(
            "fact_secret_killer",
            "event",
            "真凶是师兄秦松，他偷走了另半枚玉佩。",
            story_time=0,
            involved_entities=["char_senior", "obj_jade"],
        )
    )

    return repo


def seed_m2(db_path: str = ":memory:") -> Repository:
    """M2 种子：在 M1 世界上加多角色 + 故事线 + 分布在不同账本里的情报。

    用于演示：信息传播（含扭曲）、内在冲突生成器、导演循环、conflict_pairs。
    """
    repo = seed(db_path)

    # 师兄·秦松：真凶，怯懦，想掩盖真相
    repo.insert_persona(
        Persona(
            agent_id=SENIOR_ID,
            name="秦松",
            want="掩盖自己与师父之死的关联",
            values=[{"name": "前程功名", "weight": 0.8}, {"name": "对林晚的旧情", "weight": 0.5}],
            fatal_flaw="怯懦，遇事先想自保",
            obstacles=["林晚步步追问"],
            cost_threshold={"max": "可弃旧情，不可弃前程"},
            voice="滴水不漏，惯用反问",
            mannerisms=["摩挲剑柄"],
        )
    )

    # 给林晚补一对"会相撞"的价值（忠义 vs 明哲保身已在 M1 persona），
    # 并标注弧线起点，供监控与写回使用。
    lin = repo.get_persona(PROTAGONIST_ID)
    assert lin is not None
    lin.cost_threshold = {"max": "可舍命，不可舍真相"}
    lin.arc_state = {"last_change_tick": 0, "last_flaw_cost_tick": 0, "changed": False}
    repo.insert_persona(lin)
    senior = repo.get_persona(SENIOR_ID)
    assert senior is not None
    senior.arc_state = {"last_change_tick": 0, "last_flaw_cost_tick": 0, "changed": False}
    repo.insert_persona(senior)

    # 故事线
    repo.insert_thread(
        Thread(
            thread_id="thread_main",
            central_question="师父之死的真相，林晚查得到吗？",
            involved_agents=[PROTAGONIST_ID, SENIOR_ID],
            priority_weight=0.9,
            current_tension=0.2,
        )
    )
    repo.insert_thread(
        Thread(
            thread_id="thread_side",
            central_question="秦松能否守住前程？",
            involved_agents=[SENIOR_ID],
            priority_weight=0.4,
            current_tension=0.1,
        )
    )

    # 一条只在秦松账本里的真相 → 供"二手转述会扭曲"的演示
    repo.append_fact(
        Fact(
            "fact_jade_location",
            "state",
            "另半枚玉佩就锁在秦松的剑匣里。",
            story_time=0,
            involved_entities=[SENIOR_ID, "obj_jade"],
        )
    )
    repo.insert_knowledge(
        KnowledgeItem(
            agent_id=SENIOR_ID,
            fact_id="fact_jade_location",
            version_content="另半枚玉佩就锁在秦松的剑匣里。",
            confidence=1.0,
            learned_tick=0,
        )
    )

    # 给林晚标注关联意象（用于渲染的母题）
    lin2 = repo.get_persona(PROTAGONIST_ID)
    lin2.motif_objects = ["obj_jade"]
    repo.insert_persona(lin2)

    return repo
