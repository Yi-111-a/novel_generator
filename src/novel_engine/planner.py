"""规划器（大纲驱动，P2）。

职责：把"涌现优先"的引擎装上一根叙事脊柱，分三层、滚动生成：

  build_master()      锁定后跑一次：
                        · 拆【揭示链】clue→clue→truth，绑定到各 Part（探索驱动揭示）
                        · 划分【Part】3-5 个，每个生成 region + 一个地点实体
                        · 把每个 persona 的 motif_objects 落入 inventory（物品归属各人物库）
  plan_next_arc()     滚动：为"当前未铺满的 Part"生成下一个【Arc】(5-10章) + 其【逐章计划】
                        · focus_agents 决定本段戏份权重（可主讲配角）
                        · 章计划的 cast/location/items/reveal_gate 全部用 DB 现状做硬约束
  name_chapter()      章写满后据正文取章名（P4 收束时调用）

所有 LLM 产出都会被约束/校验回 DB 现状；无 LLM 或解析失败时走确定性回退（与 worldsmith 一致）。
"""
from __future__ import annotations

import json
import uuid

from .llm.base import LLMClient
from .models import Arc, ChapterPlan, Entity, InventoryItem, Location, Part, Persona, RevealNode
from .narration.retrieval import build_context
from .repository import Repository

DEFAULT_PART_COUNT = 4          # 默认全书部分数（落在 3-5）
DEFAULT_ARCS_PER_PART = 2       # 每个 Part 切几个小部分
DEFAULT_ARC_CHAPTERS = 5        # 每个小部分目标章数（5-10）
DEFAULT_CHAPTER_SCENES = 3      # 每章目标场数（2-4）

# ② 起承转合：role → 目标张力 / 中文名 / 模板目标 / 取名风格
_ROLE_TENSION = {"setup": 0.3, "rising": 0.55, "twist": 0.78, "climax": 0.92, "resolution": 0.4}
_ROLE_CN = {"setup": "起·铺垫", "rising": "承·推进", "twist": "转·逆转", "climax": "合·高潮", "resolution": "余波"}
_ROLE_GOAL = {
    "setup": "铺垫日常与处境，埋下不安的种子",
    "rising": "冲突升级，人物被推得更紧",
    "twist": "揭出意料之外的真相或逆转态势",
    "climax": "逼出非选不可的抉择与代价",
    "resolution": "高潮余波：情感释放与后果显现",
}
_ROLE_QUESTION = {
    "setup": "主角会不会察觉到不对劲？",
    "rising": "这一步会把局面推向何处？",
    "twist": "藏在背后的真相是什么？",
    "climax": "主角最终会做出哪个抉择？",
    "resolution": "代价落定后，还剩下什么？",
}
# §13.3 戏剧问题去重：每个 role 备一组各异的备选问题，命中"与近 6 章雷同"时换一个非雷同的。
# 治根因 A 的"十章问同一句"——中间章被逼着问别的（关系/副线/探索/势力冲突/角色弧某步）。
_QUESTION_BANK = {
    "setup": ["主角会不会察觉到不对劲？", "这处平静底下藏着什么？", "谁在暗中盯着主角？"],
    "rising": ["这一步会把局面推向何处？", "对手的下一手是什么？", "盟友还靠得住吗？",
               "这道难关能不能闯过去？", "谁会先沉不住气？"],
    "twist": ["藏在背后的真相是什么？", "谁才是真正的对手？", "被瞒住的代价有多大？"],
    "climax": ["主角要牺牲掉什么才能赢？", "主角最终会做出哪个抉择？", "这一战谁会付出代价？",
               "守住底线和达成目的，他只能选一个——选哪个？"],
    "resolution": ["代价落定后，还剩下什么？", "这段关系将走向何方？", "新的隐患是否已经埋下？"],
}

# 问题4：每章"出口状态"——本章结束时世界必须发生的、可被叙述/引擎识别的具体变化。
# 引导 agent 朝"做出抉择 / 交换关键物 / 揭开线索"推进，而非原地周旋。
_ROLE_EXIT = {
    "setup": "主角抓到一条具体的、能往下查的线索（一件物、一个地址、一句证词）",
    "rising": "局面被推进一步：一个筹码/把柄/关键物在人物间易手或被暴露",
    "twist": "一条关键真相或某人的真实身份被揭开，认知发生翻转",
    "climax": "一个非做不可的抉择被当场做出，并立即付出代价",
    "resolution": "代价落定、尘埃暂歇，同时一个新的隐患浮出水面",
}

# §13/B1 每章 ≥3 个各异、递进的节拍（离线回退模板；LLM 产具体节拍）。逐场消费（B2）破"一拍演多遍"。
_ROLE_BEATS = {
    "setup": ["交代处境与日常的裂缝", "一个反常细节打破平静", "主角被迫迈出第一步"],
    "rising": ["新的阻力浮现", "一次试探与受挫", "代价抬高、逼近抉择"],
    "twist": ["一处线索动摇既有认知", "真相的一角露出水面", "立场或关系因之翻转"],
    "climax": ["对峙被逼到顶点", "非选不可的抉择落下", "代价当场兑现"],
    # ⑥ 情感滞后（主题6·结构版）：重大冲击的情感不在 climax 当场宣泄，而在余波章一个微小细节处突然崩塌。
    "resolution": ["表面恢复平静、人物反常地冷静处理后事", "一件无关的小事/微小细节突然引爆滞后的情感",
                   "新的隐患浮现，留下一个未解的扣"],
}

# ⑤ 人物弧线阶段（主题7 / ArcANE 心理轴漂移）：按 role 映射主角当前所处的心理弧线阶段。
# 里程碑章（twist/climax）要把主角推入与其一贯姿态相反的抉择，展现心理弹性，而非从头到尾一个样。
_ROLE_ARC = {
    "setup": "起点姿态：仍沿用本性的惯常应对，尚未被真正触动",
    "rising": "被处境推着走，内在矛盾开始累积、旧的应对方式吃力",
    "twist": "信念动摇，一贯的姿态被现实击穿、失效",
    "climax": "被逼做出与一贯姿态**相反**的抉择（弧线转折点），代价沉重",
    "resolution": "新的自我显现，或代价让他再回不到从前的样子",
}

# §13.2 章末钩子：按 role 定钩子类型；钩子必须指向后文（下一章问题/推进中的线）。
_HOOK_TYPE = {
    "setup": "new_question",
    "rising": "new_question",
    "twist": "reversal_tease",
    "climax": "cliffhanger",
    "resolution": "dramatic_irony",
}
_HOOK_CN = {
    "new_question": "新悬念", "reversal_tease": "反转预告",
    "cliffhanger": "悬崖式收尾", "dramatic_irony": "戏剧反讽",
}
# 篇幅瘦身（治"描写过度饱和/5章塞2章量"）：整体下调 ~35%，叙述层再以软上限+留白约束。
_ROLE_WORDS = {"setup": 1400, "rising": 1500, "twist": 1900, "climax": 2100, "resolution": 1300}

_ROLE_TITLE_HINT = {
    "setup": "用一处宁静的环境意象或物件",
    "rising": "用一个推进冲突的动作",
    "twist": "用一个带悬念的问句或反转意象",
    "climax": "用本章冲突最尖锐的焦点物/动作",
    "resolution": "用一个收束、留白的意象",
}


def _uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:6]}"


def _role_curve(n: int, bias: str = "") -> list[tuple[str, float]]:
    """为 n 章排布起承转合，保证至少 1 个 twist + 1 个 climax，并给出张弛曲线。

    §16.3 文风参数化：`bias` 由 tone_profile.tension_curve_bias 归一而来，只改 `rising`
    章的张力形状（里程碑 role 的张力恒定，保证 climax≥setup 等不变量）：
      sawtooth（锯齿/爽文）= 频繁小高潮；ramp（递进/恐怖）= 单调爬升到爆发；
      wave（波浪/喜剧）= 轻缓起伏；默认 = 原有线性递增。
    """
    import math

    roles = ["rising"] * max(1, n)
    roles[0] = "setup"
    climax_i = n - 1 if n <= 3 else n - 2
    roles[climax_i] = "climax"
    if n >= 4:
        roles[n - 1] = "resolution"
    twist_i = max(1, min(climax_i - 1, int(round(n * 0.6))))
    if twist_i == climax_i:
        twist_i = max(1, climax_i - 1)
    roles[twist_i] = "twist"
    out: list[tuple[str, float]] = []
    for i, r in enumerate(roles):
        if r == "rising":
            frac = i / max(1, n - 1)
            if bias == "sawtooth":
                t = (0.45 + 0.25 * frac) + (0.12 if i % 2 else -0.10)
            elif bias == "ramp":
                t = 0.35 + 0.45 * frac
            elif bias == "wave":
                t = 0.5 + 0.16 * math.sin(i * 1.3) + 0.08 * frac
            else:  # 默认：线性递增（与改造前完全一致）
                t = 0.4 + 0.3 * frac
            t = round(min(0.85, max(0.25, t)), 2)  # 钳制在 climax 之下，且 ∈[0,1]
        else:
            t = _ROLE_TENSION[r]
        out.append((r, t))
    return out


# S1 冲突类型池（治"七章同一支舞"：role 管张弛，conflict_type 管冲突"种类"）。
# 依据 DOC/Re3 与"按指定故事弧类型生成"的研究：不显式指定类型，LLM 会退化成单一模式。
_CONFLICT_TYPES = ["潜入任务", "心理博弈", "身份危机", "立场抉择", "正面对峙", "情感羁绊", "三方搅局"]
_CONFLICT_HINT = {
    "潜入任务": "在监视/敌环境下完成一个具体动作（取物/接头/传递），靠隐蔽与机变推进",
    "心理博弈": "两人言语机锋、试探与反试探，靠信息差与心理压迫推进，而非动作",
    "身份危机": "主角的伪装/真实身份濒临暴露，或识破他人真实身份，认知发生动摇",
    "立场抉择": "被逼在两种忠诚/价值/阵营间做出取舍，且代价不可逆",
    "正面对峙": "矛盾摊牌、当面冲突或对峙到顶点，关系就此改变",
    "情感羁绊": "围绕牵挂之人（如林婉）的情感线推进，柔化节奏、深化动机",
    "三方搅局": "第三方势力介入打破二人平衡，局势复杂化",
}
# rising/setup 章轮换用的池（里程碑 role 另有指定类型）
_CONFLICT_ROT = ["潜入任务", "心理博弈", "三方搅局", "立场抉择", "情感羁绊"]


def _conflict_curve(roles: list[str]) -> list[str]:
    """为各章排布**轮换**的冲突类型：里程碑 role 给契合类型，其余轮换；保证相邻章不同类型。"""
    out: list[str] = []
    j = 0
    for r in roles:
        if r == "twist":
            c = "身份危机"
        elif r == "climax":
            c = "正面对峙"
        elif r == "resolution":
            c = "情感羁绊"
        else:
            c = _CONFLICT_ROT[j % len(_CONFLICT_ROT)]
            j += 1
        if out and c == out[-1]:  # 防相邻同类型
            c = _CONFLICT_ROT[j % len(_CONFLICT_ROT)]
            j += 1
            if out and c == out[-1]:
                c = next((x for x in _CONFLICT_TYPES if x != out[-1]), c)
        out.append(c)
    return out


def _norm_tension_bias(raw: str) -> str:
    """tone_profile.tension_curve_bias（自由文本）→ _role_curve 的 bias 关键字。"""
    s = raw or ""
    if "锯齿" in s:
        return "sawtooth"
    if "递进" in s or "爆发" in s:
        return "ramp"
    if "波浪" in s:
        return "wave"
    return ""


class Planner:
    def __init__(self, repo: Repository, llm: LLMClient | None = None, theme: str = "",
                 worldsmith=None) -> None:
        self.repo = repo
        self.llm = llm
        self.theme = theme
        # ④ 角色孵化器：给定 worldsmith 时，新 Arc 开场可按需登场新角色（写进世界+账本+故事线）
        self.worldsmith = worldsmith
        # 群像上限：旧值 4 恰等于"3 主角 + 1 缺席名点人物"的种子规模 → 孵化每次都被堵死，
        # 全书就那几张脸（用户反馈）。提到 8，让每个 Part 能登场 1 个新面孔，壮大群像。
        self.roster_cap = 8

    # ================= 锁定后一次性：总纲 =================
    def build_master(
        self,
        part_count: int | None = None,
        arcs_per_part: int = DEFAULT_ARCS_PER_PART,
        chapter_scenes: int = DEFAULT_CHAPTER_SCENES,
    ) -> dict:
        """生成揭示链 + Part 划分 + 地点 + 库存。幂等：已存在 parts 则跳过。"""
        if self.repo.list_parts():
            return {"skipped": True}

        self.arcs_per_part = max(1, arcs_per_part)
        self.chapter_scenes = min(4, max(2, chapter_scenes))

        personas = self.repo.list_personas()
        # 1) 把每个人物的关联意象落成"持有的物品"（物品归属各人物库）
        self._seed_inventory(personas)
        # ⑤ 给主角排一条弧线阶段序列（存进现成的 arc_state，零 schema），供里程碑章注入"心理弹性"约束。
        self._seed_arc_phases(personas)

        # 2) 揭示链：从"主角尚不知道的真相"拆成 clue→clue→truth
        nodes = self._build_reveal_chain(personas)

        # 3) Part 划分（LLM 拟题/地域，回退确定性），并把揭示链节点摊到各 Part
        n_parts = part_count or self._suggest_part_count()
        n_parts = min(5, max(3, n_parts))
        parts = self._build_parts(n_parts)
        self._assign_nodes_to_parts(parts, nodes)

        if parts:
            self.repo.set_part_status(parts[0].part_id, "active")
        return {
            "parts": [p.part_id for p in parts],
            "reveal_nodes": [n.node_id for n in nodes],
            "arcsPerPart": self.arcs_per_part,
        }

    def _seed_inventory(self, personas: list[Persona]) -> None:
        for p in personas:
            for obj_id in p.motif_objects or []:
                if self.repo.get_inventory_item(obj_id) is None:
                    self.repo.set_inventory(
                        InventoryItem(obj_id, holder_agent_id=p.agent_id, status="held", acquired_chapter=0)
                    )

    def _seed_arc_phases(self, personas: list[Persona]) -> None:
        """⑤ 人物弧线阶段（主题7）：给主角排一条从'本性姿态'到'蜕变'的心理弧线，存入 arc_state。
        默认据 fatal_flaw→want 推一条 5 段弧（确定性，零 LLM）；里程碑章据此把主角推向相反抉择。
        仅作规划元数据（dossier 可展示）；本章具体注入由 _chapter_spec 按 role 映射 _ROLE_ARC。"""
        if not personas:
            return
        hero = personas[0]  # list_personas 按 rowid 排序，personas[0]=主角
        flaw = (hero.fatal_flaw or "自保").strip()[:10]
        want = (hero.want or "想要的东西").strip()[:12]
        phases = [
            f"起点：以{flaw}的惯性应对一切",
            "被迫卷入：旧的应对方式开始吃力",
            f"动摇：{flaw}被现实击穿、不再管用",
            f"蜕变：为了{want}做出与一贯姿态相反的抉择，付出代价",
            "新的自我：再回不到从前的样子",
        ]
        st = dict(hero.arc_state or {})
        st["arc_phases"] = phases
        self.repo.update_arc_state(hero.agent_id, st)

    def _build_reveal_chain(self, personas: list[Persona]) -> list[RevealNode]:
        """§13.4 揭示链强制多节点（治根因 A：单点揭示 = 必然循环）。

        真相来源：①must_resolve 伏笔指向的 fact；②主角(personas[0])不知、他人知的 fact。
        每条核心真相拆成 4 节点：**线索→线索→子真相→核心真相**（探索驱动，逐层揭开）。
        子真相是 clue 节点（fact_id=None），给中间章一个"更深一层"的里程碑而非直接给底。
        此外**强制 ≥1 条副线谜团**：从未握核心真相的配角派生一条次级悬念
        （关系/动机），让中间章有别的问题可问 → 从源头打散"十章问同一句"。
        最后若**主角本人**藏着伪装身份（holder==主角，方向与核心真相相反），
        单独补一条"暴露"揭示线（见 _build_self_reveal_chain）。
        """
        hero = personas[0].agent_id if personas else None
        hero_known = {k.fact_id for k in self.repo.get_agent_ledger(hero)} if hero else set()

        truth_fids: list[str] = []
        for fs in self.repo.list_foreshadows():
            if fs.must_resolve and fs.linked_fact_id not in truth_fids:
                truth_fids.append(fs.linked_fact_id)
        for f in self.repo.list_facts():
            if f.fact_id not in hero_known and f.fact_id not in truth_fids:
                # 只取"有人知道的秘密"，避免把主角自己的前提当谜底
                holders = self.repo.holders_of_fact(f.fact_id)
                if holders and not (len(holders) == 1 and holders[0].agent_id == hero):
                    truth_fids.append(f.fact_id)
        truth_fids = truth_fids[:4]  # 控量：最多 4 条主真相

        nodes: list[RevealNode] = []
        seq = 0
        # 谁握着核心真相 → 这些配角不再单独派生副线（避免与核心线重复）
        truth_holders: set[str] = set()
        for fid in truth_fids:
            truth_holders.update(h.agent_id for h in self.repo.holders_of_fact(fid))

        for fid in truth_fids:
            fact = self.repo.get_fact(fid)
            label = (fact.canonical_content[:14] + "…") if fact else "一桩隐秘"
            prev: str | None = None
            for ci in range(2):  # 两个表层线索
                seq += 1
                node = RevealNode(
                    node_id=_uid("rv"), fact_id=None, kind="clue", sequence_order=seq,
                    prereq_node_ids=[prev] if prev else [],
                    description=f"线索{ci + 1}：关于「{label}」的蛛丝马迹。",
                )
                self.repo.upsert_reveal_node(node)
                nodes.append(node)
                prev = node.node_id
            # 子真相（中间里程碑，仍是 clue：揭开一层但未到底）
            seq += 1
            sub = RevealNode(
                node_id=_uid("rv"), fact_id=None, kind="clue", sequence_order=seq,
                prereq_node_ids=[prev] if prev else [],
                description=f"子真相：关于「{label}」更深一层的端倪初现。",
            )
            self.repo.upsert_reveal_node(sub)
            nodes.append(sub)
            prev = sub.node_id
            # 核心真相（唯一带 fact_id 的 truth 节点 → 可作 reveal_gate）
            seq += 1
            tnode = RevealNode(
                node_id=_uid("rv"), fact_id=fid, kind="truth", sequence_order=seq,
                prereq_node_ids=[prev] if prev else [],
                description=f"核心真相：{label}",
            )
            self.repo.upsert_reveal_node(tnode)
            nodes.append(tnode)

        # ≥1 副线谜团：从配角的欲望/秘密派生（结构性，fact_id=None）。
        # §16.3 文风参数化：complexity=低（爽文，单主线为主）→ 副线少；中/高 → 允许更多副线。
        cap = {"低": 1, "中": 2, "高": 2}.get(
            self.repo.get_tone_profile().complexity.strip(), 2)
        seq = self._build_subplots(personas, hero, truth_holders, nodes, seq, cap)

        # 问题5 补全：主角**自身**的伪装身份是"本人知、他人/读者不知"，与核心真相
        # （主角去发现别人的秘密）语义相反，会被上面的主循环排除（既在 hero_known，
        # 又是 holder==hero 的单持有 fact）→ 永远进不了揭示链，payoff 无受控节点驱动。
        # 这里据 entity.attributes['identity'] 为它单独建一条**暴露**揭示线，挂到最后
        # （索引最大 → _assign_nodes_to_parts 必落末部，对齐高潮）。
        if hero:
            hero_ent = self.repo.get_entity(hero)
            hero_idn = (hero_ent.attributes or {}).get("identity") if hero_ent else None
            if (isinstance(hero_idn, dict) and hero_idn.get("fact_id")
                    and hero_idn.get("true") and hero_idn["fact_id"] not in truth_fids):
                seq = self._build_self_reveal_chain(hero_idn, nodes, seq)
        return nodes

    def _build_self_reveal_chain(self, idn: dict, nodes: list[RevealNode], seq: int) -> int:
        """主角自身伪装身份的『暴露』揭示线（问题5）：线索→线索→暴露(truth)。

        与核心真相方向相反——核心真相是"主角不知→撞破别人的秘密"，这条是"主角已知→
        自己的伪装被他人/读者撞破"。但**机制完全复用**：truth 节点带 identity 的 fact_id，
        在末部里程碑章被选作 reveal_gate；触发时 director._reveal_for_chapter 把该 fact
        推进读者账本（reveal_to_reader），从而解锁 narrator._identity_lines 的称谓闸门
        → 主角真实头衔由受控节点在高潮处揭破，而非凭空点破。clue 节点 fact_id=None，
        不会被选作 gate，只给中间章"他人起疑"的递进料子。"""
        fid = idn["fact_id"]
        true_app = (idn.get("true") or "真实身份").strip()
        pub = (idn.get("public") or "中性身份").strip()
        prev: str | None = None
        for desc in (
            f"暴露线索1：有人开始对「{pub}」的来历起疑，目光多停了一瞬。",
            f"暴露线索2：主角的伪装露出破绽，旁人察觉到不对劲。",
        ):
            seq += 1
            node = RevealNode(
                node_id=_uid("rv"), fact_id=None, kind="clue", sequence_order=seq,
                prereq_node_ids=[prev] if prev else [], description=desc,
            )
            self.repo.upsert_reveal_node(node)
            nodes.append(node)
            prev = node.node_id
        seq += 1
        tnode = RevealNode(
            node_id=_uid("rv"), fact_id=fid, kind="truth", sequence_order=seq,
            prereq_node_ids=[prev] if prev else [],
            description=f"身份暴露：主角的真实身份「{true_app}」被当众揭破。",
        )
        self.repo.upsert_reveal_node(tnode)
        nodes.append(tnode)
        return seq

    def _build_subplots(self, personas, hero, truth_holders, nodes, seq, cap: int = 2) -> int:
        """派生 ≤cap 条副线谜团（每条 线索→揭示 两节点，clue-kind 结构节点）。
        候选 = 非主角、未握核心真相的配角；至少保证生成 1 条（若有任何配角）。"""
        cap = max(1, cap)
        cands = [p for p in personas
                 if p.agent_id != hero and p.agent_id not in truth_holders]
        if not cands and len(personas) > 1:
            cands = [p for p in personas if p.agent_id != hero]  # 退而求其次
        for p in cands[:cap]:
            want = (p.want or "某种执念").strip()[:12]
            prev = None
            for stage, desc in (
                ("clue", f"副线线索：{p.name}的举动里藏着与「{want}」相关的隐情。"),
                ("clue", f"副线揭示：{p.name}的真正动机/牵连浮出水面。"),
            ):
                seq += 1
                node = RevealNode(
                    node_id=_uid("rv"), fact_id=None, kind=stage, sequence_order=seq,
                    prereq_node_ids=[prev] if prev else [], description=desc,
                )
                self.repo.upsert_reveal_node(node)
                nodes.append(node)
                prev = node.node_id
        # ③ 故意留悬一条副线（StoryScope 硬伤二：79% AI 伏笔完美全收 → 机器味）。
        # 额外加 1 个 kind="dangling" 的单节点疑点：永不 discover、永不回收，作为留白存在。
        # fact_id=None（与 clue 节点同构，reveal/director 天然忽略未 discover 的节点，安全）。
        if cands:
            src = cands[cap] if len(cands) > cap else cands[0]
            seq += 1
            dangling = RevealNode(
                node_id=_uid("rv"), fact_id=None, kind="dangling", sequence_order=seq,
                prereq_node_ids=[],
                description=(f"留悬副线（**不回收**）：{src.name}身上有一处始终无人解释的疑点，"
                            f"作为留白贯穿全书，**不给明确答案**——不是所有线索都该被收束。"),
            )
            self.repo.upsert_reveal_node(dangling)
            nodes.append(dangling)
        return seq

    def _suggest_part_count(self) -> int:
        if self.llm is None:
            return DEFAULT_PART_COUNT
        data = self._complete_json(
            f"你为主题「{self.theme}」的长篇小说规划结构。只输出 JSON：{{\"parts\": 整数(3到5)}}",
            "根据题材体量，建议把全书分成几个大部分？只输出 JSON。",
        )
        try:
            return int((data or {}).get("parts", DEFAULT_PART_COUNT))
        except Exception:
            return DEFAULT_PART_COUNT

    def _build_parts(self, n: int) -> list[Part]:
        specs = self._llm_part_specs(n) or self._fallback_part_specs(n)
        parts: list[Part] = []
        for i, s in enumerate(specs, 1):
            pid = _uid("part")
            # ⑥/§12.3 每个 Part 落**多个一等地点实体**（含 geo_full/连通/势力/固有道具）
            self._materialize_part_locations(pid, s)
            part = Part(
                part_id=pid, sequence_order=i,
                title=s.get("title", f"第{i}部"),
                goal=s.get("goal", ""),
                region=s.get("region", ""),
                reveal_node_ids=[], status="planned",
            )
            self.repo.upsert_part(part)
            parts.append(part)
        return parts

    def _canonical_locations(self) -> list[Location]:
        """W0：canonical 地点池——`attributes.canon=True` 的 location 实体对应的 Location 行。
        由 worldbible.lock_canonical_geography 在锁定时固化。供保真闸门选取/校验/兜底。"""
        out: list[Location] = []
        for e in self.repo.list_entities():
            if e.type == "location" and (e.attributes or {}).get("canon"):
                loc = self.repo.get_location(e.entity_id)
                out.append(loc if loc else Location(loc_id=e.entity_id, name=e.name))
        return out

    @staticmethod
    def _name_faithful(name: str, canon_names: list[str]) -> bool:
        """W0 保真判定：地名须忠于 canonical——等于某 canon 名、从属其下（含 canon 名为子串）、
        或为其缩写。镜面塔/遮面街这类与设定无关的奇幻地名将全部不匹配 → 被拒。"""
        nm = (name or "").strip()
        if not nm:
            return False
        for cn in canon_names:
            if nm == cn or cn in nm or nm in cn:
                return True
        return False

    def _fallback_canon_locations(self, canon: list[Location], region: str) -> list[dict]:
        """LLM 反复产出不忠地名时的确定性兜底：直接返回 2-3 个 canon 地点本身，
        优先未被其它 Part 占用、且与本部 region 相关者。"""
        def used(c: Location) -> bool:
            e = self.repo.get_entity(c.loc_id)
            return bool((e.attributes or {}).get("part")) if e else False

        pool = sorted(canon, key=lambda c: (used(c), (region or "") not in (c.geo_full or "")))
        picked = pool[:3] if len(pool) >= 2 else canon[:3]
        return [{"name": c.name,
                 "geo_full": c.geo_full or f"{c.name}：世界观已确立的地点。",
                 "controlling_faction": c.controlling_faction, "notable_items": []}
                for c in picked]

    def _materialize_part_locations(self, part_id: str, part_spec: dict) -> list[str]:
        """§12.3 为一个 Part 落出具体地点实体：location 实体 + locations 行（geo_full/连通/势力/
        固有道具）+ 固有道具 object 实体并入 inventory（无主，归属该地）。返回 loc_id 列表。
        W0：名字恰为某 canon 地点 → **复用**该 canon 实体（指派到本 part），不再新建重复；
        子地点新建时标注从属的 canon 父地点（parent_canon）。"""
        specs = self._part_locations(part_spec)
        canon_by_name = {e.name: e.entity_id for e in self.repo.list_entities()
                         if e.type == "location" and (e.attributes or {}).get("canon")}
        loc_ids: list[str] = []
        for ls in specs:
            name = ls.get("name", "无名之地")
            # W0：复用 canon 实体（霞飞路等权威地点跨 Part 共享，不复制）
            canon_lid = canon_by_name.get(name)
            if canon_lid:
                self.repo.update_entity_attributes(canon_lid, {"part": part_id})
                cloc = self.repo.get_location(canon_lid)
                if cloc:
                    cloc.part_id = part_id
                    if ls.get("geo_full") and len(ls["geo_full"]) > len(cloc.geo_full or ""):
                        cloc.geo_full = ls["geo_full"]
                    if ls.get("controlling_faction") and not cloc.controlling_faction:
                        cloc.controlling_faction = ls["controlling_faction"]
                    self.repo.upsert_location(cloc)
                loc_ids.append(canon_lid)
                continue
            lid = _uid("loc")
            attrs: dict = {"part": part_id}
            parent = next((cid for cn, cid in canon_by_name.items()
                           if name.startswith(cn) and name != cn), "")
            if parent:
                attrs["parent_canon"] = parent
            self.repo.insert_entity(Entity(lid, "location", name, attrs))
            # 固有道具 → object 实体 + inventory（holder=None，note 记归属地）
            item_ids: list[str] = []
            for it_name in ls.get("notable_items", [])[:3]:
                oid = _uid("obj")
                self.repo.insert_entity(Entity(oid, "object", it_name, {"home_loc": lid}))
                self.repo.set_inventory(
                    InventoryItem(oid, holder_agent_id=None, status="held",
                                  acquired_chapter=0, note=f"{name}固有")
                )
                item_ids.append(oid)
            self.repo.upsert_location(Location(
                loc_id=lid, part_id=part_id, name=name,
                geo_full=ls.get("geo_full", ""), connects_to=[],
                controlling_faction=ls.get("controlling_faction", ""),
                notable_items=item_ids,
            ))
            loc_ids.append(lid)
        # 连通拓扑：同部地点串成链（防瞬移；首尾不相连，留一条主通路）
        for a, b in zip(loc_ids, loc_ids[1:]):
            la, lb = self.repo.get_location(a), self.repo.get_location(b)
            if la and lb:
                la.connects_to = sorted(set(la.connects_to + [b]))
                lb.connects_to = sorted(set(lb.connects_to + [a]))
                self.repo.upsert_location(la)
                self.repo.upsert_location(lb)
        return loc_ids

    def _part_locations(self, part_spec: dict) -> list[dict]:
        """为一个 Part 生成 2-3 个具体地点的完整规格（name/geo_full/势力/固有道具）。
        LLM 优先（喂世界圣经地理全文，落真实地点）；失败回退确定性模板。"""
        region = part_spec.get("region", "")
        canon = self._canonical_locations()
        canon_names = [c.name for c in canon]
        if self.llm is not None:
            # W6 RAG：按 canon 地点为种子检索地理+势力子图
            _loc_seeds = {c.loc_id for c in canon} if canon else set()
            geo = build_context(self.repo, _loc_seeds, budget=1800,
                                include_bible_summary=False)
            geo_block = f"世界圣经地理/势力（请落出其中真实存在的地点，勿凭空造）：\n{geo}\n" if geo else ""
            # W0 设定保真闸门：有 canon 池时，只能从中选/细化、子地点须从属，禁止发明冲突奇幻地名。
            canon_block = ""
            if canon_names:
                canon_block = (
                    "【canonical 地点·硬约束】**只能**从以下世界观已确立的地点中选择并细化，"
                    "或新增**从属于**它们的子地点（命名形如『某canon地点·子地点』，"
                    "如『霞飞路·梧桐里咖啡馆』）；**严禁**发明与设定冲突的新地名"
                    "（如设定里并不存在的塔/祠堂/秘境/结界）：" + "、".join(canon_names) + "\n")

            def _deep_enough(d) -> bool:  # §14 深度闸门：≥2 个地点且每个 geo_full 足够具体
                if not isinstance(d, list) or len(d) < 2:
                    return False
                ok = [x for x in d if isinstance(x, dict)
                      and str(x.get("name", "")).strip()
                      and len(str(x.get("geo_full", "")).strip()) >= 12]
                return len(ok) >= 2

            def _ok(d) -> bool:  # 叠加 W0 保真：每个地名都须忠于 canon（有 canon 池时）
                if not _deep_enough(d):
                    return False
                if not canon_names:
                    return True
                return all(self._name_faithful(str(x.get("name", "")).strip(), canon_names)
                           for x in d if isinstance(x, dict) and str(x.get("name", "")).strip())

            data = self._complete_json(
                f"{geo_block}{canon_block}为小说某一部分（地域：{region or '未定'}，目标：{part_spec.get('goal','')}）"
                "落出 2-3 个**具体、各异**的地点。每个给：name(地点名)、"
                "geo_full(完整描写：方位/气候/建筑或地貌/声光气味/危险/通路，≥2 句)、"
                "controlling_faction(所属势力)、notable_items(该地 0-2 个固有道具名)。"
                "只输出 JSON 数组：[{\"name\",\"geo_full\",\"controlling_faction\",\"notable_items\":[]}]",
                "只输出 JSON 数组。", expect_list=True, validate=_ok, retries=1,
            )
            if isinstance(data, list) and data:
                out = []
                for d in data[:3]:
                    if not (isinstance(d, dict) and str(d.get("name", "")).strip()):
                        continue
                    nm = str(d["name"]).strip()
                    # 双保险：漏网的不忠地名直接丢弃（不让它落库）
                    if canon_names and not self._name_faithful(nm, canon_names):
                        continue
                    out.append({
                        "name": nm,
                        "geo_full": str(d.get("geo_full", "")).strip(),
                        "controlling_faction": str(d.get("controlling_faction", "")).strip(),
                        "notable_items": [str(x).strip() for x in (d.get("notable_items") or [])
                                          if str(x).strip()],
                    })
                if out:
                    return out
        # 确定性兜底：有 canon 池则回退到 canon 地点本身（绝不再造奇幻地名）
        if canon_names:
            return self._fallback_canon_locations(canon, region)
        base = region or "无名之地"
        return [
            {"name": f"{base}·其一", "geo_full": f"{base}的一处要地，方位与气候自成一格。",
             "controlling_faction": "", "notable_items": []},
            {"name": f"{base}·其二", "geo_full": f"{base}另一处与前者隔路相望之地。",
             "controlling_faction": "", "notable_items": []},
        ]

    def _assign_nodes_to_parts(self, parts: list[Part], nodes: list[RevealNode]) -> None:
        """把揭示链节点按顺序摊到各 Part（越往后的部分揭越深的真相），并回填 part_id。"""
        if not parts:
            return
        # reveal_node_ids 此前临时存了地点 id；重置为真正的揭示节点，地点改存进 part 自身（用 region 已够）
        buckets: dict[str, list[str]] = {p.part_id: [] for p in parts}
        for idx, node in enumerate(nodes):
            p = parts[min(idx * len(parts) // max(1, len(nodes)), len(parts) - 1)]
            buckets[p.part_id].append(node.node_id)
            node.part_id = p.part_id
            self.repo.upsert_reveal_node(node)
        for p in parts:
            p.reveal_node_ids = buckets[p.part_id]
            self.repo.upsert_part(p)

    # ================= 滚动 + ⑤软节拍：Arc 只建骨架，章节逐章懒生成 =================
    def plan_next_arc(self) -> Arc | None:
        """为"当前尚未铺满的 Part"建下一个 Arc 的**骨架**（标题/梗概/焦点/章数+role 曲线已定，
        但不预先铺满逐章计划）。全部铺满返回 None。具体章节由 next_chapter() 临场生成。"""
        for part in self.repo.list_parts():
            existing = self.repo.list_arcs(part.part_id)
            if len(existing) < getattr(self, "arcs_per_part", DEFAULT_ARCS_PER_PART):
                self.repo.set_part_status(part.part_id, "active")
                arc = self._build_arc(part, len(existing) + 1)
                self._maybe_incubate(part)  # ④ 新 Arc 开场按需孵化新角色（壮大群像）
                return arc
            if part.status != "done":
                self.repo.set_part_status(part.part_id, "done")
        return None

    def _maybe_incubate(self, part: Part) -> None:
        """④ 角色孵化（经 §1 选角层）：群像未满则按功能位登场新角色（持久建卡、绝不重抽），
        并入张力最低的故事线 → 自然参与后续两难。
        P4c 收敛：roster_cap 由 6 降到 4，且每个 Part 至多孵化一个 → 优先复用现有角色，
        从源头抑制"孵化沈伯/沈默/无名客6"式的人物膨胀。"""
        chars = [e for e in self.repo.list_entities() if e.type == "character"]
        if len(chars) >= self.roster_cap:
            return
        from .casting import cast_or_get

        slot = f"incubated_p{part.sequence_order}_{len(chars)}"
        ctx = f"主题：{self.theme}；本部分：{part.title}（{part.goal}）。需要一个能与现有角色形成张力的新面孔。"
        try:
            card = cast_or_get(self.repo, slot, tier="supporting", context=ctx, llm=self.llm)
        except Exception:
            return
        if card.agent_id:
            threads = self.repo.list_threads()
            if threads:
                t = min(threads, key=lambda x: x.current_tension)
                if card.agent_id not in t.involved_agents:
                    t.involved_agents.append(card.agent_id)
                    self.repo.insert_thread(t)

    def _build_arc(self, part: Part, seq: int) -> Arc:
        personas = self.repo.list_personas()
        spec = self._llm_arc_spec(part, seq, personas) or {}
        focus = spec.get("focus_agents") or self._fallback_focus(personas, seq)
        valid_ids = {p.agent_id for p in personas}
        focus = [f for f in focus if f.get("agent_id") in valid_ids] or self._fallback_focus(personas, seq)
        target = int(spec.get("target_chapters", DEFAULT_ARC_CHAPTERS))
        target = min(10, max(5, target))
        summary = spec.get("summary", "")
        geo_hint = self._part_geography_hint(part)
        faction_hint = self._part_faction_hint(part, focus)
        if geo_hint or faction_hint:
            summary = "；".join(x for x in [summary, faction_hint, geo_hint] if x)
        arc = Arc(
            arc_id=_uid("arc"), part_id=part.part_id, sequence_order=seq,
            title=spec.get("title", f"{part.title}·其{seq}"),
            summary=summary,
            target_chapters=target, focus_agents=focus, status="active",
        )
        self.repo.upsert_arc(arc)
        return arc

    # ================= §11 开拍前全量规划（取代⑤懒生成） =================
    def build_all_arcs(self) -> int:
        """为**所有** Part 一次性建满 Arc 骨架（不把 Part 标 done——尚未开演）。
        只把第一部标 active，其余 planned（用于区分 locked / provisional）。返回新建 Arc 数。"""
        made = 0
        per = getattr(self, "arcs_per_part", DEFAULT_ARCS_PER_PART)
        for part in self.repo.list_parts():
            existing = len(self.repo.list_arcs(part.part_id))
            for k in range(existing, per):
                self._build_arc(part, k + 1)
                made += 1
            self._maybe_incubate(part)  # ④ 孵化新角色（出生即建卡）
        parts = self.repo.list_parts()
        if parts:
            self.repo.set_part_status(parts[0].part_id, "active")
        return made

    def build_all_chapters(self) -> int:
        """§11 开拍前生成**全部章节**的完整章纲（章际接钩、戏剧问题去重、道具台账一并落定）。
        用户能一次看到整盘棋；尚未抵达的 Part 的章为 provisional（API 据 part.status 派生），
        演到时再按已发生事实复核。返回新建章数。"""
        made = 0
        for part in self.repo.list_parts():
            for arc in self.repo.list_arcs(part.part_id):
                done = len(self.repo.list_chapter_plans(arc.arc_id))
                for i in range(done, arc.target_chapters):
                    self._generate_chapter(arc, i)
                    made += 1
        return made

    def build_full_outline(self) -> dict:
        """锁定时一次性：全 Arc 骨架 + 全章纲（§11 的对外入口）。"""
        arcs = self.build_all_arcs()
        chapters = self.build_all_chapters()
        return {"arcs": arcs, "chapters": chapters}

    def build_chapters_for_arc(self, arc) -> int:
        """只为某一个 Arc 生成其章纲（惰性大纲最小粒度）。返回新建章数。"""
        made = 0
        done = len(self.repo.list_chapter_plans(arc.arc_id))
        for i in range(done, arc.target_chapters):
            self._generate_chapter(arc, i)
            made += 1
        return made

    def build_chapters_for_part(self, part_id: str) -> int:
        """为某一个 Part 生成其全部 Arc 的章纲。返回新建章数。"""
        made = 0
        for arc in self.repo.list_arcs(part_id):
            made += self.build_chapters_for_arc(arc)
        return made

    def build_lazy_outline(self) -> dict:
        """惰性大纲（治"一直在播种"）：锁定时只生成**总体大纲(全 Arc 骨架) + 第一个 Arc 的章纲**，
        几分钟即可开写。后续章纲懒生成：同部下一 Arc 由导演 `next_chapter()` 边写边补；
        跨部由 `ensure_part_chapters`（演到时）补齐。把"一锤子全量"拆成最小段，开写最快、崩溃只丢一段。"""
        arcs = self.build_all_arcs()
        first_arcs = self.repo.list_arcs(self.repo.list_parts()[0].part_id) if self.repo.list_parts() else []
        chapters = self.build_chapters_for_arc(first_arcs[0]) if first_arcs else 0
        return {"arcs": arcs, "chapters": chapters}

    def ensure_part_chapters(self, part_id: str) -> int:
        """演到某 Part 时若其章纲尚未生成（惰性），则即时生成（用已发生事实，吸收涌现）。
        只生成该部**第一个 Arc**（其余 Arc 由 next_chapter 边写边补），保持"开写快"。幂等。"""
        if any(self.repo.list_chapter_plans(a.arc_id) for a in self.repo.list_arcs(part_id)):
            return 0
        arcs = self.repo.list_arcs(part_id)
        return self.build_chapters_for_arc(arcs[0]) if arcs else 0

    def revise_provisional_chapters(self, part_id: str) -> int:
        """§11 演到某 Part 时复核其 provisional（仍 planned 未写）章：用**已发生的事实**
        刷新目标/戏剧问题/章末钩子（吸收涌现），原地更新不改 id/cast/role/地点。返回复核章数。
        离线无 LLM 时近似幂等（模板不变）。"""
        part = self.repo.get_part(part_id)
        if part is None:
            return 0
        all_ch = self.repo.list_chapter_plans()
        recent_qs = [c.dramatic_question for c in all_ch if c.dramatic_question]
        revised = 0
        for arc in self.repo.list_arcs(part_id):
            for ch in self.repo.list_chapter_plans(arc.arc_id):
                if ch.status != "planned":
                    continue
                self._revise_one(part, arc, ch, recent_qs)
                revised += 1
        return revised

    def _revise_one(self, part, arc, ch, recent_qs: list[str]) -> str:
        """用已发生事实原地复核单个 planned 章：刷新 beats/问题/exit_state/钩子/道具台账，
        不改 id/cast/role/章号。返回（去重后的）戏剧问题，供调用方串 recent_qs。"""
        all_ch = self.repo.list_chapter_plans()
        part_locs = [(e.entity_id, e.name) for e in self.repo.list_entities()
                     if e.type == "location" and e.attributes.get("part") == part.part_id]
        locs = part_locs or ([(ch.location_ids[0], "")] if ch.location_ids
                             else [("loc_main", "主场景")])
        prev_loc = ch.location_ids[0] if ch.location_ids else None
        prevs = [c for c in all_ch if c.sequence_order < ch.sequence_order]
        prev_hook = (max(prevs, key=lambda c: c.sequence_order).ending_hook if prevs else "")
        # W0 主角 POV 偏置（与 _generate_chapter 一致）：用 ch.sequence_order 作全书章序定预定主视角。
        from .casting import pov_eligible
        personas = self.repo.list_personas()
        hero_id = personas[0].agent_id if personas else None
        eligible = [a for a in ch.cast if pov_eligible(self.repo, a, hero_id)] or \
                   ([hero_id] if hero_id else ch.cast[:1])
        lead, hero_ok = self._pov_lead(ch.sequence_order, eligible, hero_id)
        pov_for_prompt = lead or hero_id or (eligible[0] if eligible else "")
        pov_name = (self.repo.get_persona(pov_for_prompt).name
                    if self.repo.get_persona(pov_for_prompt) else "")
        cast_names_str = "、".join(self.repo.get_persona(a).name for a in eligible
                                   if self.repo.get_persona(a))
        id_by_name = {p.name: p.agent_id for p in personas if p.name}
        beats, loc, dq, props, ex, beat_pov_names = self._chapter_spec(
            part, arc, ch.role, has_reveal=bool(ch.reveal_gate),
            locs=locs, prev_loc=prev_loc, prev_hook=prev_hook,
            conflict_type=getattr(ch, "conflict_type", ""), pov_name=pov_name, cast_names=cast_names_str)
        if not ch.reveal_gate and dq and self._question_similar(dq, recent_qs[-6:]):
            dq = self._distinct_question(ch.role, recent_qs[-6:])
        ch.beat_goals = beats
        beat_pov_ids = []
        for i in range(len(beats)):
            nm = beat_pov_names[i] if i < len(beat_pov_names) else ""
            pid = id_by_name.get(nm)
            beat_pov_ids.append(pid if (pid and pid in eligible)
                                else (pov_for_prompt or (eligible[0] if eligible else "")))
        ch.pov_agent, ch.beat_povs = self._bias_pov(
            lead, hero_ok, eligible, beat_pov_ids, pov_for_prompt)
        ch.dramatic_question = dq
        # P2：复核时也让场数跟上节拍数
        ch.target_scenes = max(2, len(beats))
        if ex:
            ch.exit_state = ex
        ch.ending_hook = self._ending_hook(ch.role, dq, beats[0] if beats else "")
        for pid in self._register_props(props):
            if pid not in ch.items_present:
                ch.items_present.append(pid)
            if pid not in ch.items_introduced:
                ch.items_introduced.append(pid)
        self.repo.upsert_chapter_plan(ch)
        recent_qs.append(dq)
        return dq

    def revise_next_chapter(self) -> ChapterPlan | None:
        """B3（scripted 章末动态细化）：复核**下一个 planned 章**（按 sequence_order 最小的未写章），
        用刚写出的真实事实刷新它的 beats/问题/exit_state/钩子（吸收涌现）。不改 id/role/章号。"""
        planned = [c for c in self.repo.list_chapter_plans() if c.status == "planned"]
        if not planned:
            return None
        ch = min(planned, key=lambda c: c.sequence_order)
        arc = self.repo.get_arc(ch.arc_id)
        part = self.repo.get_part(arc.part_id) if arc else None
        if arc is None or part is None:
            return None
        recent_qs = [c.dramatic_question for c in self.repo.list_chapter_plans()
                     if c.dramatic_question and c.sequence_order < ch.sequence_order]
        self._revise_one(part, arc, ch, recent_qs)
        return ch

    def ensure_chapter(self) -> ChapterPlan | None:
        """导演每拍调用：有 active/planned 章则用之，否则临场生成下一章（含跨 Arc）。
        §11 后全章纲已在册，通常直接命中已生成章；next_chapter 仅作兜底。"""
        cur = self.repo.active_chapter_plan()
        if cur is not None:
            return cur
        return self.next_chapter()

    def next_chapter(self) -> ChapterPlan | None:
        """生成"下一章"：在尚有空位的 Arc 内续生成；该 Arc 已满则建下一个 Arc 再生成其首章。"""
        for arc in self.repo.list_arcs():
            done = len(self.repo.list_chapter_plans(arc.arc_id))
            if done < arc.target_chapters:
                return self._generate_chapter(arc, done)
        arc = self.plan_next_arc()
        if arc is None:
            return None
        return self._generate_chapter(arc, 0)

    def _pov_lead(self, global_seq: int, eligible: list[str], hero_id: str | None):
        """W0 主角 POV 偏置（硬上限优先）：按**全书章序** global_seq 决定本章预定主视角。
        返回 (lead, hero_ok)。主角 eligible 时：默认主角主讲；每全书第 4 章在 eligible 配角间
        轮换一人主讲（=25% ≤30%，第 1 章恒主角）。主角不 eligible（藏未揭身份/缺席）→
        返回 (None, False)，交回退按 beat 多数（不强加偏置，绝不泄底）。"""
        if not (hero_id and hero_id in eligible):
            return None, False
        pool = [a for a in eligible if a != hero_id]
        if pool and global_seq % 4 == 0:
            return pool[(global_seq // 4 - 1) % len(pool)], True
        return hero_id, True

    def _bias_pov(self, lead, hero_ok: bool, eligible: list[str],
                  beat_pov_ids: list[str], fallback: str):
        """W0：让 lead 占 beat **严格多数**（至多保留 floor((n-1)/2) 个 eligible 配角 beat），
        返回 (章主视角 pov_agent, 偏置后 beat_pov_ids)。这样渲染出的多数场都是 lead 视角，
        全书 ≥70% 章主角主讲得到硬保证。主角不 eligible → 不强加，退回 beat 多数。"""
        from collections import Counter
        out = list(beat_pov_ids)
        n = len(out)
        if not hero_ok or not lead:
            pov = Counter(out).most_common(1)[0][0] if out else fallback
            return pov, out
        if n == 0:
            return lead, out
        keep_other = n - (n // 2 + 1)   # 严格多数留给 lead，其余 beat 落回 lead
        other = 0
        for i, a in enumerate(out):
            if a == lead:
                continue
            if a in eligible and other < keep_other:
                other += 1
                continue
            out[i] = lead
        return lead, out

    def _generate_chapter(self, arc: Arc, idx: int) -> ChapterPlan:
        """⑤ 临场生成 Arc 的第 idx 章：role 由曲线钉死（里程碑位置固定），
        但**具体目标据最近发生的事/主角抉择即时生成** → 角色选择能改写后续章。"""
        part = self.repo.get_part(arc.part_id)
        personas = self.repo.list_personas()
        valid_ids = {p.agent_id for p in personas}
        focus_ids = [f["agent_id"] for f in arc.focus_agents if f.get("agent_id") in valid_ids]
        # ⑥/§12.3 本 Part 可用地点（多个，一等实体），(id,name) 形式。loc_main 仅作无地点时兜底。
        part_locs = [
            (e.entity_id, e.name) for e in self.repo.list_entities()
            if e.type == "location" and part and e.attributes.get("part") == part.part_id
        ]
        if not part_locs:
            part_locs = [
                (e.entity_id, e.name) for e in self.repo.list_entities()
                if e.type == "location" and e.entity_id == "loc_main"
            ] or [("loc_main", "主场景")]

        bias = _norm_tension_bias(self.repo.get_tone_profile().tension_curve_bias)
        curve = _role_curve(arc.target_chapters, bias)
        role, tension = curve[idx]
        # S1 冲突类型轮换：本章冲突"种类"，作硬约束注入戏剧问题+beat（治"七章同一支舞"）
        conflict_type = _conflict_curve([r for r, _ in curve])[idx]
        all_ch = self.repo.list_chapter_plans()
        base_seq = max((c.sequence_order for c in all_ch), default=0)
        prev = [c for c in all_ch if c.arc_id == arc.arc_id]
        prev_loc = prev[-1].location_ids[0] if prev and prev[-1].location_ids else None
        # §13.2 接钩：取全书上一章的章末钩子，让本章开场目标"接住"它（章际连贯）。
        prev_global = max(all_ch, key=lambda c: c.sequence_order, default=None)
        prev_hook = prev_global.ending_hook if prev_global else ""

        # ---- cast 组装：过滤缺席人物 + 保证主角在场 + present 的被点名核心人物优先 ----
        hero_id = personas[0].agent_id if personas else None  # 主角永远是 personas[0]

        def _is_absent(pid: str) -> bool:
            # 缺席人物（已死/已失踪/仅存于记忆，如待寻的亡妻）不进常规 cast，保住"缺席"悬念。
            p = self.repo.get_persona(pid)
            return bool(p and (p.arc_state or {}).get("absent"))

        # focus 里也要剔除缺席人物（修：苏窈这类缺席者经 focus_agents 绕过过滤）
        cast = [pid for pid in focus_ids if not _is_absent(pid)]
        # 修：focus_agents 可能漏掉主角 → 强制把主角放在 cast 首位（除非主角本身缺席）
        if hero_id and hero_id not in cast and not _is_absent(hero_id):
            cast.insert(0, hero_id)
        others = [pid for pid in valid_ids if pid not in cast and not _is_absent(pid)]
        # present 的被点名核心人物（named_）优先于孵化配角进 cast
        others.sort(key=lambda pid: (not str(pid).startswith("named_"), pid))
        if others:
            cast.append(others[idx % len(others)])
        cast = self._rotate_faction_member_into_cast(arc, cast, idx)
        cast = cast[:4]

        items = sorted({it.object_id for aid in cast for it in self.repo.items_held_by(aid)})
        # §13.1 道具台账：在场 = 前章未消耗的继承 + 本章 cast 携带；新登场 = cast 携带里前章没有的
        carried = [o for o in (prev_global.items_present if prev_global else [])
                   if o not in (prev_global.items_consumed if prev_global else [])
                   and self.repo.item_exists(o)]
        items_present = list(dict.fromkeys(carried + items))
        items_introduced = [o for o in items if o not in carried]

        # 揭示：里程碑章（转/合）若本 Part 还有未揭真相 → 主角在此撞到
        hero = focus_ids[0] if focus_ids else (personas[0].agent_id if personas else None)
        gate: list[str] = []
        delta: dict[str, list[str]] = {}
        if role in ("twist", "climax") and part is not None:
            truth = next((n for n in self.repo.list_reveal_nodes()
                          if n.node_id in part.reveal_node_ids and not n.discovered and n.fact_id), None)
            if truth is not None:
                gate = [truth.fact_id]
                if hero:
                    delta = {hero: [truth.fact_id], "reader": [truth.fact_id]}

        # 问题1/S3：近 5 章用过的地点 → 施加换场压力（窗口 3→5，治"第5章又回到第1章咖啡馆"的回溯感）
        recent_locs = [c.location_ids[0] for c in sorted(all_ch, key=lambda c: c.sequence_order)[-5:]
                       if c.location_ids]
        from .casting import pov_eligible
        id_by_name = {p.name: p.agent_id for p in personas if p.name}
        # 视角只能是"合格"的人（主角+无未揭反派身份者）——防读者从反派视角提前全知泄底
        eligible = [a for a in cast if pov_eligible(self.repo, a, hero_id)] or \
                   ([hero_id] if hero_id else cast[:1])
        cast_names_str = "、".join(self.repo.get_persona(a).name for a in eligible
                                   if self.repo.get_persona(a))
        # W0 主角 POV 偏置（硬上限优先）：按全书章序定本章预定主视角（默认主角；每全书第 4 章配角主讲）。
        global_seq = base_seq + 1
        lead, hero_ok = self._pov_lead(global_seq, eligible, hero_id)
        pov_for_prompt = lead or hero_id or (eligible[0] if eligible else "")
        pov_name = (self.repo.get_persona(pov_for_prompt).name
                    if self.repo.get_persona(pov_for_prompt) else "")
        beats, loc, dq, props, exit_state, beat_pov_names = self._chapter_spec(
            part, arc, role, has_reveal=bool(gate),
            locs=part_locs, prev_loc=prev_loc, prev_hook=prev_hook,
            recent_locs=recent_locs, conflict_type=conflict_type,
            pov_name=pov_name, cast_names=cast_names_str)
        intro_beats = self._world_intro_beats(role, global_seq)
        if intro_beats:
            beats = intro_beats + beats
        faction_pressure = self._chapter_faction_pressure(loc, cast)
        if faction_pressure and beats:
            beats[0] = f"{beats[0]}（{faction_pressure}）"
        # POV 跟着节拍走：每个 beat 的视角名→id；不合格(反派未揭身份)→落到预定主视角，绝不泄底
        beat_pov_ids = []
        for i in range(len(beats)):
            nm = beat_pov_names[i] if i < len(beat_pov_names) else ""
            pid = id_by_name.get(nm)
            beat_pov_ids.append(pid if (pid and pid in eligible)
                                else (pov_for_prompt or (eligible[0] if eligible else "")))
        # W0：偏置 beat_povs 使预定主视角占严格多数，并据此定章主视角（保证全书 ≥70% 主角主讲）。
        pov_agent, beat_pov_ids = self._bias_pov(lead, hero_ok, eligible, beat_pov_ids, pov_for_prompt)
        goal = beats[0] if beats else ""
        # 道具来源闸门：把 beat 文本里点到的器物登记成实体 + 写入本章 items_introduced，
        # 让叙述层白名单覆盖它们、并标记"本章新登场" → 杜绝"日记本/照片/字条凭空出现"。
        for pid in self._register_props(props):
            if pid not in items_present:
                items_present.append(pid)
            if pid not in items_introduced:
                items_introduced.append(pid)
        # §12.3+§13.1：本章地点的固有道具并入在场台账与可用物品（"地点固有"来源，防凭空）
        loc_obj = self.repo.get_location(loc)
        loc_items = loc_obj.notable_items if loc_obj else []
        for oid in loc_items:
            if oid not in items_present:
                items_present.append(oid)
                if oid not in carried:
                    items_introduced.append(oid)
        items = sorted(set(items) | set(loc_items))  # available_items 含地点固有道具，可被取用
        # §13.3 戏剧问题去重：中心秘密只许里程碑章（有 reveal_gate）问；中间章若与近 6 章
        # 戏剧问题雷同，强制换一个非雷同问题 → 从结构上禁止"十章问同一句"（破循环）。
        recent_qs = [c.dramatic_question for c in all_ch if c.dramatic_question][-6:]
        if not gate and dq and self._question_similar(dq, recent_qs):
            dq = self._distinct_question(role, recent_qs)
        # §2 收束判定（机器可评的 DSL，确定性生成而非 LLM）：
        #   有揭示 → "撞到真相"即收；否则 → 焦点角色"做出抉择"即收。
        if gate:
            predicate = f"reveal_discovered_fact({gate[0]})"
        elif hero:
            predicate = f"decision_made({hero})"
        else:
            predicate = ""
        # 问题2：道具台账按名字去重（同名不同 id 只留一个），让叙述白名单干净
        items_present = self._dedup_by_name(items_present)
        items = self._dedup_by_name(items)
        items_introduced = self._dedup_by_name(items_introduced)
        # §13.2 本章章末钩子：按 role 定类型，内容指向本章未决的戏剧问题/后文
        hook_type = _HOOK_TYPE.get(role, "new_question")
        ending_hook = self._ending_hook(role, dq, goal)
        ch = ChapterPlan(
            chapter_id=_uid("ch"), arc_id=arc.arc_id, sequence_order=base_seq + 1,
            title="", cast=cast, location_ids=[loc], available_items=items,
            items_present=items_present, items_introduced=items_introduced, items_consumed=[],
            beat_goals=beats, beat_povs=beat_pov_ids, reveal_gate=gate, knowledge_delta=delta,
            # P2 一拍一场·不漏拍：场数 = 节拍数（每个 beat 写成一整场），治"4 拍只写 3 场丢最后一拍"
            target_scenes=max(2, len(beats)),
            role=role, target_tension=tension,
            dramatic_question=dq, resolution_predicate=predicate, min_scenes=min(2, len(beats)),
            target_words=_ROLE_WORDS.get(role, 2400),
            ending_hook=ending_hook, hook_type=hook_type,
            pov_agent=pov_agent, exit_state=exit_state,
            conflict_type=conflict_type,
            status="planned",
        )
        self.repo.upsert_chapter_plan(ch)
        return ch

    def _resolve_character(self, name: str) -> str | None:
        """大纲编辑级联：按名找角色实体；不存在则**以该名**即时建（实体+persona+卡），
        让"改大纲带出新人物"。直接建（不走 cast_or_get，因其会按 LLM/回退另取名，无视意图名）。"""
        nm = (name or "").strip()
        if not nm:
            return None
        for e in self.repo.list_entities():
            if e.type == "character" and e.name == nm:
                return e.entity_id
        from .models import CharacterCard
        aid = _uid("edit")
        self.repo.insert_entity(Entity(aid, "character", nm, {"from": "edit"}))
        self.repo.insert_persona(Persona(
            agent_id=aid, name=nm,
            arc_state={"last_change_tick": 0, "last_flaw_cost_tick": 0, "changed": False},
            cost_ledger=[]))
        try:
            self.repo.add_card(CharacterCard(
                card_id=_uid("card"), agent_id=aid, tier="supporting",
                slot_key=f"edit_{nm}", name=nm, one_liner="大纲编辑新增的人物"))
        except Exception:
            pass
        return aid

    def edit_chapter(self, chapter_id: str, *, title=None, dramatic_question=None,
                     beat_goals=None, cast_names=None, location_name=None,
                     conflict_type=None, exit_state=None, item_names=None) -> ChapterPlan | None:
        """编辑一章大纲（仅供未写章；写过的由调用方拦）。级联：cast/道具按名找不到就**新建实体**，
        让"改大纲，相关人物/物品跟着改"。返回更新后的章计划，章不存在返回 None。"""
        ch = self.repo.get_chapter_plan(chapter_id)
        if ch is None:
            return None
        if title is not None:
            ch.title = title.strip()
        if dramatic_question is not None:
            ch.dramatic_question = dramatic_question.strip()
        if conflict_type is not None:
            ch.conflict_type = conflict_type.strip()
        if exit_state is not None:
            ch.exit_state = exit_state.strip()
        if beat_goals is not None:
            ch.beat_goals = [str(b).strip() for b in beat_goals if str(b).strip()]
            ch.ending_hook = self._ending_hook(ch.role, ch.dramatic_question,
                                                ch.beat_goals[0] if ch.beat_goals else "")
        if cast_names is not None:  # 级联建人物
            ids = [aid for nm in cast_names if (aid := self._resolve_character(nm))]
            if ids:
                ch.cast = list(dict.fromkeys(ids))
        if location_name is not None:  # 在本 Part 地点里按名匹配；找不到则保持原地点
            loc = next((e.entity_id for e in self.repo.list_entities()
                        if e.type == "location" and e.name == location_name.strip()), None)
            if loc:
                ch.location_ids = [loc]
        if item_names is not None:  # 级联建道具（_register_props）
            ids = self._register_props(item_names)
            carried = [oid for oid in (ch.items_present or []) if self.repo.item_exists(oid)]
            ch.items_present = self._dedup_by_name(carried + ids)
            ch.items_introduced = self._dedup_by_name(list(ch.items_introduced or []) + ids)
            ch.available_items = self._dedup_by_name(list(ch.available_items or []) + ids)
        self.repo.upsert_chapter_plan(ch)
        return ch

    def _register_props(self, prop_names: list[str]) -> list[str]:
        """道具来源闸门：把 beat 文本里点到的器物名登记成 object 实体，返回其 id 列表。
        已存在同名实体则复用（按名匹配），否则新建 → 让 §13.1 道具台账/叙述白名单覆盖它们，
        使"日记本/照片/字条"这类剧情核心道具不再游离于结构之外、凭空出现。"""
        if not prop_names:
            return []
        name_to_id = {e.name: e.entity_id for e in self.repo.list_entities() if e.type == "object"}
        out: list[str] = []
        for raw in prop_names:
            nm = str(raw).strip().strip("「」『』\"' ")
            # 过滤：空 / 过长（像句子而非器物名）/ 纯英文 id 残留
            if not nm or len(nm) > 12 or nm.isascii():
                continue
            oid = name_to_id.get(nm)
            if oid is None:
                oid = _uid("obj")
                self.repo.insert_entity(Entity(oid, "object", nm, {"from": "beat"}))
                name_to_id[nm] = oid
            if oid not in out:
                out.append(oid)
        return out

    def _dedup_by_name(self, object_ids: list[str]) -> list[str]:
        """问题2 修复：同名不同 id 的道具（如多个『失踪者名册』）在台账里只保留一个，
        防止正文出现重复器物。保持原顺序、首次出现者胜。"""
        names = {e.entity_id: e.name for e in self.repo.list_entities()}
        seen: set[str] = set()
        out: list[str] = []
        for oid in object_ids:
            nm = names.get(oid, oid)
            if nm in seen:
                continue
            seen.add(nm)
            out.append(oid)
        return out

    def _recent_context(self) -> str:
        """最近发生的事 + 主角抉择，喂给下一章目标生成（让选择改写后续）。"""
        evs = self.repo.list_events()[-6:]
        bits = []
        for e in evs:
            note = e.payload.get("note") or e.payload.get("dialogue") or e.action_type
            bits.append(str(note)[:40])
        return "；".join(bits)

    # ----- §13.3 戏剧问题去重（确定性，二字 gram 的 Jaccard 相似度） -----
    @staticmethod
    def _q_grams(text: str) -> set[str]:
        t = "".join(ch for ch in (text or "") if not ch.isspace())
        return {t[i:i + 2] for i in range(len(t) - 1)}

    def _question_similar(self, q: str, recent: list[str], thresh: float = 0.6) -> bool:
        qg = self._q_grams(q)
        if not qg:
            return False
        for r in recent:
            rg = self._q_grams(r)
            if not rg:
                continue
            inter = len(qg & rg)
            union = len(qg | rg)
            if union and inter / union >= thresh:
                return True
        return False

    def _distinct_question(self, role: str, recent: list[str]) -> str:
        """从本 role 的备选池（不够则借其它 role）里挑一个与近 6 章非雷同的问题。"""
        pool = list(_QUESTION_BANK.get(role, []))
        for other in ("rising", "twist", "setup", "climax", "resolution"):
            for cand in _QUESTION_BANK[other]:
                if cand not in pool:
                    pool.append(cand)
        for cand in pool:
            if not self._question_similar(cand, recent):
                return cand
        return _ROLE_QUESTION.get(role, "本章会如何收束？")

    def _tone_beat_clause(self) -> str:
        """把文风契约（题材/主效果/节奏/手法/禁忌）注入 beat 生成，
        让节拍贴合调性——避免把文艺/悬疑/情感向的故事规划成与基调不符的动作场面。"""
        tp = self.repo.get_tone_profile()
        if not tp or not tp.is_set():
            return ""
        parts: list[str] = []
        if tp.genre:
            parts.append(f"题材「{tp.genre}」")
        if tp.primary_effect:
            parts.append(f"每一拍都要服务于核心效果「{tp.primary_effect}」")
        if tp.pacing:
            parts.append(f"节奏「{tp.pacing}」")
        if tp.device_kit:
            parts.append(f"多用与该题材相称的手法（{('、'.join(tp.device_kit[:6]))}）")
        if tp.diction_dont:
            parts.append(f"回避（{('、'.join(tp.diction_dont[:6]))}）")
        if not parts:
            return ""
        return (
            "【调性约束】" + "；".join(parts) +
            "。节拍须落在该题材与节奏里——冲突可以靠对峙、心理、抉择、信息差来推进，"
            "不要默认写成打斗、追逐、爆炸、自毁程序倒计时这类与基调不符的动作戏（除非题材本就是动作）。"
        )

    def _prior_chapters_digest(self, limit: int = 5) -> str:
        """前几章"做过什么"的速览（章号/冲突类型/地点/核心动作）——喂给下一章生成，
        强制其核心动作/场景/手段与已发生的不同（治"前四章动作几乎一模一样"）。"""
        chs = sorted(self.repo.list_chapter_plans(), key=lambda c: c.sequence_order)[-limit:]
        if not chs:
            return ""
        nm = {e.entity_id: e.name for e in self.repo.list_entities()}
        lines = []
        for c in chs:
            loc = nm.get(c.location_ids[0], "") if c.location_ids else ""
            first = (c.beat_goals[0] if c.beat_goals else "")[:46]
            lines.append(f"· 第{c.sequence_order}章〔{c.conflict_type or c.role}@{loc}〕{first}")
        return "\n".join(lines)

    def _chapter_spec(self, part, arc: Arc, role: str, has_reveal: bool,
                      locs: list[tuple[str, str]], prev_loc: str | None,
                      prev_hook: str = "", recent_locs: list[str] | None = None,
                      conflict_type: str = "", pov_name: str = "", cast_names: str = ""
                      ) -> tuple[list[str], str, str, list[str]]:
        """据 role + 前情即时生成本章 (**节拍列表 beats≥3**, 地点, 戏剧问题, **道具名 props**)。
        §13 beats 各异、逐场消费（B2）；⑥ 地点按剧情从本 Part 地点中选；§13.2 首拍接住上一章钩子。
        问题1：recent_locs（近几章用过的地点 id）→ 施加"换场压力"，避免故事卡在同一处。
        道具来源闸门：props = 这些 beat 中出现/易手的具体器物（信件/日记/照片/钥匙/戒指…），
        回到 _generate_chapter 登记成实体 + 写入 items_introduced，杜绝叙述层"凭空道具"。
        地点-beat 一致：所有节拍必须发生在选定地点内，不得在 beat 里冒出本章地点之外的场所
        （治"地点字段=邮政支局、beat 却写灯塔、正文又是别处"的三层脱节）。"""
        loc_by_name = {nm: lid for lid, nm in locs}
        loc_names = "、".join(nm for _, nm in locs)
        prev_name = next((nm for lid, nm in locs if lid == prev_loc), None)
        recent_locs = recent_locs or []
        recent_loc_names = [nm for lid, nm in locs if lid in recent_locs]
        # 缺席人物（已失踪/已消失/仅存于回忆/被追寻者）：beats 不得安排他们登场或说话
        absent_names = [e.name for e in self.repo.list_entities()
                        if e.type == "character" and (e.attributes or {}).get("absent")]
        absent_clause = (
            f"【缺席人物约束】以下人物当前是**缺席的**（已失踪/已消失/仅存于回忆或正被追寻）："
            f"{('、'.join(absent_names))}。本章节拍中**绝不可**安排他们现身、登场、开口说话或带路，"
            f"他们只能作为被追寻、被提及、被回忆的对象出现在线索里。"
            if absent_names else "")
        tone_clause = self._tone_beat_clause()
        # S1 冲突类型硬约束：本章必须落在指定的冲突"种类"，从结构上避免"每章都是潜入获取任务"。
        ct_clause = ""
        if conflict_type:
            ct_clause = (
                f"【本章冲突类型·硬约束】本章必须是**【{conflict_type}】**类冲突："
                f"{_CONFLICT_HINT.get(conflict_type, '')}。"
                f"戏剧问题与节拍都要落在这个类型上，**不得**写成又一个"
                f"'在监视下潜入/获取/接头'的任务（除非本章类型就是潜入任务）。")
        # POV 跟着节拍走：每个 beat 聚焦一个角色的视角（可不同），并在 beat_povs 里标出是谁。
        # 治"节拍写的是赵九的戏、POV 却被钉成沈砚"——谁是这一拍的主角，这一场就用谁的视角。
        pov_lead_clause = (
            f"本章**主视角**应为「{pov_name}」：多数 beat 用其视角，其余我方角色至多点缀 1 拍。"
            if pov_name else "")
        pov_clause = (
            f"【视角·硬约束】**可用视角角色仅限**：{cast_names or pov_name or '（见 cast）'}。"
            f"{pov_lead_clause}"
            f"每个 beat 聚焦其中一个角色的视角（不同 beat 可不同——谁是这一拍的行动者/亲历者就用谁），"
            f"在 beat_povs 里**按 beat 顺序写出每个 beat 的视角角色名（只能从上面这几个里选）**。"
            f"**绝不可**用藏着未揭秘密的反派当视角（会让读者提前知道谜底）——他们只能被这几个视角角色"
            f"**从外部观察**。同一个 beat 内只写该视角角色能看到/听到/感觉到的，**不要混入别人的内心活动**。")
        prior = self._prior_chapters_digest()
        if self.llm is not None:
            recent = self._recent_context() or "（开篇，尚无前情）"
            # ④ 伏笔非对称（主题4）：埋设期压低显著度、回收期塌陷放大。按 role 区分两态。
            if has_reveal:
                if role in ("twist", "climax"):
                    reveal_hint = ("【伏笔回收·放大】本章其中一拍要让**此前埋下的某条线索在此刻塌陷为枢纽**："
                                   "明确回指那个早先不起眼的细节，揭示它真正的分量，让读者恍然。")
                else:
                    reveal_hint = ("【伏笔埋设·压低】本章可埋一条关键线索，但要作为**不起眼的闲笔**一带而过——"
                                   "**禁止**让它与『秘密/关键/真相/隐藏/重要』这类词同句出现，"
                                   "也不要让人物显得很在意它（越不像伏笔越好）。")
            else:
                reveal_hint = ""
            # ⑤ 人物弧线阶段（主题7）：本章主角所处心理阶段；里程碑章推他做相反抉择。
            arc_phase = _ROLE_ARC.get(role, "")
            arc_clause = ""
            if arc_phase:
                arc_clause = f"【主角弧线·本章阶段】{arc_phase}。"
                if role in ("twist", "climax"):
                    arc_clause += ("本章要把主角推入一个**与他一贯姿态相反**的处境或抉择，"
                                   "逼他展现出之前没有过的一面，不要让他从头到尾都是一个样子。")
            # ② 道德灰度 + 非圆满（硬伤三）：里程碑/收束章的抉择要有代价、可道德模糊。
            moral_clause = ""
            if role in ("twist", "climax", "resolution"):
                moral_clause = ("【道德灰度·结局不必圆满】本章的抉择要**有代价、可道德模糊**："
                                "胜利可以付出沉重代价，善意可能酿成恶果，主角可以自私、可以妥协、可以做错；"
                                "**不要**让主角靠'正确的意志'把一切完美解决，允许惨胜、两难或悬而未决。")
            hook_hint = f"第一拍须**接住上一章的悬念**：{prev_hook}。" if prev_hook else ""
            prior_block = (
                f"\n【前几章已经发生过的（核心动作/地点/手段——本章务必避开，不要重复）】\n{prior}\n"
                if prior else "")

            def _ok(d):  # §14 深度闸门：beats≥3 且各异、问题与出口状态非空
                if not isinstance(d, dict):
                    return False
                bs = [str(x).strip() for x in (d.get("beats") or []) if str(x).strip()]
                return (len(bs) >= 3 and len(set(bs)) == len(bs)
                        and str(d.get("question", "")).strip() and str(d.get("exit_state", "")).strip())

            # W6 RAG：按本章可选地点 + arc 涉及人物检索相关子图
            _plan_seeds: set[str] = set()
            for lid, _nm in locs:
                _plan_seeds.add(lid)
            rag_ctx = build_context(self.repo, _plan_seeds, budget=2000,
                                    beat_text=arc.summary or (part.goal if part else ""))
            bible_block = (
                f"【世界观（据此理解所有设定，**勿望文生义**——如『孤岛』指被沦陷区围困的孤悬租界，"
                f"不是真的海岛；专有名词、势力、地名都按此设定用）】\n{rag_ctx}\n\n" if rag_ctx else "")
            data = self._complete_json(
                bible_block
                + f"你是资深小说编剧，为长篇小说规划**下一章的章纲**。本小部分梗概："
                f"{arc.summary or (part.goal if part else '')}。本章功能：{_ROLE_CN.get(role, role)}。"
                f"前情（最近发生）：{recent}。{hook_hint}{reveal_hint}\n"
                f"{prior_block}"
                f"【这一章怎么写（每章是一个完整的戏剧单元，不是流水账）】\n"
                f"① 本章聚焦角色有一个**具体的、可衡量的目标**（这一章他想干成的一件事）；\n"
                f"② 过程遭遇**实打实的阻力/对抗**（有人或处境挡着，且戳中他的软肋）；\n"
                f"③ 结尾必须有一个**转折或新的麻烦**（disaster）——让他的处境比开头**更糟或更复杂**，"
                f"而不是顺利拿到东西就走人；\n"
                f"④ 本章结束时的局面必须比开头**更进一步、赌注更高**（escalation），价值发生正负翻转。\n"
                f"【最重要·不许雷同】本章的**核心动作、发生地点、推进手段**都要和上面"
                f"〔前几章已发生〕**明显不同**——**严禁**又一次"
                f"『在咖啡馆/书店碰头 → 敲桌打暗号 → 某人递来一件信物 → 物品易手 → 走人』这套。"
                f"让人物**走出去做不一样的事**：跟踪、搜查、审讯、潜入、伏击、营救、当面摊牌、"
                f"被迫逃亡、交易破裂、身份险些败露、设局反将一军……每章换一种。\n"
                f"{pov_clause}{ct_clause}{tone_clause}{absent_clause}{arc_clause}{moral_clause}\n"
                f"【地点】可选地点：{loc_names}。上一章在「{prev_name or '未定'}」，最近几章待过："
                f"{('、'.join(recent_loc_names) or '（无）')}。**本章必须换一个不同的地点**"
                f"（除非剧情有非留不可的强理由）。所有节拍都只发生在你选定的**这一个**地点内，"
                f"**不得**在 beat 里写到该地点之外的场所（要换地方就是另起一章的事）。\n"
                f"【地点·铁律】你填的 location 必须**就是 beat 当前正在发生的那个地点**——"
                f"**绝不可**把场景写在某地、location 却填另一个（哪怕承接上一章在某处，也要把 location "
                f"直接选成那处，而不是嘴上换地、身体没动）。把别处只能当作被提及/回忆的对象，不得当作当前场景描写。\n"
                f"【道具】props = 这些节拍中**首次出现或易手的具体物件**（用中文名）；凡 beat 里被人"
                f"拿到/交出/发现的东西都要登进 props（供登记来源，杜绝凭空出现）。\n"
                f"【输出】3-4 个各异且递进的具体节拍 beats；**beat_povs**=与 beats 等长的数组，"
                f"每项是对应 beat 的**视角角色名**（这一拍以谁的视角写）；一个**悬而待答的是非/抉择型**戏剧问题 question；"
                f"一句**具体到本章**的出口状态 exit_state（到本章结尾世界/关系/认知发生的**外部可观测变化**："
                f"物/位置/关系/局面/被揭开的认知；**不要**写'一个关键物易手'这种放之四海皆准的空话，"
                f"也**禁止**写成『主角领悟了/明白了/想通了某个道理』这类内心升华或主题点题）；props 清单。\n"
                f"只输出 JSON：{{\"beats\":[\"…\",\"…\",\"…\"],\"beat_povs\":[\"角色名\",\"角色名\",\"角色名\"],"
                f"\"location\":\"地点名\",\"question\":\"…\",\"exit_state\":\"…\",\"props\":[\"…\"]}}",
                "只输出 JSON。", validate=_ok, retries=1,
            )
            if data:
                beats = [str(x).strip() for x in (data.get("beats") or []) if str(x).strip()]
                povs = [str(x).strip() for x in (data.get("beat_povs") or [])]
                picked = str(data.get("location", "")).strip()
                dq = str(data.get("question", "")).strip()
                ex = str(data.get("exit_state", "")).strip()
                props = [str(x).strip() for x in (data.get("props") or []) if str(x).strip()]
                # LLM 选了有效地点用之；否则换场轮换（避开最近用过的），而非恒沿用 prev_loc
                loc = loc_by_name.get(picked) or self._rotate_location(locs, recent_locs, prev_loc)
                # 问题1（地点↔beat 对账）：若 beat 首拍**实际发生**在另一个可选地点（LLM 嘴上换地、
                # 身体没动 → 包厢/咖啡馆自相矛盾），以 beat 实际地点为准对齐 location，消除硬矛盾。
                bl = self._dominant_beat_loc(beats, locs)
                if bl and bl != loc:
                    loc = bl
                if len(beats) >= 3:
                    return (beats[:4], loc, (dq or _ROLE_QUESTION.get(role, "本章会如何收束？")),
                            props[:6], ex or _ROLE_EXIT.get(role, ""), povs[:4])
        # 离线/失败回退：按 role 给 3 个各异的递进节拍（首拍可接钩）
        topic = (arc.summary or (part.goal if part else "") or (part.title if part else ""))[:16]
        loc = self._rotate_location(locs, recent_locs, prev_loc)
        beats = list(_ROLE_BEATS.get(role, _ROLE_BEATS["rising"]))
        beats = [f"{b}（围绕：{topic}）" for b in beats]
        if prev_hook:
            beats[0] = f"回应上一章悬念（{prev_hook[:18]}），" + beats[0]
        return beats, loc, _ROLE_QUESTION.get(role, "本章会如何收束？"), [], _ROLE_EXIT.get(role, ""), []

    def _rotate_location(self, locs: list[tuple[str, str]], recent_locs: list[str],
                         prev_loc: str | None) -> str:
        """问题1：换场轮换——优先选一个最近几章没用过的地点；都用过则退回 prev_loc/首个。
        避免确定性回退总是沿用 prev_loc 导致故事卡在同一处。"""
        if not locs:
            return prev_loc or "loc_main"
        fresh = [lid for lid, _ in locs if lid not in recent_locs]
        if fresh:
            return fresh[0]
        return prev_loc or locs[0][0]

    def _rotate_faction_member_into_cast(self, arc: Arc, cast: list[str], idx: int) -> list[str]:
        cast = list(dict.fromkeys(cast))
        focus_ids = [f.get("agent_id") for f in (arc.focus_agents or []) if f.get("agent_id")]
        faction_ids = set()
        for aid in focus_ids:
            ent = self.repo.get_entity(aid)
            fid = (ent.attributes or {}).get("faction_id") if ent else ""
            if fid:
                faction_ids.add(fid)
        if not faction_ids:
            return cast
        recent = [c for c in self.repo.list_chapter_plans()
                  if c.arc_id == arc.arc_id and c.status in ("planned", "active", "done")]
        recent_cast = {aid for c in recent for aid in (c.cast or [])}
        candidates: list[str] = []
        for faction in self.repo.list_factions():
            if faction.faction_id not in faction_ids:
                continue
            for member in faction.key_members or []:
                aid = member.get("agent_id")
                if aid and aid not in cast:
                    candidates.append(aid)
        if not candidates:
            return cast
        candidates = [aid for aid in candidates if aid not in recent_cast] or candidates
        pick = candidates[idx % len(candidates)]
        if len(cast) >= 4:
            cast[-1] = pick
        else:
            cast.append(pick)
        return list(dict.fromkeys(cast))

    def _world_intro_beats(self, role: str, sequence_order: int) -> list[str]:
        if role != "setup" or sequence_order > 3:
            return []
        beats = {
            1: "自然带出世界运行的基本秩序与地理处境，让读者知道人物活在什么规则里",
            2: "借行动或对话补足一个与当前冲突相关的势力或地域常识，不写说明书",
            3: "把世界观交代落到具体代价或禁忌上，让规则开始咬人",
        }
        beat = beats.get(sequence_order)
        return [beat] if beat else []

    def _part_geography_hint(self, part: Part | None) -> str:
        if part is None:
            return ""
        locs = [loc for loc in self.repo.list_locations(part.part_id) if getattr(loc, "name", "")]
        names = [loc.name for loc in locs[:3]]
        if not names:
            return ""
        return f"主要地理舞台：{'、'.join(names)}"

    def _part_faction_hint(self, part: Part | None, focus: list[dict]) -> str:
        if part is None:
            return ""
        focus_ids = {f.get('agent_id') for f in (focus or []) if f.get('agent_id')}
        relevant = []
        for faction in self.repo.list_factions():
            member_ids = {m.get("agent_id") for m in (faction.key_members or []) if m.get("agent_id")}
            territory = set(faction.territory or [])
            if member_ids & focus_ids or part.region in territory:
                relevant.append(faction)
        if not relevant:
            return ""
        parts = []
        for faction in relevant[:2]:
            names = [m.get("name") for m in (faction.key_members or [])[:2] if m.get("name")]
            seg = faction.name
            if names:
                seg += f"（核心成员：{'、'.join(names)}）"
            parts.append(seg)
        return "相关势力：" + "；".join(parts)

    def _chapter_faction_pressure(self, loc_id: str, cast: list[str]) -> str:
        loc = self.repo.get_location(loc_id) if loc_id else None
        pieces = []
        if loc and getattr(loc, "controlling_faction", ""):
            faction = next((f for f in self.repo.list_factions() if f.faction_id == loc.controlling_faction), None)
            if faction:
                pieces.append(f"{faction.name}控制此地")
        cast_factions = []
        for aid in cast:
            ent = self.repo.get_entity(aid)
            fid = (ent.attributes or {}).get("faction_id") if ent else ""
            if fid:
                cast_factions.append(fid)
        cast_factions = list(dict.fromkeys(cast_factions))
        if len(cast_factions) >= 2:
            names = []
            for fid in cast_factions[:2]:
                faction = next((f for f in self.repo.list_factions() if f.faction_id == fid), None)
                if faction:
                    names.append(faction.name)
            if len(names) >= 2:
                pieces.append(f"{names[0]}与{names[1]}的摩擦在场")
        return "；".join(pieces)

    def _dominant_beat_loc(self, beats: list[str], locs: list[tuple[str, str]]) -> str:
        """问题1：判定这些 beat（取**首拍**=当前场景设定）实际发生在哪个可选地点。

        按地点名及其 `·` 分段与首拍文本的二字-gram 覆盖度取最像的一个（地名常带前缀/后缀，
        如 location='百乐门舞厅·二楼牡丹包厢' 而正文写'百乐门二楼牡丹包厢'，故用分段覆盖度而非全等）。
        覆盖度 <0.5（无明显匹配）时返回 ""（不强行改地点）。供 `_chapter_spec` 对齐 location↔beat。"""
        head = (beats[0] if beats else "")[:60]
        hg = self._q_grams(head)
        if not hg:
            return ""
        best, best_score = "", 0.0
        for lid, nm in locs:
            for seg in [nm] + (nm.split("·") if nm else []):
                sg = self._q_grams(seg)
                if not sg:
                    continue
                score = len(hg & sg) / len(sg)   # seg 被首拍覆盖的比例
                if score > best_score:
                    best, best_score = lid, score
        return best if best_score >= 0.5 else ""

    def _ending_hook(self, role: str, dq: str, goal: str) -> str:
        """§13.2 生成本章章末钩子（指向后文的悬念）。LLM 优先，失败回退确定性模板。
        钩子不能凭空——以本章未决的戏剧问题 dq 为种子，按 role 给出前瞻式悬念。"""
        seed = (dq or goal or "未决的局面").strip().rstrip("？?")
        if self.llm is not None:
            data = self._complete_json(
                "你为小说章节设计一句**章末钩子**（act-out/button）：留一个悬而未决的悬念，"
                f"勾住读者读下一章。钩子类型：{_HOOK_CN.get(_HOOK_TYPE.get(role,'new_question'))}。"
                "钩子必须指向后文（呼应本章未答的问题或抛出新威胁/新疑问），一句话，不剧透答案。"
                f"本章未决的问题：{dq or '（无）'}；本章目标：{goal}。只输出 JSON：{{\"hook\":\"…\"}}",
                "只输出 JSON。",
            )
            h = str((data or {}).get("hook", "")).strip()
            if h:
                return h[:120]
        tmpl = {
            "new_question": f"{seed}——答案未明，新的疑问已经浮现。",
            "reversal_tease": f"就在以为尘埃落定时，{seed}的背后透出截然相反的一角。",
            "cliffhanger": f"{seed}悬于一线，下一刻的变故已逼到眼前。",
            "dramatic_irony": f"局中人尚未察觉，{seed}的代价才刚刚开始显形。",
        }
        return tmpl.get(_HOOK_TYPE.get(role, "new_question"), f"{seed}——悬念未解。")

    # ================= 章节命名（①：意象/对话/悬念 + 去重 + 黑名单） =================
    def name_chapter(self, chapter_id: str, prose_excerpt: str = "") -> str:
        ch = self.repo.get_chapter_plan(chapter_id)
        if ch is None:
            return ""
        # 取本章成稿正文作为命名素材（具体场景细节 → 标题不空泛）
        ev_ids = {e.event_id for e in self.repo.events_for_beat(chapter_id)}
        proses = [s.prose_text for s in self.repo.list_scenes() if set(s.source_events) & ev_ids]
        material = (" ".join(proses) or ch.summary or prose_excerpt or "；".join(ch.beat_goals))[:500]
        recent = [c.title for c in self.repo.list_chapter_plans() if c.title][-6:]
        blacklist = self._title_blacklist(recent)
        if blacklist:  # §4.3 统一禁用词：章名里反复出现的词，正文也一并规避
            try:
                self.repo.add_banned_words(sorted(blacklist))
            except Exception:
                pass

        title = ""
        if self.llm is not None:
            hint = _ROLE_TITLE_HINT.get(ch.role, "贴合本章内容")
            avoid = ("；".join(recent) or "（无）")
            ban = ("、".join(sorted(blacklist)) or "（无）")
            data = self._complete_json(
                "你为小说章节取一个 3-7 字、含蓄不剧透的中文章名。"
                f"风格倾向：{hint}。要求用**本章独有的具象名词/动作/一句对话**，"
                f"**严禁**与主题词或下列已用词重复：{self.theme}；禁用词：{ban}。"
                f"也不得与近期章名雷同：{avoid}。只输出 JSON：{{\"title\":\"…\"}}",
                f"本章正文片段：{material}\n本章目标：{'；'.join(ch.beat_goals)}\n只输出 JSON。",
            )
            title = str((data or {}).get("title", "")).strip().strip("《》\"'")
            # 去重/黑名单复查：命中则再要一次更强约束，仍不行就退确定性名
            if title and (title in recent or any(b in title for b in blacklist)):
                data = self._complete_json(
                    f"上一个章名「{title}」与既有章名或主题词重复了。换一个**完全不同**、用本章独有细节的 3-7 字中文章名。"
                    f"禁用词：{('、'.join(sorted(blacklist)) or '（无）')}；不得与这些重复：{avoid}。只输出 JSON：{{\"title\":\"…\"}}",
                    f"本章正文片段：{material}\n只输出 JSON。",
                )
                t2 = str((data or {}).get("title", "")).strip().strip("《》\"'")
                if t2 and t2 not in recent and not any(b in t2 for b in blacklist):
                    title = t2
                elif title in recent or any(b in title for b in blacklist):
                    title = ""
        if not title:  # 无 LLM / 反复重复：朴素章号（确定性、永不雷同）
            title = f"第{ch.sequence_order}章"
        ch.title = title
        self.repo.upsert_chapter_plan(ch)
        return title

    def _title_blacklist(self, recent: list[str]) -> set[str]:
        """从近期章名 + Part 名里挑"被反复使用的二字词"作为禁用词（如反复出现的"轮回"）。"""
        from collections import Counter

        texts = list(recent) + [p.title for p in self.repo.list_parts()]
        c: Counter = Counter()
        for t in texts:
            for i in range(len(t) - 1):
                bg = t[i:i + 2]
                if bg.strip():
                    c[bg] += 1
        return {bg for bg, k in c.items() if k >= 2}

    # ================= LLM 规格（带回退） =================
    def _llm_part_specs(self, n: int) -> list[dict] | None:
        if self.llm is None:
            return None
        wb_theme = self.theme or "（未定）"
        schema = '[{"title","goal","region"}]  // region=本部分主要地域'
        # W6 RAG：全书规划级——无具体种子，注入世界观 summary + 势力速览
        rag_ctx = build_context(self.repo, set(), budget=3000)
        bible_block = (
            f"【世界圣经（充分使用其中地理/势力/历史/专有名词，勿概括）】：\n{rag_ctx}\n\n" if rag_ctx else "")
        data = self._complete_json(
            f"{bible_block}你为主题「{wb_theme}」的长篇小说规划 {n} 个递进的大部分（起承转合）。"
            f"每部分给：title(部分名)、goal(本部分要达成/改变什么)、region(主要发生地域，须引用世界圣经中的地名)。"
            f"只输出 JSON 数组：{schema}",
            f"请输出恰好 {n} 个部分，按剧情先后顺序。只输出 JSON 数组。",
            expect_list=True,
        )
        if isinstance(data, list) and data:
            out = [d for d in data if isinstance(d, dict)][:n]
            return out or None
        return None

    def _fallback_part_specs(self, n: int) -> list[dict]:
        arcs = ["入局", "暗涌", "破局", "终章", "余烬"]
        return [
            {"title": f"第{i}部·{arcs[(i - 1) % len(arcs)]}", "goal": "推进主线、揭开一层真相",
             "region": f"故事地域之{i}"}
            for i in range(1, n + 1)
        ]

    def _llm_arc_spec(self, part: Part, seq: int, personas: list[Persona]) -> dict | None:
        if self.llm is None:
            return None
        roster = "；".join(f"{p.agent_id}={p.name}" for p in personas)
        schema = ('{"title","summary","target_chapters":整数(5到10),'
                  '"focus_agents":[{"agent_id","weight":0到1}]}')
        data = self._complete_json(
            f"你为小说「{part.title}」（目标：{part.goal}）规划其中第 {seq} 个小部分（5-10章）。"
            f"focus_agents 决定本段戏份权重——某些小部分可以主讲配角而非主角。"
            f"agent_id 必须取自角色名册。只输出 JSON：{schema}",
            f"角色名册：{roster}。只输出 JSON。",
        )
        return data if isinstance(data, dict) else None

    def _fallback_focus(self, personas: list[Persona], seq: int) -> list[dict]:
        if not personas:
            return []
        # 默认主角(personas[0])为焦点；偶数段把一个配角抬到次焦点（让某段主讲别人）
        focus = [{"agent_id": personas[0].agent_id, "weight": 0.7}]
        if len(personas) > 1:
            sec = personas[seq % len(personas)]
            w = 0.6 if sec.agent_id != personas[0].agent_id and seq % 2 == 0 else 0.3
            if sec.agent_id != personas[0].agent_id:
                focus.append({"agent_id": sec.agent_id, "weight": w})
        return focus

    # ================= §12 世界圣经检索（全文，不摘要） =================
    def _bible_context(self, sections: list[str] | None = None, max_chars: int = 3000) -> str:
        """取相关世界圣经全文段落作规划提示词上下文（治"设定被概括掉"）。无则空串。"""
        getter = getattr(self.repo, "bible_sections_text", None)
        if getter is None:
            return ""
        try:
            return getter(sections, max_chars)
        except Exception:
            return ""

    def _bible_overview(self) -> str:
        """W1：全世界观各节 summary 速览（常驻注入，token 极省）。无 w1 行时空串。"""
        getter = getattr(self.repo, "bible_summaries_text", None)
        if getter is None:
            return ""
        try:
            return getter()
        except Exception:
            return ""

    # ================= JSON 工具 =================
    def _complete_json(self, system: str, user: str, expect_list: bool = False,
                       validate=None, retries: int = 0):
        """§14 深度校验闸门：可选 validate(data)->bool；不过则带"太单薄"反馈重生成，
        retries 次仍不过 → 返回 None（让调用方走确定性回退）。validate=None 时行为不变。"""
        if self.llm is None:
            return None
        cur_user = user
        for attempt in range(retries + 1):
            data = self._complete_json_once(system, cur_user, expect_list)
            if data is not None and (validate is None or validate(data)):
                return data
            if attempt < retries:
                cur_user = user + "\n\n[上次输出过于单薄/不完整或不合规，请重写得更具体、更完整、字段齐全]"
        return None if validate is not None else data

    def _complete_json_once(self, system: str, user: str, expect_list: bool = False):
        try:
            raw = self.llm.complete(system + " 输出必须是合法 json。", user)
        except Exception:
            return None
        text = (raw or "").strip().strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
        try:
            return json.loads(text)
        except Exception:
            o, c = (text.find("["), text.rfind("]")) if expect_list else (text.find("{"), text.rfind("}"))
            if 0 <= o < c:
                try:
                    return json.loads(text[o : c + 1])
                except Exception:
                    return None
            return None
