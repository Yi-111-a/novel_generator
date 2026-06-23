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
import re
import uuid

from .chapter_titles import repair_chapter_title, validate_chapter_title
from .disclosure import auto_schedule_disclosures
from .llm.base import LLMClient
from .models import Arc, ChapterPlan, Entity, GraphEdge, InventoryItem, Location, Part, Persona, RevealNode
from .narration.retrieval import build_context
from .narration.story_clock import fold_timeline, format_minutes
from .outline_validator import is_valid_outline
from .prompt_addons import ANTI_AI_FLAVOR_GUIDANCE
from .repository import Repository
from .story_contract import (
    contract_prompt_block,
    ensure_story_contract,
    first_arc_override,
    first_part_override,
    resolve_story_scale,
)

DEFAULT_PART_COUNT = 6          # 仅作无合同/异常 fallback；真实卷数由 StoryScale 决定
DEFAULT_ARCS_PER_PART = 2       # 每个 Part 切几个小部分
DEFAULT_ARC_CHAPTERS = 5        # 每个小部分目标章数（5-10）
DEFAULT_CHAPTER_SCENES = 3      # 每章目标场数（2-4）
BREATH_BEAT_TEXT = (
    "呼吸拍：不新增事件、不新增人物、不新增专有名词，只写动作细节、感官细节、无对白留白或物件细节，"
    "让上一拍的后果沉下来，章末停在一个具体物象/动作上。"
)

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
# 篇幅：作者手写长章（2800–3600 字/章），章纲按此排预算，让每章有余地铺垫世界与留白，
# 而非 2 拍 1500 字逼着快进（旧值整体 1300–2100 是为"治写太满"砍了 35%，与手写长章相悖）。
_ROLE_WORDS = {"setup": 3000, "rising": 3000, "twist": 3200, "climax": 3400, "resolution": 2800}

_ROLE_TITLE_HINT = {
    "setup": "用一处宁静的环境意象或物件",
    "rising": "用一个推进冲突的动作",
    "twist": "用一个带悬念的问句或反转意象",
    "climax": "用本章冲突最尖锐的焦点物/动作",
    "resolution": "用一个收束、留白的意象",
}


def _uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:6]}"


def _two_main_plus_breath(beats: list[str], max_mains: int = 4) -> list[str]:
    """Normalize chapter beats to N event beats (2..max_mains) + 1 trailing breath beat.

    末拍视作呼吸拍，其前各拍都是主拍。放宽到至多 max_mains 个主拍（治"2 拍 1500 字铺不开、
    逼章纲快进/什么都没交代"）：手写长章需要 3–4 个事件拍才能把世界铺开。
    仍保留 LLM 给的各主拍与呼吸拍具体内容；只在呼吸拍缺失/空时才落回 BREATH_BEAT_TEXT 占位符。
    旧版本主动把 LLM 输出包成占位符模板，等于把具体场景全替换掉，导致章末呼吸拍永远是规则文字。"""
    raw = [str(b).strip() for b in (beats or []) if str(b).strip()]
    if not raw:
        return list(_ROLE_BEATS["rising"][:2]) + [BREATH_BEAT_TEXT]
    # 末拍=呼吸拍，其余=主拍（至多 max_mains 个）
    if len(raw) == 1:
        mains, breath = list(raw), ""
    else:
        mains, breath = list(raw[:-1]), raw[-1]
    mains = mains[:max_mains]
    while len(mains) < 2:
        mains.append(_ROLE_BEATS["rising"][len(mains)])
    if not breath:
        breath = next((b for b in raw if "呼吸" in b or "留白" in b), "") or BREATH_BEAT_TEXT
    return mains + [breath]


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

# ③ 选角偏好：按本章冲突类型，优先挑角色名/职务里含这些关键词的势力成员（确定性、可空）
_CONFLICT_MEMBER_KEYWORDS = {
    "潜入任务": ["守卫", "哨", "巡", "看守", "门", "卫"],
    "心理博弈": ["情报", "谋", "军师", "祭司", "审", "探", "智"],
    "身份危机": ["探", "谍", "卧底", "审", "情报", "暗"],
    "立场抉择": ["首领", "长", "主", "议", "头", "尊"],
    "正面对峙": ["战", "卫", "执法", "军", "打手", "护", "锋"],
    "情感羁绊": [],
    "三方搅局": ["商", "掮", "走私", "中间", "贩", "舵"],
}


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
                 worldsmith=None, template_id: str = "", story_scale=None) -> None:
        self.repo = repo
        self.llm = llm
        self.theme = theme
        # ④ 角色孵化器：给定 worldsmith 时，新 Arc 开场可按需登场新角色（写进世界+账本+故事线）
        self.worldsmith = worldsmith
        # 群像上限：旧值 4 恰等于"3 主角 + 1 缺席名点人物"的种子规模 → 孵化每次都被堵死，
        # 全书就那几张脸（用户反馈）。提到 8，让每个 Part 能登场 1 个新面孔，壮大群像。
        self.roster_cap = 8
        # 题材模板：若选了模板，注入"章节钩子词/必备节拍维度/系统拟人"等结构性约束。
        from . import templates as _tmpls
        self.template = _tmpls.get(template_id)
        self.story_contract = ensure_story_contract(self.repo, template=self.template, theme=theme)
        contract_template_id = (self.story_contract or {}).get("template_id") or template_id
        self.story_scale = resolve_story_scale(story_scale, template_id=contract_template_id, contract=self.story_contract)

    # ================= 锁定后一次性：总纲 =================
    def build_master(
        self,
        part_count: int | None = None,
        arcs_per_part: int | None = None,
        chapter_scenes: int = DEFAULT_CHAPTER_SCENES,
        story_scale=None,
    ) -> dict:
        """生成揭示链 + Part 划分 + 地点 + 库存。幂等：已存在 parts 则跳过。"""
        if self.repo.list_parts():
            return {"skipped": True}

        if story_scale is not None:
            contract_template_id = (self.story_contract or {}).get("template_id") or ""
            self.story_scale = resolve_story_scale(story_scale, template_id=contract_template_id, contract=self.story_contract)
        scale = self.story_scale
        self.arcs_per_part = max(1, arcs_per_part if arcs_per_part is not None else scale.arcs_per_volume)
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
        n_parts = min(scale.volume_count_max, max(scale.volume_count_min, int(n_parts or scale.suggested_volume_count)))
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
        在末部里程碑章被选作 reveal_gate；触发时由章节揭示流程把该 fact
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
        # fact_id=None（与 clue 节点同构，揭示流程天然忽略未 discover 的节点，安全）。
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
        scale = self.story_scale
        if self.llm is None:
            return scale.suggested_volume_count
        data = self._complete_json(
            f"你为主题「{self.theme}」的长篇小说规划结构。当前体量档位为 {scale.id}，"
            f"全书应规划为 {scale.volume_count_min} 到 {scale.volume_count_max} 卷。"
            f"只输出 JSON：{{\"parts\": 整数({scale.volume_count_min}到{scale.volume_count_max})}}",
            "根据题材体量，建议把全书分成几个阶段性闭环卷？只输出 JSON。",
        )
        try:
            return int((data or {}).get("parts", scale.suggested_volume_count))
        except Exception:
            return scale.suggested_volume_count

    def _build_parts(self, n: int) -> list[Part]:
        fallback = self._fallback_part_specs(n)
        specs = self._llm_part_specs(n) or fallback
        if len(specs) < n:
            specs = specs + fallback[len(specs):]
        first = first_part_override(self.story_contract)
        if first and specs:
            specs[0] = {**specs[0], **first}
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
                key_twist=s.get("key_twist", ""),
                new_crisis_hook=s.get("new_crisis_hook", ""),
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
            # W0：复用 canon 实体（权威地点跨 Part 共享，不复制）
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
                    "如『既有地点·具体房间/街角/机构』）；**严禁**发明与设定冲突的新地名"
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
        region_names = self._split_region_names(base)
        if len(region_names) >= 2:
            return [
                {"name": name, "geo_full": f"{name}：本卷阶段性闭环的关键舞台，具体细节随章节滚动补足。",
                 "controlling_faction": "", "notable_items": []}
                for name in region_names[:3]
            ]
        return [
            {"name": base, "geo_full": f"{base}：本卷阶段性闭环的主舞台。",
             "controlling_faction": "", "notable_items": []},
            {"name": f"{base}外围", "geo_full": f"{base}外围：与主舞台相连的调查、追逐或反转发生地。",
             "controlling_faction": "", "notable_items": []},
        ]

    @staticmethod
    def _split_region_names(region: str) -> list[str]:
        return [
            p.strip()
            for p in re.split(r"[、,，/／;；]+", region or "")
            if p.strip() and not p.strip().startswith("故事地域之")
        ]

    @staticmethod
    def _fallback_region_for_volume(idx: int, spec: dict) -> str:
        allowed = [str(x).strip() for x in (spec.get("allowed") or []) if str(x).strip()]
        allowed_places = [x for x in allowed if Planner._looks_like_region_name(x)]
        if allowed_places:
            return "、".join(allowed_places[:3])
        text = " ".join(str(spec.get(k, "")) for k in (
            "title", "short_goal", "obstacle", "key_twist", "gain_and_hook", "goal"
        ))
        keyword_regions = [
            (("校车", "司机"), "明德小学、城南校车公司、事故路线"),
            (("器官", "医院", "移植"), "第三人民医院、旧器官移植中心、地下移植链"),
            (("烧纸", "我妈", "凶手", "旧坟"), "老家村镇、旧坟、族谱祠堂"),
            (("老板", "员工", "职场", "合同"), "替死公司、办公室、法务档案室"),
            (("爸爸不是爸爸", "父亲被替换", "女儿"), "女儿家、学校门口、监护权办公室"),
            (("全村", "村", "祠堂"), "旧村、祠堂、村委会"),
            (("沈知夏", "父亲", "旧案", "警号"), "江州市刑警支队、旧档案室、父亲旧案现场"),
            (("天命", "借寿", "命数"), "天命咨询公司、客户档案库、借寿现场"),
            (("地府", "仲裁", "阴司"), "地府仲裁庭、阴司案卷库、集体仲裁席"),
            (("林晚", "别墅", "差评"), "无忧售后服务有限公司、锦澜湾别墅区、江州市刑警支队"),
        ]
        for keys, region in keyword_regions:
            if any(k in text for k in keys):
                return region
        return f"第{idx}卷主要舞台"

    @staticmethod
    def _looks_like_region_name(name: str) -> bool:
        if any(bad in name for bad in ("差评", "失业", "凶手", "丈夫", "假林晚", "集体差评", "内鬼", "正面登场")):
            return False
        return any(mark in name for mark in (
            "公司", "局", "后台", "别墅", "地下室", "支队", "学校", "校车", "路线",
            "医院", "中心", "村", "坟", "祠堂", "办公室", "档案", "家", "门口",
            "现场", "庭", "阴司", "仲裁", "客户", "借寿"
        ))

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
        if part.sequence_order == 1 and seq == 1:
            locked = first_arc_override(self.story_contract, personas)
            if locked:
                spec = {**spec, **locked}
        focus = spec.get("focus_agents") or self._fallback_focus(personas, seq)
        valid_ids = {p.agent_id for p in personas}
        focus = [f for f in focus if f.get("agent_id") in valid_ids] or self._fallback_focus(personas, seq)
        target = int(spec.get("target_chapters", self.story_scale.chapter_target_per_arc))
        target = min(20, max(3, target))  # 章数由内容/AI 定，不再顶在 8 章（诸天售后重构 §1.2）
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
        auto_schedule_disclosures(self.repo)
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
        auto_schedule_disclosures(self.repo)
        return made

    def build_chapters_for_part(self, part_id: str) -> int:
        """为某一个 Part 生成其全部 Arc 的章纲。返回新建章数。"""
        made = 0
        for arc in self.repo.list_arcs(part_id):
            made += self.build_chapters_for_arc(arc)
        return made

    def build_lazy_outline(self) -> dict:
        """惰性大纲：锁定时只生成**总卷纲/全 Arc 骨架 + 第一卷第一个 Arc 的章纲**，
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
        auto_schedule_disclosures(self.repo)
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
        ch.cast = self._force_apprentice_cast(list(ch.cast or []), self._seed_apprentice_id(), hero_id)
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
            conflict_type=getattr(ch, "conflict_type", ""), pov_name=pov_name, cast_names=cast_names_str,
            cast_agent_ids=list(eligible), chapter_idx=max(0, ch.sequence_order - 1))
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
        ch.ending_hook = self._ending_hook(ch.role, dq, beats=beats, exit_state=ch.exit_state)
        ch.time_hint = self._time_constraint(ch.sequence_order)[1]
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

    def replan_chapter(self, chapter_id: str) -> ChapterPlan | None:
        """Regenerate one unwritten chapter from current, disclosure-safe context."""
        ch = self.repo.get_chapter_plan(chapter_id)
        if ch is None or ch.status == "done" or ch.audited:
            return None
        arc = self.repo.get_arc(ch.arc_id)
        part = self.repo.get_part(arc.part_id) if arc else None
        if arc is None or part is None:
            return None
        recent_qs = [
            row.dramatic_question
            for row in self.repo.list_chapter_plans()
            if row.sequence_order < ch.sequence_order and row.dramatic_question
        ]
        self._revise_one(part, arc, ch, recent_qs)
        auto_schedule_disclosures(self.repo)
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
        """W0 主角 POV 偏置：主角 eligible 时**恒主角主讲**（锁单视角·限制性第三人称）。
        返回 (lead, hero_ok)。主角不 eligible（藏未揭身份/缺席）→ 返回 (None, False)，
        交回退按 beat 多数（不强加偏置，绝不泄底）。
        注：旧版每全书第 4 章轮一个配角主讲（群像），与"全程锁主角视角"的写作纪律相悖，已关闭。"""
        if not (hero_id and hero_id in eligible):
            return None, False
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
        locked_unit = bool(
            part and part.sequence_order == 1 and arc.sequence_order == 1
            and ((self.story_contract or {}).get("active_unit") or {}).get("locked")
        )
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

        # ③ 势力/地理驱动选角（治"明明几十个人物卡、大纲永远只有两三个主角"）：
        #    cast = 主角(必) + 一个主线配角(续戏) + 预留 1-2 位给本章相关势力的核心成员。
        hero_first = [hero_id] if (hero_id and not _is_absent(hero_id)) else []
        focus_secondary = [pid for pid in focus_ids
                           if pid not in hero_first and not _is_absent(pid)]
        recent_cast = {aid for c in self.repo.list_chapter_plans()
                       if c.arc_id == arc.arc_id for aid in (c.cast or [])}
        relevant_fids = self._relevant_faction_ids(part, part_locs, hero_first + focus_secondary[:1])
        if not relevant_fids:
            # 地理未映射到势力时，按本卷序轮换一个势力，保证每章也能带出势力成员
            all_f = [f.faction_id for f in self.repo.list_factions()]
            if all_f:
                relevant_fids = {all_f[(arc.sequence_order or 0) % len(all_f)]}
        members = self._faction_member_personas(
            relevant_fids, conflict_type,
            set(hero_first + focus_secondary), recent_cast, idx)

        cast = list(hero_first)
        if focus_secondary:                     # 一个主线配角续戏（保连续性）
            cast.append(focus_secondary[0])
        for m in members[:2]:                    # 预留 1-2 位给势力成员（核心修复，先于其余主角）
            if len(cast) >= 4:
                break
            cast.append(m)
        if len(cast) < 3:                        # 仍有空位：补其余 focus / 非缺席配角（兼容无势力项目）
            fillers = [pid for pid in focus_secondary[1:] if pid not in cast] + \
                      [pid for pid in valid_ids if pid not in cast and not _is_absent(pid)
                       and not str(pid).startswith("named_")]
            for f in fillers:
                if len(cast) >= 3:
                    break
                cast.append(f)
        cast = list(dict.fromkeys(cast))[:4]
        apprentice_id = self._seed_apprentice_id()
        cast = self._force_apprentice_cast(cast, apprentice_id, hero_id)
        if locked_unit:
            cast = self._locked_unit_cast(idx, hero_id)

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
            pov_name=pov_name, cast_names=cast_names_str,
            cast_agent_ids=list(eligible), chapter_idx=idx)
        # 人物 motif 只是候选池，不等于本章全部随身出现。只有当前节拍明确点到的
        # 物品，或从上一章实际继承且未消耗的物品，才进入正文白名单。
        beat_blob = "\n".join(beats)
        explicit_items = []
        for oid in items:
            ent = self.repo.get_entity(oid)
            if ent and ent.name and ent.name in beat_blob:
                explicit_items.append(oid)
        items = self._dedup_by_name(explicit_items)
        items_present = self._dedup_by_name(carried + explicit_items)
        items_introduced = [oid for oid in explicit_items if oid not in carried]
        intro_beats = self._world_intro_beats(role, global_seq)
        if intro_beats and beats:
            beats[0] = f"{beats[0]}（顺带落入世界规则细节：{intro_beats[0]}）"
            beats = _two_main_plus_breath(beats)
        faction_pressure = "" if locked_unit else self._chapter_faction_pressure(loc, cast)
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
        ending_hook = self._ending_hook(role, dq, goal, beats=beats, exit_state=exit_state)
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
            time_hint=self._time_constraint(global_seq)[1],
            status="planned",
        )
        self.repo.upsert_chapter_plan(ch)
        return ch

    def _seed_apprentice_id(self) -> str:
        """Project-specific seed-persona guard: keep the protagonist's disciple in chapter casts."""
        p = self.repo.get_persona("p_gu")
        if p and not (p.arc_state or {}).get("absent"):
            return p.agent_id
        return ""

    def _locked_unit_cast(self, idx: int, hero_id: str | None) -> list[str]:
        preferred_by_chapter = [
            ["p_chen"],
            ["p_chen", "p_linwan"],
            ["p_chen", "p_linwan"],
            ["p_chen", "p_linwan"],
            ["p_chen", "p_shen", "p_linwan"],
            ["p_chen", "p_shen", "p_linwan"],
            ["p_chen", "p_shen", "p_linwan"],
            ["p_chen", "p_linwan", "p_lupan"],
        ]
        wanted = preferred_by_chapter[idx] if idx < len(preferred_by_chapter) else ["p_chen", "p_shen"]
        out: list[str] = []
        for aid in wanted:
            if self.repo.get_persona(aid) and aid not in out:
                out.append(aid)
        if hero_id and hero_id not in out:
            out.insert(0, hero_id)
        return out[:4]

    def _force_apprentice_cast(self, cast: list[str], apprentice_id: str, hero_id: str | None) -> list[str]:
        if not apprentice_id or apprentice_id in cast:
            return list(dict.fromkeys(cast))[:4]
        out = list(dict.fromkeys(cast))
        insert_at = 1 if hero_id and out and out[0] == hero_id else len(out)
        if len(out) < 4:
            out.insert(insert_at, apprentice_id)
        else:
            replace_at = next((i for i in range(len(out) - 1, -1, -1)
                               if out[i] not in {hero_id, apprentice_id}), len(out) - 1)
            out[replace_at] = apprentice_id
        return list(dict.fromkeys(out))[:4]

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
            ch.ending_hook = self._ending_hook(
                ch.role, ch.dramatic_question,
                ch.beat_goals[0] if ch.beat_goals else "",
                beats=ch.beat_goals, exit_state=ch.exit_state)
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

    def _time_constraint(self, chapter_seq: int | None) -> tuple[str, str]:
        """故事时钟（阶段2）：据 timeline 折出「上一章末时间」+「活跃死线」，生成
        本章的【时间·硬约束】提示块 + 落库用的 time_hint。

        治"规划层无时间观念 → 随手写一个更早的钟点 → 时间倒流被事后 fact_delta blocked"。
        无 timeline 数据时返回 ("", "")（空操作，不改变既有行为）。"""
        if chapter_seq is None:
            return "", ""
        folded = fold_timeline(self.repo.get_story_timeline(), before_chapter=int(chapter_seq))
        last_clock = folded.get("last_end_clock")
        last_text = folded.get("last_end_text") or ""
        actives = folded.get("active_deadlines") or []
        overdue = folded.get("overdue_deadlines") or []
        if last_clock is None and not actives and not overdue:
            return "", ""
        lines: list[str] = []
        hint_bits: list[str] = []
        if last_clock is not None:
            lines.append(
                f"上一章结束于故事时间【{last_text}】。本章时间**必须 ≥ 此刻、绝不可倒流**"
                f"（哪怕换地点/换视角，钟点也只能往后走）。")
            hint_bits.append(f"不早于{last_text}")
        # 方案二·到点收口：时刻已过却仍未了结的死线 → 本章必须正面交代它达成还是错过
        if overdue:
            od = overdue[0]
            lines.append(
                f"**死线「{od.get('label','')}」的时刻（{od.get('due_text','') or '约定时限'}）已经到/过了，"
                f"却还没了结**。本章必须正面把它收掉——明确写出它**达成还是错过**，并交代后果，"
                f"不要再绕开、拖着不收。")
            hint_bits.append(f"收掉死线「{od.get('label','')}」(达成或错过)")
        if actives:
            dl_parts = []
            for d in actives[:3]:
                due_text = d.get("due_text") or ""
                label = d.get("label") or ""
                seg = f"「{label}」" + (f"截止于{due_text}" if due_text else "（时限未明）")
                dl_parts.append(seg)
            lines.append(
                "当前有**活跃死线**：" + "；".join(dl_parts) +
                "。本章应朝最近的死线**实质逼近**（推进、消耗时间或迫近临界），"
                "并给出本章发生的大致钟点；不要让时间停滞或绕开死线另起无关支线。")
            nearest = actives[0]
            # 迫在眉睫（剩 ≤2 小时）→ 额外加紧迫感
            due = nearest.get("due")
            if last_clock is not None and isinstance(due, (int, float)) and 0 <= due - last_clock <= 120:
                lines.append(
                    f"死线「{nearest.get('label','')}」已**迫在眉睫**（仅剩约 {int(due - last_clock)} 分钟），"
                    f"本章应推到它的临界点上、准备收口。")
            if nearest.get("due_text"):
                hint_bits.append(f"朝死线「{nearest.get('label','')}」({nearest['due_text']})逼近")
            else:
                hint_bits.append(f"推进死线「{nearest.get('label','')}」")
        block = "\n【时间·硬约束（故事时钟）】\n" + "\n".join(lines) + "\n"
        hint = "；".join(hint_bits)
        return block, hint

    def _emergent_threads(self, lookback: int = 2, cap: int = 12) -> str:
        """最近 lookback 章**写出来才涌现**的关键叙事事实/未了线索（来自 fact_delta 的
        narrative_assertion 事实台账），喂给下一章规划。

        治"规划层只按静态大纲（arc 简介/揭示链/计划钩子）推演，看不到实际写出的具体人名、
        时间、钩子"——例如 ch8 正文涌现的"念念·校车·七点十五分""别让她上那辆校车"。
        让下一章承接实际写出的内容，而非另起一个不相干的新人物/新案子。"""
        accepted = self.repo.list_accepted_chapters()
        if not accepted:
            return ""
        max_no = max(int(getattr(a, "chapter_no", 0) or 0) for a in accepted)
        lo = max(1, max_no - lookback + 1)
        rows: list[tuple[int, str]] = []
        for f in self.repo.list_facts():
            if f.fact_type != "narrative_assertion":
                continue
            st = int(f.story_time or 0)
            if st < lo or st > max_no:
                continue
            struct = f.structured if isinstance(f.structured, dict) else {}
            for a in struct.get("assertions", []) or []:
                if a.get("fact_class") != "narration":
                    continue
                txt = (str(a.get("source_text") or "").strip()
                       or str(f.canonical_content or "").strip())
                if txt:
                    rows.append((st, txt))
        rows.sort(key=lambda r: -r[0])  # 最近一章的事实排在前
        seen: set[str] = set()
        out: list[str] = []
        for _st, txt in rows:
            if txt not in seen:
                seen.add(txt)
                out.append("· " + txt)
            if len(out) >= cap:
                break
        return "\n".join(out)

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

    def _prop_dossier_clause(self, cast_agent_ids: list[str],
                             loc_ids: list[str] | None = None,
                             carried_recent: list[str] | None = None) -> str:
        """道具档案：告诉 LLM 本章登场角色"口袋里现在有什么"+ 本地点固有什么 +
        近章已登场什么。防 LLM 每章现编新道具、不复用种子/前章已落库的物件。

        来源：
        - inventory.items_held_by(agent_id)：每个 cast 成员的持有道具
        - location.notable_items：本章地点的固有道具
        - 最近 3 章的 items_introduced：近场道具
        - entities 里的所有 type='object' 名字（兜底白名单）
        """
        nm = {e.entity_id: e.name for e in self.repo.list_entities()}
        cast_lines: list[str] = []
        for aid in (cast_agent_ids or []):
            persona = self.repo.get_persona(aid)
            if not persona:
                continue
            owned = self.repo.items_held_by(aid)
            if not owned:
                continue
            names = [nm.get(it.object_id, it.object_id) for it in owned]
            cast_lines.append(f"  · {persona.name}：{('、'.join(n for n in names if n))[:160]}")

        # 本章可选地点的固有道具（union 起来给 LLM 一个白名单）
        loc_items: list[str] = []
        seen_li: set[str] = set()
        for lid in (loc_ids or []):
            loc_obj = self.repo.get_location(lid) if hasattr(self.repo, "get_location") else None
            if not loc_obj:
                continue
            for oid in (loc_obj.notable_items or []):
                n = nm.get(oid, oid)
                if n and n not in seen_li:
                    seen_li.add(n)
                    loc_items.append(n)

        # 近 3 章已登场（拼凑成"近场记忆"）
        recent_intro: set[str] = set()
        recent = sorted(self.repo.list_chapter_plans(), key=lambda c: c.sequence_order)[-3:]
        for c in recent:
            for oid in (c.items_introduced or []):
                n = nm.get(oid)
                if n:
                    recent_intro.add(n)
        for oid in (carried_recent or []):
            n = nm.get(oid)
            if n:
                recent_intro.add(n)

        if not (cast_lines or loc_items or recent_intro):
            return ""

        parts = ["【本章道具·档案（**新道具必须有来源**：要么从下面挑，要么写明本场景的发现来历）】"]
        if cast_lines:
            parts.append("登场角色现持有的道具：")
            parts.extend(cast_lines)
        if loc_items:
            parts.append(f"本章地点固有道具（可被取用）：{('、'.join(loc_items))[:160]}")
        if recent_intro:
            parts.append(f"近 3 章已登场的道具（可复用，不要换名重写）：{('、'.join(recent_intro))[:160]}")
        parts.append(
            "**警示**：① 优先复用上面已有道具（写成「摸出怀里的XX」「从地缝抠出本地的XX」等），"
            "不要每章都凭空冒新名字；② 若必须新增，须在 beat 里写清楚来源（「墙缝里翻出」「仇人掉的」「系统兑换得到」等），"
            "props 字段统一用上面用过的中文名（不要为同一物件起多个变体名）。"
        )
        return "\n".join(parts) + "\n"

    def _value_budget_clause(self, part, arc) -> str:
        """数值预算闸：在使用数值系统的模板（如装逼打脸/反派养成器）下，
        给 LLM 算一份"本 Arc 在全书数值中的份额"，防 LLM 在前 1-2 个 Arc
        就把数值打满（曾出现 Arc 1 章 8 黑化值 90%，后续 7 个 Arc 无空间）。

        预算逻辑：全书 100% 数值额度，平摊到所有 active arc。
        本 Arc 是第 N/M 个 → 应在 ~[(N-1)/M, N/M] 区间内推进。
        """
        if self.template is None:
            return ""
        s = self.template.structural or {}
        if not (s.get("system_npc") or {}).get("enabled"):
            return ""
        all_arcs = []
        for p in self.repo.list_parts():
            all_arcs.extend(self.repo.list_arcs(p.part_id))
        if not all_arcs:
            return ""
        total = len(all_arcs)
        try:
            arc_idx = next(i for i, a in enumerate(all_arcs) if a.arc_id == arc.arc_id)
        except StopIteration:
            return ""
        # 每 Arc 大约 100/total %；本 Arc 应在 [floor, ceil] 区间内推进
        per_arc_pct = 100.0 / total
        floor_pct = int(arc_idx * per_arc_pct)
        ceil_pct = int((arc_idx + 1) * per_arc_pct)
        terms = self.story_contract.get("progress_terms") or s.get("progress_terms") or ["进度条", "奖励进度"]
        term_text = " / ".join(str(x) for x in terms[:6])
        return (
            f"【进度预算·硬约束】本题材的可见成长/系统进度只使用这些口径：{term_text}。"
            f"采用**全书 100% 总额、按 Arc 平摊**的预算制。本 Arc 是第 {arc_idx + 1}/{total} 个，"
            f"本章结束时总体进度应落在 **[{floor_pct}%, {ceil_pct}%]** 区间内。"
            f"**绝不可**在本 Arc 内把进度打超 {ceil_pct}%，给后面 Arc 留空间。"
            f"单章跃迁建议 +3~+8（小颗粒高频），不要动辄 +20/+30 把额度打爆。\n"
        )

    def _cast_dossier_clause(self, cast_agent_ids: list[str]) -> str:
        """章节生成时给 LLM 注入"本章登场角色身份档案"——治 LLM 看到名字列表
        猜不出"谁是谁"导致的张冠李戴（如把主角徒弟与某守陵氏成员混为一谈）。

        档案来源：character_cards（W4 已落 tier/one_liner/defining_trait/key_relation）。
        额外加"易混淆角色警示"：列出系统里所有 character 实体，把不在本 cast 的也
        简短点名，防 LLM 自由发挥时把场外角色的名字硬塞给场内主角的位置。"""
        if not cast_agent_ids:
            return ""
        cards_by_aid = {c.agent_id: c for c in self.repo.list_cards() if c.agent_id}
        lines: list[str] = []
        cast_names_set: set[str] = set()
        for aid in cast_agent_ids:
            card = cards_by_aid.get(aid)
            persona = self.repo.get_persona(aid)
            name = (card and card.name) or (persona and persona.name) or aid
            if not name:
                continue
            cast_names_set.add(name)
            tier = (card and card.tier) or ""
            one_liner = (card and card.one_liner) or (persona and persona.want) or ""
            key_rel = (card and card.key_relation) or ""
            bits = [f"**{name}**"]
            if tier:
                bits.append(f"（{tier}）")
            parts = [f"  · {''.join(bits)}"]
            if one_liner:
                parts.append(f"｜定位：{one_liner[:50]}")
            if key_rel:
                parts.append(f"｜关系：{key_rel[:50]}")
            lines.append("".join(parts))
        # 场外角色警示：所有 character 实体里不在 cast 的，列名 + 一句话定位，
        # 防 LLM 自由发挥时把"骨寒渊"等场外角色当成"主角徒弟"用。
        offstage: list[str] = []
        for e in self.repo.list_entities():
            if e.type != "character" or e.name in cast_names_set:
                continue
            ent_card = cards_by_aid.get(e.entity_id)
            ent_persona = self.repo.get_persona(e.entity_id)
            tag = (ent_card and ent_card.one_liner) or (ent_persona and ent_persona.want) or ""
            offstage.append(f"{e.name}（{tag[:30] or '场外'}）" if tag else e.name)
        offstage_clause = ""
        if offstage:
            offstage_clause = (
                "\n【场外角色（本章不可登场，但其名字也不可被借用给本章角色）】"
                + "、".join(offstage[:20])
                + "。**警示**：这些是另外的人物，与上面登场角色是不同个体；"
                "不得把他们的名字塞给「师父/徒弟/同伴」的位置——本章人物身份严格按上面档案。"
            )
        return (
            "【本章登场角色·身份档案（严格按此理解每人身份，**名字不可张冠李戴**）】\n"
            + "\n".join(lines)
            + offstage_clause
        )

    def _template_beat_clause(self) -> str:
        """题材模板的结构性约束：在 _chapter_spec 的 LLM 提示里硬性要求
        本章节拍里必须包含 payoff_beat（爽点）+ humor_beat（笑点）等模板规定的 beat 维度。
        未选模板则返回空串，不影响原链路。"""
        if self.template is None:
            return ""
        s = self.template.structural or {}
        must_have = s.get("chapter_must_have_beats") or []
        sys_npc = s.get("system_npc") or {}
        parts: list[str] = []
        if must_have:
            mh_desc = []
            for k in must_have:
                if k == "payoff_beat":
                    mh_desc.append("**payoff_beat（爽点）**：本章必有一拍是数值/进度条/胜负的即时兑现"
                                   "（如反派自爆、系统播报数值变化、立的 flag 兑现）；")
                elif k == "complaint_beat":
                    mh_desc.append("**complaint_beat（差评钩子）**：本章必有一拍围绕亡者差评/投诉内容展开，"
                                   "让读者明确这单售后要替谁讨公道；")
                elif k == "evidence_beat":
                    mh_desc.append("**evidence_beat（证据推进）**：本章必有一拍拿到可验证线索/证据，"
                                   "能把恶人从'死无对证'拖到台前；")
                elif k == "punishment_beat":
                    mh_desc.append("**punishment_beat（清算打脸）**：本章必有一拍让恶人露怯、破防、被反制"
                                   "或被因果后台标记；")
                elif k == "humor_beat":
                    mh_desc.append("**humor_beat（笑点）**：本章必有一拍是反差幽默"
                                   "（系统拟人吐槽 / 内心 OS 与外表反差 / 一本正经胡说八道）；")
                elif k == "breath_beat":
                    mh_desc.append("**breath_beat（呼吸拍）**：本章最后一拍必须是无新事件的沉淀拍，"
                                   "只写动作细节、感官细节、无对白留白或物件细节；")
                else:
                    mh_desc.append(f"**{k}**：模板要求本章必有该类节拍。")
            parts.append(
                "【模板·必备节拍维度】本章 beats 中必须覆盖以下维度："
                + " ".join(mh_desc)
                + "把 payoff/humor 写得**具体到事**，breath_beat 则必须具体到一个动作/物件/感官余波。"
            )
        if sys_npc.get("enabled"):
            sname = sys_npc.get("name") or "系统"
            parts.append(
                f"【模板·系统拟人】本世界存在一个会插话播报的拟人系统「{sname}」（官方文体、冷面、永远叫宿主）。"
                f"它**不是物理在场的角色**，而是主角脑内的播报源，可在节拍中出现："
                f"以「{sname}：叮——……」的播报方式作为爽点/笑点的承载，但**不要**把它列进 cast/POV。"
            )
        if not parts:
            return ""
        return "\n".join(parts)

    def _template_title_clause(self) -> str:
        """章节标题的钩子词约束：标题须命中模板预设的钩子词任一。"""
        if self.template is None:
            return ""
        hooks = (self.template.structural or {}).get("chapter_title_hooks") or []
        if not hooks:
            return ""
        return (
            "【模板·钩子词词库（可参考，不必每章命中；五法仍优先）】"
            + "、".join(hooks[:16])
            + "。【重要·多样性】避免连续多章用同一钩子词开头（如不要连续 3 章都「叮——」开头）；"
            "五种取名法在全书里要尽量轮换，避免单一法垄断。"
        )

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

    def _prev_chapter_tail(self, chapter_seq: int | None, max_chars: int = 220) -> str:
        """上一已接受章正文的**最后 1–2 段**，作为本章"无缝承接"的锚（方案一·强承接）。

        只有上一章**已写出正文**时才非空——批量预排章纲时（前一章还没写）返回 ""，
        故强承接在"写完一章→重规划下一章"的连写节奏里才生效（正是它该生效的时机）。"""
        if not chapter_seq or chapter_seq <= 1:
            return ""
        prev = next((a for a in self.repo.list_accepted_chapters()
                     if int(getattr(a, "chapter_no", 0) or 0) == chapter_seq - 1), None)
        prose = (getattr(prev, "prose", "") or "").strip() if prev else ""
        if not prose:
            return ""
        paras = [p.strip() for p in re.split(r"\n\s*\n", prose) if p.strip()]
        tail = "\n".join(paras[-2:]) if len(paras) >= 2 else (paras[-1] if paras else prose)
        return tail[-max_chars:]

    def _prev_anchor_terms(self, chapter_seq: int | None) -> list[str]:
        """上一章的结构性锚词（地点名 + 出场人物名）——用于检测本章是否"另起炉灶"
        并在必要时确定性地把承接补回 beat1。来自章纲（可靠），不靠正文分词。"""
        if not chapter_seq or chapter_seq <= 1:
            return []
        prev = next((c for c in self.repo.list_chapter_plans()
                     if c.sequence_order == chapter_seq - 1), None)
        if prev is None:
            return []
        nm = {e.entity_id: e.name for e in self.repo.list_entities()}
        terms: list[str] = []
        for lid in (prev.location_ids or []):
            if nm.get(lid):
                terms.append(nm[lid])
        for aid in (prev.cast or []):
            p = self.repo.get_persona(aid)
            if p and p.name:
                terms.append(p.name)
        return [t for t in dict.fromkeys(terms) if t]

    def _locked_unit_chapter_spec(self, part, arc: Arc, locs: list[tuple[str, str]],
                                  chapter_idx: int | None, pov_name: str = ""):
        if part is None or part.sequence_order != 1 or arc.sequence_order != 1:
            return None
        unit = (self.story_contract or {}).get("active_unit") or {}
        specs = unit.get("chapter_specs") or []
        if not unit.get("locked") or chapter_idx is None or chapter_idx >= len(specs):
            return None
        spec = specs[chapter_idx] or {}
        loc_hint = str(spec.get("loc_hint", "")).strip()
        loc = ""
        if loc_hint:
            loc = next((lid for lid, nm in locs if loc_hint in nm), "")
        if not loc and locs:
            loc = locs[0][0]
        beats = [str(x).strip() for x in (spec.get("beats") or []) if str(x).strip()]
        breath = beats[2] if len(beats) > 2 else BREATH_BEAT_TEXT
        beats = _two_main_plus_breath(beats[:2] + [breath])
        question = str(spec.get("question", "")).strip() or _ROLE_QUESTION.get("rising", "本章会如何推进？")
        exit_state = str(spec.get("exit", "")).strip() or _ROLE_EXIT.get("rising", "")
        props = [str(x).strip() for x in (spec.get("props") or []) if str(x).strip()]
        beat_povs = [pov_name] * len(beats) if pov_name else []
        return beats, loc, question, props, exit_state, beat_povs

    def _chapter_spec(self, part, arc: Arc, role: str, has_reveal: bool,
                      locs: list[tuple[str, str]], prev_loc: str | None,
                      prev_hook: str = "", recent_locs: list[str] | None = None,
                      conflict_type: str = "", pov_name: str = "", cast_names: str = "",
                      cast_agent_ids: list[str] | None = None,
                      chapter_idx: int | None = None,
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
        locked_spec = self._locked_unit_chapter_spec(part, arc, locs, chapter_idx, pov_name)
        if locked_spec is not None:
            return locked_spec
        # 缺席人物（已失踪/已消失/仅存于回忆/被追寻者）：beats 不得安排他们登场或说话
        absent_names = [e.name for e in self.repo.list_entities()
                        if e.type == "character" and (e.attributes or {}).get("absent")]
        absent_clause = (
            f"【缺席人物约束】以下人物当前是**缺席的**（已失踪/已消失/仅存于回忆或正被追寻）："
            f"{('、'.join(absent_names))}。本章节拍中**绝不可**安排他们现身、登场、开口说话或带路，"
            f"他们只能作为被追寻、被提及、被回忆的对象出现在线索里。"
            if absent_names else "")
        tone_clause = self._tone_beat_clause()
        contract_clause = contract_prompt_block(
            self.story_contract,
            part_seq=(part.sequence_order if part else None),
            chapter_idx=chapter_idx,
        )
        # 题材模板的结构性约束：必备节拍维度 + 系统拟人 NPC（仅在选了模板时非空）
        tmpl_clause = self._template_beat_clause()
        # 角色身份档案：让 LLM 严格按 character_card 理解谁是谁，防张冠李戴
        cast_dossier = self._cast_dossier_clause(cast_agent_ids or [])
        # 道具档案：登场人物持有/地点固有/近章登场——治 LLM 每章现编新道具
        prop_dossier = self._prop_dossier_clause(
            cast_agent_ids or [],
            loc_ids=[lid for lid, _ in (locs or [])],
        )
        # 数值预算闸：模板有系统数值（黑化值/装逼值/进度条）时，给 LLM 一个全书额度，
        # 防止单 Arc 把数值打到天花板（曾出现 Arc 1 章 8 已 90%，后 50 章无空间膨胀）。
        budget_clause = self._value_budget_clause(part, arc)
        antagonist_id, antagonist_clause = self._antagonist_clause()
        part_turn_clause = self._part_turn_clause(part, arc, role)
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
        emergent = self._emergent_threads()
        # 方案一·强承接：上一章实际写出的结尾 + 结构锚词（地点/人物）
        _cur_seq = (chapter_idx + 1) if chapter_idx is not None else None
        prev_tail = self._prev_chapter_tail(_cur_seq)
        prev_anchors = self._prev_anchor_terms(_cur_seq)
        # 故事时钟硬约束：本章 chapter_seq = chapter_idx + 1（全书章号）
        time_block, _time_hint = self._time_constraint(_cur_seq)
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
            # 方案一·强承接块：把上一章**实际结尾原文**摆出来，要求 beat1 从这个画面无缝接续。
            # 对转折/高潮/收束章升级为铁律（这几类最容易甩开前文另起场景）。
            continuity_block = ""
            if prev_tail:
                strict = role in ("twist", "climax", "resolution")
                continuity_block = (
                    f"\n【承接·硬约束（上一章的结尾原文）】\n「…{prev_tail}」\n"
                    f"**本章第一拍（beat1）必须从这个结尾画面无缝接续**——同一时间、同一地点、"
                    f"同一批在场人物往下写，先把上一章悬在半空的动作/对峙/疑问推进下去，"
                    f"**绝不可**跳到另一个场景、另一拨人、或回到更早已写过的情节另起炉灶。"
                    + ("**这是本章不可违背的铁律**（收束/高潮章尤其要正面把上一章的钩子兑现，不要绕开）。\n"
                       if strict else "\n"))
            prior_block = (
                f"\n【前几章已经发生过的（核心动作/地点/手段——本章务必避开，不要重复）】\n{prior}\n"
                if prior else "")
            # 治本：把上一章**实际写出**的关键事实/未了线索喂进规划，让章纲承接涌现内容
            # （具体人名/时间/钩子），而非另起一个不相干的新案子（如丢掉"念念·7:15"另编张姓学生）。
            emergent_block = (
                f"\n【上一章已写出、本章必须承接的关键事实与未了线索（**直接推进这些具体的人名/时间/物件/钩子**，"
                f"不要丢掉它们、也不要另起一个不相干的新人物或新案子）】\n{emergent}\n"
                if emergent else "")

            def _ok(d):  # §14 深度闸门：3–4 主拍 + 1 呼吸拍（共 4–5 拍，向下兼容 3 拍），问题与出口非空
                if not isinstance(d, dict):
                    return False
                bs = [str(x).strip() for x in (d.get("beats") or []) if str(x).strip()]
                if not (3 <= len(bs) <= 5 and len(set(bs)) == len(bs)
                        and str(d.get("question", "")).strip() and str(d.get("exit_state", "")).strip()):
                    return False
                # 呼吸拍占位符闸：末拍（呼吸拍）不得复述规则元描述
                breath = bs[-1]
                placeholder_markers = (
                    "呼吸拍：", "不新增事件", "不新增人物", "不新增专有名词",
                    "只写动作细节", "只写感官细节", "无对白留白",
                    "breath_beat", "（呼吸拍）",
                )
                if any(m in breath for m in placeholder_markers):
                    return False
                return True

            # W6 RAG：按本章可选地点 + cast 人物检索相关子图。
            # 加 cast agent_ids 作种子：P2 直接带回每人的 character_card snippet
            # （含 one_liner/backstory/弧线/语域/口头禅），P4 经 owns/has_member 边
            # 1-hop 扩展到主角持有道具与所属势力——治"LLM 不知道主角口袋里啥/谁的徒弟"。
            _plan_seeds: set[str] = set()
            for lid, _nm in locs:
                _plan_seeds.add(lid)
            for aid in (cast_agent_ids or []):
                if aid:
                    _plan_seeds.add(aid)
            if antagonist_id:
                _plan_seeds.add(antagonist_id)
            # 规划发现态：脱敏（不带小传/谜底）但**不上硬白名单**，保留图谱扩展，
            # 让 planner 仍能从种子 1-hop 带出主角道具/所属势力，却看不到未来章答案。
            _plan_chapter_seq = (chapter_idx + 1) if chapter_idx is not None else None
            rag_ctx = build_context(self.repo, _plan_seeds, budget=2500,
                                    beat_text=arc.summary or (part.goal if part else ""),
                                    chapter_seq=_plan_chapter_seq,
                                    exclude_future=True)
            bible_block = (
                f"【世界观（据此理解所有设定，**勿望文生义**；专有名词、绰号、隐喻和地理称呼"
                f"只按设定文本中已有解释处理）】\n{rag_ctx}\n\n" if rag_ctx else "")
            def _contract_ok(d):
                return _ok(d) and is_valid_outline(
                    self.story_contract,
                    d,
                    part_seq=(part.sequence_order if part else None),
                )

            data = self._complete_json(
                bible_block
                + contract_clause
                + f"你是资深小说编剧，为长篇小说规划**下一章的章纲**。本小部分梗概："
                f"{arc.summary or (part.goal if part else '')}。本章功能：{_ROLE_CN.get(role, role)}。"
                f"前情（最近发生）：{recent}。{hook_hint}{reveal_hint}\n"
                f"{continuity_block}"
                f"{emergent_block}"
                f"{time_block}"
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
                f"{cast_dossier}\n{prop_dossier}{budget_clause}{antagonist_clause}{part_turn_clause}"
                f"{pov_clause}{ct_clause}{tone_clause}{tmpl_clause}{absent_clause}{arc_clause}{moral_clause}\n"
                f"【地点】可选地点：{loc_names}。上一章在「{prev_name or '未定'}」，最近几章待过："
                f"{('、'.join(recent_loc_names) or '（无）')}。**本章必须换一个不同的地点**"
                f"（除非剧情有非留不可的强理由）。所有节拍都只发生在你选定的**这一个**地点内，"
                f"**不得**在 beat 里写到该地点之外的场所（要换地方就是另起一章的事）。\n"
                f"【地点·铁律】你填的 location 必须**就是 beat 当前正在发生的那个地点**——"
                f"**绝不可**把场景写在某地、location 却填另一个（哪怕承接上一章在某处，也要把 location "
                f"直接选成那处，而不是嘴上换地、身体没动）。把别处只能当作被提及/回忆的对象，不得当作当前场景描写。\n"
                f"【道具】props = 这些节拍中**首次出现或易手的具体物件**（用中文名）；凡 beat 里被人"
                f"拿到/交出/发现的东西都要登进 props（供登记来源，杜绝凭空出现）。\n"
                f"【输出·硬契约】输出 4 个 beat：**3 个主拍 + 1 个呼吸拍**。"
                f"第 1-3 拍是各异且递进的主拍，每拍只推进一个核心事件（多一个主拍是给本章铺垫世界/"
                f"人物/规则的余地，别把三拍写成同一件事）；"
                f"第 4 拍（末拍）必须是 breath_beat（呼吸拍）。**呼吸拍输出格式·硬约束**："
                f"  ① **直接写出具体场景文字**，约 30-60 字，描写一个具体动作 + 一个感官细节，章末停在一个具体物象上。"
                f"  ② **不要复述规则文字**——禁止输出『呼吸拍：不新增事件...』『只写动作细节/感官细节...』这类"
                f"对呼吸拍本身的元描述（这类文字一旦出现即视作 LLM 偷懒，违反契约）。"
                f"  ③ 不得在呼吸拍里新增人物/地点/道具/势力/术语/信息点。"
                f"  ④ 示范：「萧守拙蹲在泉边洗手，血色在水里散成一缕，他盯着指甲缝里嵌的暗红泥渍」"
                f"——这种就是合格的 breath_beat：具体、感官、无新增、停在物象上。"
                f"**beat_povs**=与 beats 等长的数组，"
                f"每项是对应 beat 的**视角角色名**（这一拍以谁的视角写）；一个**悬而待答的是非/抉择型**戏剧问题 question；"
                f"一句**具体到本章**的出口状态 exit_state（到本章结尾世界/角色/关系/认知发生的**外部可观测变化**："
                f"物/位置/关系/局面/被揭开的认知/能力伤情/关系态度；**应含 1 条具体角色状态变化**"
                f"（能力、伤情、关系或新认知均可，但必须可被旁人观察或从行动中验证，例如『丹田裂一道纹』"
                f"『开始警惕铁如山』『学会用怂功护丹田』）。**不要**写'一个关键物易手'这种放之四海皆准的空话，"
                f"也**禁止**写成『主角悟了/想通了某个道理』这类抽象内心升华或主题点题）；props 清单。\n"
                f"只输出 JSON：{{\"beats\":[\"…\",\"…\",\"…\",\"…\"],"
                f"\"beat_povs\":[\"角色名\",\"角色名\",\"角色名\",\"角色名\"],"
                f"\"location\":\"地点名\",\"question\":\"…\",\"exit_state\":\"…\",\"props\":[\"…\"]}}"
                + ANTI_AI_FLAVOR_GUIDANCE,
                "只输出 JSON。", validate=_contract_ok, retries=1,
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
                # 方案一·承接兜底：本章仍未承接上一章实际结尾（beat 不含任何上一章锚词）→
                # 确定性把承接补回 beat1，避免"另起场景"漂走（沿用 prev_hook 前缀的同一手法）。
                # 覆盖所有 role：新弧开篇常是 setup，却同样要接住上一章（尤其上一章是高潮/反转结尾）。
                if (prev_tail and beats and prev_anchors
                        and not any(t in b for b in beats for t in prev_anchors)):
                    _scene = prev_anchors[0]
                    _hk = (prev_hook[:18] + "…") if prev_hook else "上一章悬而未决的局面"
                    beats[0] = f"紧接上一章结尾（{_scene}，{_hk}）往下写：" + beats[0]
                if 3 <= len(beats) <= 5:
                    return (_two_main_plus_breath(beats), loc, (dq or _ROLE_QUESTION.get(role, "本章会如何收束？")),
                            props[:6], ex or _ROLE_EXIT.get(role, ""), povs[:5])
        # 离线/失败回退：按 role 给 3 个各异的递进节拍（首拍可接钩）
        topic = (arc.summary or (part.goal if part else "") or (part.title if part else ""))[:16]
        loc = self._rotate_location(locs, recent_locs, prev_loc)
        base_beats = list(_ROLE_BEATS.get(role, _ROLE_BEATS["rising"]))
        beats = _two_main_plus_breath(base_beats[:2] + [BREATH_BEAT_TEXT])
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
                # ④ 把控制势力的诉求带进大纲上下文，让本章冲突据"谁的地盘、他们要什么"展开
                goal = (getattr(faction, "goals", "") or getattr(faction, "ideology", "") or "").strip()
                pieces.append(f"{faction.name}控制此地" + (f"（其诉求：{goal[:40]}）" if goal else ""))
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

    def _part_turn_clause(self, part: Part | None, arc: Arc, role: str) -> str:
        if part is None:
            return ""
        lines: list[str] = []
        key_twist = (getattr(part, "key_twist", "") or "").strip()
        crisis = (getattr(part, "new_crisis_hook", "") or "").strip()
        if key_twist and role in ("twist", "climax", "resolution"):
            lines.append(
                f"【本部大反转兑现】本章接近/处于收束位，必须把本部 key_twist 往台前推：{key_twist}。"
                "不要只提名词，要让一件行动或证据改变角色判断。"
            )
        if crisis and role == "resolution" and arc.sequence_order >= getattr(self, "arcs_per_part", DEFAULT_ARCS_PER_PART):
            lines.append(
                f"【部末新危机】本章收束后必须埋下抛给下一部的新危机：{crisis}。"
                "危机要落成可见征兆，不要提前解决。"
            )
        return ("\n".join(lines) + "\n") if lines else ""

    def _antagonist_clause(self) -> tuple[str, str]:
        antagonist_id, profile = self._ensure_antagonist_profile()
        if not antagonist_id:
            return "", ""
        name = str(profile.get("name") or antagonist_id).strip()
        goal = str(profile.get("goal") or "压迫主角目标的既有秩序").strip()
        methods = str(profile.get("methods") or "借势力规则、名声和资源差制造阻力").strip()
        final = str(profile.get("final_confrontation") or "主角能公开撕开其规则优势时进入最终对决").strip()
        clause = (
            f"【最大反派锚点】当前主线对抗锚点：{name}。"
            f"反派目标：{goal}；典型手段：{methods}；最终对决条件：{final}。"
            f"本章至少用一个可见动作/证据/关系变化，推进主角与{name}的距离或关系；"
            "可以是逼近、误判、被其规则压迫、拿到能反制它的一小块筹码，禁止只空喊反派名。\n"
        )
        return antagonist_id, clause

    def _ensure_antagonist_profile(self) -> tuple[str, dict]:
        wb = self.repo.get_world_bible() if hasattr(self.repo, "get_world_bible") else {}
        antagonist_id = str((wb or {}).get("antagonist_id", "") or "").strip()
        profile = (wb or {}).get("antagonist_profile") if isinstance(wb, dict) else {}
        if antagonist_id and isinstance(profile, dict):
            self._upsert_antagonist_edge(antagonist_id, profile)
            return antagonist_id, profile
        antagonist_id, profile = self._infer_antagonist_profile(wb or {})
        if antagonist_id:
            if hasattr(self.repo, "set_world_bible_antagonist"):
                self.repo.set_world_bible_antagonist(antagonist_id, profile)
            self._upsert_antagonist_edge(antagonist_id, profile)
        return antagonist_id, profile

    def _infer_antagonist_profile(self, wb: dict) -> tuple[str, dict]:
        keywords = ("反派", "明序", "九大正派", "正派", "宿敌", "敌")
        best: tuple[int, str, dict] | None = None
        for f in self.repo.list_factions():
            blob = " ".join([
                f.name or "", f.ideology or "", f.goals or "", f.methods or "",
                f.summary or "", f.detail or "", f.secret or "",
            ])
            score = sum(2 if k in (f.name or "") else 1 for k in keywords if k in blob)
            if score <= 0:
                continue
            profile = {
                "name": f.name,
                "kind": "faction",
                "goal": f.goals or f.ideology or f.summary,
                "methods": f.methods or "以宗门秩序、名声和规训压制异端",
                "final_confrontation": f.secret or "主角拿到能公开撕开其正派叙事的证据与实力",
            }
            cand = (score, f.faction_id, profile)
            if best is None or cand[0] > best[0]:
                best = cand
        if best:
            return best[1], best[2]
        for e in self.repo.list_entities():
            blob = f"{e.name} {json.dumps(e.attributes or {}, ensure_ascii=False)}"
            score = sum(1 for k in keywords if k in blob)
            if score <= 0:
                continue
            profile = {
                "name": e.name,
                "kind": e.type,
                "goal": "阻止主角达成核心目标",
                "methods": "借身份差、信息差和既有秩序施压",
                "final_confrontation": "主角能以证据或实力正面反制时",
            }
            return e.entity_id, profile
        setting = str((wb or {}).get("setting_core", "") or "")
        if any(k in setting for k in keywords):
            return "antagonist_main", {
                "name": "最大反派势力",
                "kind": "implicit",
                "goal": "维持压迫主角目标的旧秩序",
                "methods": "利用名分、规训、资源差和舆论优势",
                "final_confrontation": "主角能证明其秩序伪善并拥有反制筹码时",
            }
        return "", {}

    def _upsert_antagonist_edge(self, antagonist_id: str, profile: dict) -> None:
        personas = self.repo.list_personas()
        if not (antagonist_id and personas):
            return
        hero_id = personas[0].agent_id
        self.repo.upsert_edge(GraphEdge(
            src=antagonist_id,
            rel="opposes",
            dst=hero_id,
            meta={
                "source": "planner_antagonist",
                "name": profile.get("name", ""),
                "goal": profile.get("goal", ""),
                "methods": profile.get("methods", ""),
                "final_confrontation": profile.get("final_confrontation", ""),
            },
            intensity=0.85,
        ))

    # ③ 势力/地理驱动选角的两个辅助
    def _resolve_faction(self, ref: str) -> str:
        """把"控制势力"引用解析成真实 faction_id：先按 id 精确，再按势力名包含匹配。解析不出则空。
        （loc.controlling_faction 常是自由文本如『天枢议会（名义上）』『无』，不能直接当 id 用。）"""
        if not ref:
            return ""
        facs = self.repo.list_factions()
        for f in facs:
            if f.faction_id == ref:
                return f.faction_id
        for f in facs:
            if f.name and (f.name in ref or ref in f.name):
                return f.faction_id
        return ""

    def _relevant_faction_ids(self, part, part_locs, cast) -> set:
        """本章相关势力 = 本Part地点的控制势力 ∪ part.region所属势力 ∪ 已在场者所属势力。
        全部归一成真实 faction_id。无势力（旧项目）则返回空集 → 调用方退回原选角逻辑。"""
        loc_ids = {lid for lid, _ in (part_locs or [])}
        fids: set = set()
        for f in self.repo.list_factions():
            terr = set(f.territory or [])
            region = getattr(part, "region", "") if part else ""
            if (region and region in terr) or (terr & loc_ids):
                fids.add(f.faction_id)
        for lid in loc_ids:
            loc = self.repo.get_location(lid) if lid else None
            cf = getattr(loc, "controlling_faction", "") if loc else ""
            rid = self._resolve_faction(cf)
            if rid:
                fids.add(rid)
        for aid in (cast or []):
            ent = self.repo.get_entity(aid)
            cf = (ent.attributes or {}).get("faction_id") if ent else ""
            if cf:
                fids.add(cf)
        return fids

    def _faction_member_personas(self, faction_ids: set, conflict_type: str,
                                 exclude: set, recent_cast: set, idx: int) -> list[str]:
        """从相关势力的核心成员（已 promote 成 persona）里，按冲突类型偏好 + 近章去重 + idx 轮换，
        返回候选 agent_id 列表，供本章 cast 补位。"""
        if not faction_ids:
            return []
        persona_ids = {p.agent_id for p in self.repo.list_personas()}
        kws = _CONFLICT_MEMBER_KEYWORDS.get(conflict_type, [])
        pool: list[tuple[int, str]] = []  # (冲突匹配分, agent_id)
        for fac in self.repo.list_factions():
            if fac.faction_id not in faction_ids:
                continue
            for m in (fac.key_members or []):
                aid = m.get("agent_id")
                if not aid or aid in exclude or aid not in persona_ids:
                    continue
                role = str(m.get("role", "")) + str(m.get("note", ""))
                pool.append((1 if any(k in role for k in kws) else 0, aid))
        if not pool:
            return []
        # 近章用过的先排除（保多样）；全被排除则放开
        fresh = [t for t in pool if t[1] not in recent_cast] or pool
        fresh.sort(key=lambda t: t[0], reverse=True)   # 冲突类型匹配者优先
        ordered = [aid for _, aid in fresh]
        k = idx % len(ordered)                          # idx 轮换：每章换不同成员
        return ordered[k:] + ordered[:k]

    def _dominant_beat_loc(self, beats: list[str], locs: list[tuple[str, str]]) -> str:
        """问题1：判定这些 beat（取**首拍**=当前场景设定）实际发生在哪个可选地点。

        按地点名及其 `·` 分段与首拍文本的二字-gram 覆盖度取最像的一个（地名常带前缀/后缀，
        如 location='主地点·二楼包厢' 而正文写'主地点二楼包厢'，故用分段覆盖度而非全等）。
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

    def _ending_hook(
        self,
        role: str,
        dq: str,
        goal: str = "",
        *,
        beats: list[str] | None = None,
        exit_state: str = "",
    ) -> str:
        """§13.2 生成本章章末钩子（指向后文的悬念）。LLM 优先，失败回退确定性模板。
        钩子不能凭空——以本章未决的戏剧问题 dq 为种子，按 role 给出前瞻式悬念。"""
        beats = [str(b).strip() for b in (beats or []) if str(b).strip()]
        seed = (dq or exit_state or goal or "未决的局面").strip().rstrip("？?")
        if self.llm is not None:
            data = self._complete_json(
                "你为小说章节设计一句**章末钩子**（act-out/button）：留一个悬而未决的悬念，"
                f"勾住读者读下一章。钩子类型：{_HOOK_CN.get(_HOOK_TYPE.get(role,'new_question'))}。"
                "钩子必须指向后文（呼应本章未答的问题或抛出新威胁/新疑问），一句话，不剧透答案。"
                "必须贴住本章最后已经发生的具体动作/状态，不能泛泛而谈。"
                "只输出 JSON：{\"hook\":\"…\"}",
                f"本章未决问题：{dq or '（无）'}\n"
                f"本章 beats：{'；'.join(beats) or goal or '（无）'}\n"
                f"本章出口状态：{exit_state or '（无）'}\n"
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
        recent = [c.title for c in self.repo.list_chapter_plans() if c.title]
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
            tmpl_title_clause = self._template_title_clause()
            pov_persona = self.repo.get_persona(ch.pov_agent) if ch.pov_agent else None
            pov_display = (
                self.repo.get_character_display_name(ch.pov_agent, pov_persona.name)
                if pov_persona and ch.pov_agent else ""
            )
            # 取名规则法（与 drafts.py 保持一致）
            if self.template is not None:
                rules_block = (
                    "【取名五法·至少命中一种，可叠加】\n"
                    "①**主视角角色（pov_agent）的第一人称嚣张台词直出**：「我个人建议你们一起上」「老子打劫的」「我啥也没干」式——本章 pov_agent 的一句话当章名，徒弟/配角的话不算。\n"
                    "②**网络梗/流行语混入修仙语境**：「先定一个小目标」「见过仙女吗？」「你咋不上天呢」式——现代俗语硬塞玄幻场景。\n"
                    "③**名著或流行 IP 荒诞串场**：悟空/李白/诸葛亮/秋名山车神/萧炎/旺仔等可借用但**不照搬原句**"
                    "（「萧炎同款，肯定牛逼」「吃俺老孙一棒！」「旺仔小馒头」式）。\n"
                    "④**反话讽刺**：用正面话指阴狠事（「好残忍的小子」「一看就是个老实孩子」「碰瓷界最强王者」式）。\n"
                    "⑤**悬念钩子**：「见证奇迹的时刻」「成长大礼包」「隐藏任务奖励」「我有一法」式——预告爽点不剧透具体。\n"
                    "【腔调】口语化、嚣张、自嘲、贱兮兮；多用感叹号问号短句爆破；偶尔故意土味书面语制造反差"
                    "（「斯人已去，唯留其物」「礼尚往来」式半文不白）。\n"
                    "【反例·绝对避开】平铺直叙正经标题（「激战来临」「突破境界」「危机降临」）/ 纯物件地点名"
                    "（「残页」「泉眼」「碎剑」死名）/ 抽象主题词（「成长」「真相」「命运」空泛）。\n"
                )
            else:
                rules_block = (
                    "【取名法】用主角的一句话/一个反转/一句金句/一组反差短语取名"
                    "（如「他没说话」「最后一封信」「轮到你了」「我不是英雄」）。不要平铺直叙。\n"
                )
            data = self._complete_json(
                "你为小说本章取一个**有钩子、贱兮兮、像 meme**的中文章名——爽文读者扫一眼就想点进来。\n"
                + rules_block +
                "【硬约束】长度 3-18 字（可带「叮——」、感叹号、问号、加号、数字、点号）；"
                f"风格倾向：{hint}；不得与主题词或近期章名重复：主题「{self.theme}」、"
                f"已用词「{ban}」、近期章名「{avoid}」；{tmpl_title_clause}"
                "\n只输出 JSON：{\"title\":\"…\"}",
                f"本章 pov_agent：{pov_display or '（未知）'}\n本章正文片段：{material}\n本章目标：{'；'.join(ch.beat_goals)}\n只输出 JSON。",
            )
            title = str((data or {}).get("title", "")).strip().strip("《》\"'")
            title_validation = validate_chapter_title(title, recent)
            # 去重/黑名单复查：命中则再要一次更强约束，仍不行就退确定性名
            if title and (not title_validation.ok or any(b in title for b in blacklist)):
                data = self._complete_json(
                    f"上一个章名「{title}」与既有章名或主题词重复了。换一个**完全不同**的有钩子的中文章名。"
                    f"3-10 字，**绝不**用孤立物件名当全章题。"
                    f"禁用词：{('、'.join(sorted(blacklist)) or '（无）')}；不得与这些重复：{avoid}。"
                    "只输出 JSON：{\"title\":\"…\"}",
                    f"本章正文片段：{material}\n只输出 JSON。",
                )
                t2 = str((data or {}).get("title", "")).strip().strip("《》\"'")
                t2_validation = validate_chapter_title(t2, recent)
                if t2 and t2_validation.ok and not any(b in t2 for b in blacklist):
                    title = t2_validation.normalized
                elif not title_validation.ok or any(b in title for b in blacklist):
                    title = ""
            elif title_validation.ok:
                title = title_validation.normalized
        if not title:  # 无 LLM / 反复重复：朴素章号（确定性、永不雷同）
            title = repair_chapter_title("", ch.sequence_order, recent)
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
        scale = self.story_scale
        # W6 RAG：全书规划级——无具体种子，注入世界观 summary + 势力速览
        rag_ctx = build_context(self.repo, set(), budget=3000)
        bible_block = (
            f"【世界圣经（充分使用其中地理/势力/历史/专有名词，勿概括）】：\n{rag_ctx}\n\n" if rag_ctx else "")
        contract_block = contract_prompt_block(self.story_contract)
        volume_contract = self._volume_planning_contract(n)
        output_schema = {
            "title": "卷名；如果 input.volume_blueprint[].locked_title 非空，必须完全等于 locked_title",
            "goal": "按五要素写成本卷阶段性闭环；不要只写主线推进",
            "region": "本卷主要发生地域；优先引用世界圣经和 allowed 中的真实地点",
            "key_twist": "本卷关键反转，必须能被具体事件兑现",
            "new_crisis_hook": "卷末收获和抛给下一卷的新危机",
            "short_goal": "可选；本卷短期目标",
            "obstacle": "可选；阻碍势力/困难",
            "conflict_chain": "可选数组；连续递进冲突事件",
            "gain_and_hook": "可选；卷末收获+下一卷危机",
        }

        def _ok_part_list(data):
            if not isinstance(data, list) or len(data) != n:
                return False
            for idx, item in enumerate(data[:n], 1):
                if not isinstance(item, dict):
                    return False
                normalized = self._normalize_llm_part_spec(item, idx)
                if not is_valid_outline(self.story_contract, normalized, part_seq=idx):
                    return False
                if not self._matches_volume_blueprint(normalized, idx):
                    return False
            return True

        data = self._complete_json(
            f"{bible_block}{contract_block}你为主题「{wb_theme}」的长篇小说补全固定卷纲。"
            f"这是强约束 JSON 合同，不是建议：\n"
            f"{json.dumps(volume_contract, ensure_ascii=False, indent=2)}\n\n"
            f"输出 schema：\n{json.dumps(output_schema, ensure_ascii=False, indent=2)}\n\n"
            f"规则：\n"
            f"1. 输出必须恰好 {n} 个对象，顺序与 input.volume_blueprint 完全一致。\n"
            f"2. 如果 locked_title 非空，title 必须完全等于 locked_title，不得改名、换意象或合并卷。\n"
            f"3. 每卷必须是阶段性闭环，goal 必须包含短期目标、阻碍、连续递进冲突、关键反转、卷末收获+下一卷危机。\n"
            f"4. allowed 是本卷可正面展开内容；shadow_only 只能低显著度埋影子，不能成为 title/goal/key_twist；forbidden 不能出现。\n"
            f"5. planning_mode=rolling：这里只做总卷纲，不生成后续卷正文级章节。",
            f"请按合同补全恰好 {n} 个卷纲。只输出 JSON 数组，不要解释。",
            expect_list=True, validate=_ok_part_list, retries=1,
        )
        if isinstance(data, list) and data:
            out = [self._normalize_llm_part_spec(d, idx) for idx, d in enumerate(data, 1) if isinstance(d, dict)][:n]
            return out if len(out) == n else None
        return None

    def _volume_planning_contract(self, n: int) -> dict:
        vols = [v for v in ((self.story_contract or {}).get("volume_blueprint") or []) if isinstance(v, dict)]
        blueprint = []
        for idx in range(1, n + 1):
            v = vols[idx - 1] if idx <= len(vols) else {}
            blueprint.append(
                {
                    "seq": idx,
                    "locked_title": str(v.get("title") or "").strip(),
                    "allowed": list(v.get("allowed") or []),
                    "shadow_only": list(v.get("shadow_only") or []),
                    "forbidden": list(v.get("forbidden") or []),
                    "seed_short_goal": str(v.get("short_goal") or "").strip(),
                    "seed_obstacle": str(v.get("obstacle") or "").strip(),
                    "seed_conflict_chain": list(v.get("conflict_chain") or []),
                    "seed_key_twist": str(v.get("key_twist") or "").strip(),
                    "seed_gain_and_hook": str(v.get("gain_and_hook") or "").strip(),
                }
            )
        return {
            "story_scale": self.story_scale.to_dict(),
            "volume_count": n,
            "planning_mode": self.story_scale.planning_mode,
            "volume_blueprint": blueprint,
        }

    def _normalize_llm_part_spec(self, item: dict, seq: int) -> dict:
        out = dict(item or {})
        vols = [v for v in ((self.story_contract or {}).get("volume_blueprint") or []) if isinstance(v, dict)]
        volume = vols[seq - 1] if 1 <= seq <= len(vols) else {}
        chain = out.get("conflict_chain") or []
        if not isinstance(chain, list):
            chain = [str(chain)]
        if not str(out.get("goal") or "").strip():
            pieces = [
                ("本卷短期目标", out.get("short_goal") or volume.get("short_goal", "")),
                ("阻碍势力/困难", out.get("obstacle") or volume.get("obstacle", "")),
                ("连续递进冲突事件", "；".join(str(x) for x in (chain or volume.get("conflict_chain", [])) if str(x).strip())),
                ("关键反转", out.get("key_twist") or volume.get("key_twist", "")),
                ("卷末收获+下一卷危机", out.get("gain_and_hook") or out.get("new_crisis_hook") or volume.get("gain_and_hook", "")),
            ]
            out["goal"] = "；".join(f"{k}：{v}" for k, v in pieces if str(v).strip())
        if not str(out.get("region") or "").strip():
            out["region"] = self._fallback_region_for_volume(seq, volume)
        if not str(out.get("new_crisis_hook") or "").strip():
            out["new_crisis_hook"] = out.get("gain_and_hook") or volume.get("gain_and_hook", "")
        return out

    def _matches_volume_blueprint(self, item: dict, seq: int) -> bool:
        """Keep LLM volume planning inside an explicit long-form contract.

        The model may phrase goals creatively, but if a template supplies a
        volume_blueprint the generated volume must still be recognizably the
        same unit in the same order.  Otherwise the caller falls back to the
        deterministic contract blueprint.
        """
        vols = [v for v in ((self.story_contract or {}).get("volume_blueprint") or []) if isinstance(v, dict)]
        if not (1 <= seq <= len(vols)):
            return True
        expected = str(vols[seq - 1].get("title") or "").strip()
        if not expected:
            return True
        got = str(item.get("title") or "").strip()
        if got != expected:
            return False
        expected_core = self._volume_title_core(expected)
        got_core = self._volume_title_core(got)
        if expected_core and got_core and expected_core not in got_core and got_core not in expected_core:
            return False
        return True

    @staticmethod
    def _volume_title_core(title: str) -> str:
        core = str(title or "").strip()
        if "·" in core:
            core = core.split("·", 1)[1]
        core = re.sub(r"^第[一二三四五六七八九十百千万\d]+[卷部篇]\s*", "", core)
        core = re.sub(r"[\s《》「」“”\"'：:，,。.!！?？、\-—_]+", "", core)
        return core

    def _fallback_part_specs(self, n: int) -> list[dict]:
        vols = [v for v in ((self.story_contract or {}).get("volume_blueprint") or []) if isinstance(v, dict)]
        specs: list[dict] = []
        for idx, v in enumerate(vols[:n], 1):
            chain = "；".join(str(x) for x in (v.get("conflict_chain") or []) if str(x).strip())
            goal_parts = [
                ("本卷短期目标", v.get("short_goal", "")),
                ("阻碍势力/困难", v.get("obstacle", "")),
                ("连续递进冲突事件", chain),
                ("关键反转", v.get("key_twist", "")),
                ("卷末收获+下一卷危机", v.get("gain_and_hook", "")),
            ]
            goal = "；".join(f"{k}：{val}" for k, val in goal_parts if str(val).strip())
            specs.append(
                {
                    "title": v.get("title") or f"第{idx}卷",
                    "goal": goal or v.get("short_goal", "完成一个阶段性闭环"),
                    "region": self._fallback_region_for_volume(idx, v),
                    "key_twist": v.get("key_twist", ""),
                    "new_crisis_hook": v.get("gain_and_hook", ""),
                }
            )
        if len(specs) >= n:
            return specs[:n]
        arcs = ["入局", "暗涌", "破局", "终章", "余烬"]
        start = len(specs) + 1
        specs.extend(
            {"title": f"第{i}部·{arcs[(i - 1) % len(arcs)]}", "goal": "推进主线、揭开一层真相",
             "region": self._fallback_region_for_volume(i, {"title": f"第{i}部·{arcs[(i - 1) % len(arcs)]}"}),
             "key_twist": "看似安全的线索反过来证明主角判断有误",
             "new_crisis_hook": "旧危机刚收束，新的追索者已经摸到主角尾迹"}
            for i in range(start, n + 1)
        )
        return specs

    def _llm_arc_spec(self, part: Part, seq: int, personas: list[Persona]) -> dict | None:
        if self.llm is None:
            return None
        roster = "；".join(f"{p.agent_id}={p.name}" for p in personas)
        schema = ('{"title","summary","target_chapters":整数(3到20),'
                  '"focus_agents":[{"agent_id","weight":0到1}]}')
        contract_block = contract_prompt_block(self.story_contract, part_seq=part.sequence_order)
        data = self._complete_json(
            f"{contract_block}你为小说「{part.title}」（目标：{part.goal}）规划其中第 {seq} 个小部分。"
            f"当前体量建议每 arc 约 {self.story_scale.chapter_target_per_arc} 章，允许 3-20 章，戏足放长、过场收短，由内容决定。"
            f"focus_agents 决定本段戏份权重——某些小部分可以主讲配角而非主角。"
            f"agent_id 必须取自角色名册。只输出 JSON：{schema}",
            f"角色名册：{roster}。只输出 JSON。",
            validate=lambda d: isinstance(d, dict) and is_valid_outline(
                self.story_contract, d, part_seq=part.sequence_order),
            retries=1,
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
