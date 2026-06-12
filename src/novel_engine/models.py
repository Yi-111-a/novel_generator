"""数据模型（dataclass）。对应设计文档 §1 的 M1 子集。

字段命名与设计文档保持一致；M1 不需要的字段（embedding/drama_score 等）
在 DB schema 中保留列但此处不建模，避免无谓复杂度。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Entity:
    entity_id: str
    type: str  # character | location | object | faction
    name: str
    attributes: dict[str, Any] = field(default_factory=dict)
    created_tick: int = 0


@dataclass
class Fact:
    """知识的原子单位：一条命题。canonical_content 是唯一、不可变的真相版本。"""

    fact_id: str
    fact_type: str  # event | state | relationship
    canonical_content: str
    structured: dict[str, Any] = field(default_factory=dict)
    story_time: int = 0
    location_id: str | None = None
    involved_entities: list[str] = field(default_factory=list)
    source_event_id: str | None = None


@dataclass
class Event:
    """发生的事，产出 facts + 状态变更。"""

    event_id: str
    story_time: int
    actors: list[str]
    action_type: str
    payload: dict[str, Any] = field(default_factory=dict)
    location_id: str | None = None
    perceivers: list[str] = field(default_factory=list)
    beat_id: str | None = None


@dataclass
class KnowledgeItem:
    """角色账本条目 = (持有者, fact_id, 它持有的版本, 可信度, 何时知道, 来源)。

    version_content 可与 facts.canonical_content 不同 —— 这是"误传/扭曲"（M2 才会用到）。
    """

    agent_id: str
    fact_id: str
    version_content: str
    confidence: float = 1.0
    learned_tick: int = 0
    source_event_id: str | None = None


@dataclass
class Persona:
    agent_id: str
    name: str
    want: str = ""
    values: list[dict[str, Any]] = field(default_factory=list)  # [{name, weight}]
    fatal_flaw: str = ""
    obstacles: list[str] = field(default_factory=list)
    cost_threshold: dict[str, Any] = field(default_factory=dict)  # 愿付代价 + 触发线
    voice: str = ""
    mannerisms: list[str] = field(default_factory=list)
    motif_objects: list[str] = field(default_factory=list)  # 关联意象/道具 id
    arc_state: dict[str, Any] = field(default_factory=dict)
    cost_ledger: list[str] = field(default_factory=list)


@dataclass
class ReaderKnowledge:
    """读者账本（§1.3）。按"话语位置"索引，而非故事时间。

    revealed_version 是读者目前被告知的版本（可能 ≠ canonical，也 ≠ 任何角色版本）。
    """

    fact_id: str
    revealed_version: str
    revealed_discourse_pos: int  # 在第几个场景揭示
    via_pov: str | None = None   # 通过谁的视角揭示


@dataclass
class Scene:
    """叙述产物（§1.6）。discourse_order ≠ story_time。"""

    scene_id: str
    discourse_order: int          # 读者阅读顺序
    source_events: list[str] = field(default_factory=list)
    pov: str | None = None
    target_tension: float = 0.5
    prose_text: str = ""
    newly_revealed: list[str] = field(default_factory=list)  # 本场新揭示给读者的 fact


@dataclass
class Thread:
    """故事线 = 一组角色 + 一个核心冲突（§1.5）。"""

    thread_id: str
    central_question: str
    involved_agents: list[str] = field(default_factory=list)
    priority_weight: float = 0.5  # 主线高、支线低
    current_tension: float = 0.0
    last_advanced_tick: int = 0
    status: str = "open"  # open | converging | resolved


@dataclass
class Beat:
    """节拍 = 朝激活结局的里程碑（§1.5）。"""

    beat_id: str
    sequence_order: int
    type: str  # structural | decision
    goal: str
    threads: list[str] = field(default_factory=list)
    target_tension: float = 0.5
    target_ending_link: str | None = None
    status: str = "planned"  # planned | active | done


@dataclass
class Foreshadow:
    """伏笔台账（§1.5）。指向世界库里一条真实的 fact → 保证"公平"。"""

    foreshadow_id: str
    question: str                 # 抛给读者的未解问题
    linked_fact_id: str           # 它指向世界库里的哪个真相
    planted_discourse_pos: int = 0
    must_resolve: bool = True
    target_payoff_beat: str | None = None
    status: str = "open"          # open | paid_off | abandoned
    payoff_discourse_pos: int | None = None


@dataclass
class Ending:
    """候选结局（§1.1）。导演动态调 active_weight；收尾前过诚实性闸门（§4.7）。"""

    ending_id: str
    summary: str
    theme_expression: str = ""
    required_conditions: list[str] = field(default_factory=list)
    active_weight: float = 0.5
    status: str = "candidate"     # candidate | final


@dataclass
class Part:
    """规划层·部分（全书 3-5 个）。锁定时一次性划分；region 约束本部分章节地点。"""

    part_id: str
    sequence_order: int
    title: str = ""
    goal: str = ""
    region: str = ""
    reveal_node_ids: list[str] = field(default_factory=list)  # 本部分计划上线的揭示链节点
    status: str = "planned"  # planned | active | done


@dataclass
class Arc:
    """规划层·小部分（每 5-10 章）。滚动生成。focus_agents 决定本段戏份权重。

    focus_agents = [{"agent_id": id, "weight": float}]；某段可主讲配角（权重盖过主角）。
    """

    arc_id: str
    part_id: str
    sequence_order: int
    title: str = ""
    summary: str = ""
    target_chapters: int = 5
    focus_agents: list[dict[str, Any]] = field(default_factory=list)
    status: str = "planned"  # planned | active | done


@dataclass
class ChapterPlan:
    """规划层·逐章计划。cast/location/items/reveal_gate 为本章硬约束（防凭空出现/越权揭示）。"""

    chapter_id: str
    arc_id: str
    sequence_order: int  # 全书章号
    title: str = ""  # 写满后由 LLM 命名
    cast: list[str] = field(default_factory=list)
    location_ids: list[str] = field(default_factory=list)
    available_items: list[str] = field(default_factory=list)
    items_present: list[str] = field(default_factory=list)     # §13.1 在场道具台账 = 叙述白名单
    items_introduced: list[str] = field(default_factory=list)  # §13.1 本章新登场道具（须有来源）
    items_consumed: list[str] = field(default_factory=list)    # §13.1 本章被销毁/失去的道具
    beat_goals: list[str] = field(default_factory=list)
    beat_povs: list[str] = field(default_factory=list)   # 每个 beat 的视角人物 id（POV 跟着节拍走）
    reveal_gate: list[str] = field(default_factory=list)
    knowledge_delta: dict[str, Any] = field(default_factory=dict)  # {agent_id:[fid], "reader":[fid]}
    summary: str = ""
    scene_ids: list[str] = field(default_factory=list)
    target_scenes: int = 3
    role: str = "rising"  # 起承转合功能：setup | rising | twist | climax | resolution
    target_tension: float = 0.5
    dramatic_question: str = ""       # §2 本章必答的戏剧问题
    resolution_predicate: str = ""    # §2 收束判定 DSL，命中即收
    min_scenes: int = 2               # §2 收束下界
    target_words: int = 2400          # §13 本章目标字数（治"内容太少"）
    ending_hook: str = ""             # §13.2 章末钩子：指向后文的悬念（act-out）
    hook_type: str = ""               # cliffhanger | dramatic_irony | new_question | reversal_tease
    pov_agent: str = ""               # 问题6：本章主讲视角（按 focus_agents 轮换，让配角有主讲场）
    exit_state: str = ""              # 问题4：本章必须达成的世界状态变化（推进闸门）
    audited: int = 0                  # 审计闸门：0 未审 / 1 已通过（衔接·转场·道具人物合规）
    conflict_type: str = ""           # S1 本章冲突类型（轮换，治"七章同一支舞"）
    status: str = "planned"  # planned | active | done


@dataclass
class Location:
    """§12.3 地点一等实体：从世界圣经地理落出的具体空间。loc_id == location 实体 id。"""

    loc_id: str
    part_id: str | None = None
    name: str = ""
    geo_full: str = ""
    connects_to: list[str] = field(default_factory=list)
    controlling_faction: str = ""
    notable_items: list[str] = field(default_factory=list)
    # W2 地理层新增
    level: str = ""          # 层级标签：大区域 / 街道 / 建筑 / 其他
    parent: str = ""         # 上级地点 loc_id（层级关系）
    culture_local: str = ""  # 本地风土人情/典型活动/常驻人群
    summary: str = ""        # 1–2句·常驻注入
    detail: str = ""         # 综合描述·按需检索


@dataclass
class GraphEdge:
    """W5 知识图谱边：源/关系/目标 + 注意力权重 + 时变激活信息。"""

    src: str
    rel: str   # member_of|controls|allied|hostile|infiltrates|neutral|tributary|related_to|located_in|knows
    dst: str
    meta: dict[str, Any] = field(default_factory=dict)  # {cause_chapter, note, valence?}
    since_chapter: int = 0
    until_chapter: int | None = None
    intensity: float = 0.5
    last_active_chapter: int = 0


@dataclass
class Faction:
    """W3 势力一等实体：信条/目标/手段/地盘/架构/核心成员/历史/势力间关系/秘密 + 两级。"""

    faction_id: str
    name: str = ""
    ideology: str = ""
    goals: str = ""
    methods: str = ""
    territory: list[str] = field(default_factory=list)        # canon loc_id 列表
    structure: str = ""
    key_members: list[dict[str, Any]] = field(default_factory=list)  # [{name, role, agent_id?, note}]
    history: str = ""
    relations: list[dict[str, Any]] = field(default_factory=list)    # [{target_faction_id, kind, intensity, note}]
    secret: str = ""
    summary: str = ""
    detail: str = ""
    source: str = "w3"
    created_at: int = 0


@dataclass
class InventoryItem:
    """物品归属：当前持有者 + 状态。可转移/丢失/销毁。holder_agent_id 为 None 表示无主/已消失/已销毁。"""

    object_id: str
    holder_agent_id: str | None = None
    status: str = "held"  # held | transferred | lost | consumed | destroyed | sacrificed
    acquired_chapter: int = 0
    note: str = ""


@dataclass
class CharacterChapterLog:
    """Chapter-level character continuity memory."""

    agent_id: str
    chapter_seq: int
    actions: str = ""
    psychology: str = ""
    intention: str = ""
    items_changed: list[str] = field(default_factory=list)


@dataclass
class BatchAudit:
    """Cross-chapter audit snapshot and compressed memory."""

    chapter_seq: int
    result_json: dict[str, Any] = field(default_factory=dict)
    summary_json: dict[str, Any] = field(default_factory=dict)
    created_tick: int = 0


@dataclass
class EmotionalState:
    """§4.2 情绪余温：跨场传递的轻量状态。intensity 随 decay 衰减，自然回落。"""

    agent_id: str
    emotion: str = ""
    intensity: float = 0.0
    cause: str = ""
    decay: float = 0.25
    updated_tick: int = 0


@dataclass
class CharacterCard:
    """§1 选角层身份卡：功能位唯一、按戏份分级。与 persona 互补（persona=驱动核心）。"""

    card_id: str
    agent_id: str | None = None
    tier: str = "extra"  # lead | supporting | extra
    slot_key: str | None = None
    name: str = ""
    one_liner: str = ""
    voice_register: str = ""
    defining_trait: str = ""
    core_desire: str = ""
    verbal_habits: str = ""
    key_relation: str = ""
    backstory: str = ""
    fatal_flaw: str = ""
    motif_objects: list[str] = field(default_factory=list)
    relationship_map: dict[str, Any] = field(default_factory=dict)
    arc: str = ""
    # W4 分层人物卡：三维度（生理/社会/心理）
    appearance: str = ""    # 生理：外貌/身材/年龄/标志物
    social_role: str = ""   # 社会：出身/家庭/阶层/隶属势力/社会关系网
    psychology: str = ""    # 心理：性格/三观/恐惧/欲望细化
    created_at: int = 0


@dataclass
class ToneProfile:
    """§16 文风契约（闸门⓪）：贯穿规划/表演/渲染三层的母约束，确认后全程只读。"""

    genre: str = ""
    primary_effect: str = ""          # 每场必交付的主效果
    register: str = ""                # 语域/腔调
    sentence_rhythm: str = ""         # 句法节奏
    diction_do: list[str] = field(default_factory=list)
    diction_dont: list[str] = field(default_factory=list)
    device_kit: list[str] = field(default_factory=list)
    pacing: str = ""
    tension_curve_bias: str = ""      # 张弛曲线偏置（参数化 _role_curve）
    reveal_cadence: str = ""          # 揭示节奏（参数化揭示链兑现快慢）
    complexity: str = ""              # 复杂度（参数化主副线数量）
    tone_reference: str = ""          # 定调样例正文（渲染锚）
    confirmed: bool = False
    # B0.6 时代隔离墙（主题8）：{enabled, moral_index, religiosity, science_level,
    #   banned_modern_words:[…], forced_attribution}。enabled=False（默认）则不约束（现代题材）。
    era_logic: dict = field(default_factory=dict)

    def is_set(self) -> bool:
        return bool(self.genre or self.primary_effect or self.register)


@dataclass
class StyleProfile:
    """B0 文风模拟（Style Skill）：用户上传作品蒸馏 / 上传现成文风，得到的文风指纹。
    存在且 enabled 时叠加并优先于 tone_profile（覆盖 register/rhythm/diction/devices + 提供样例）。
    安全注入：只学腔调，样例里的具体人名/地名/物件/情节严禁照搬（吸取 style_anchor 泄漏教训）。"""

    name: str = ""                    # 风格名（如「龙族·江南腔」）
    source: str = ""                  # 来源作品/作者
    register: str = ""                # 语域/腔调
    rhythm: str = ""                  # 句法节奏
    devices: list[str] = field(default_factory=list)      # 标志性手法
    diction_do: list[str] = field(default_factory=list)   # 偏好词
    diction_dont: list[str] = field(default_factory=list) # 禁忌词
    motifs: list[str] = field(default_factory=list)       # 母题意象
    samples: list[str] = field(default_factory=list)      # 2-4 段短样例（仅供模仿腔调）
    metrics: dict = field(default_factory=dict)           # 6 维量化指纹（compute_style_metrics）
    enabled: bool = True

    def is_set(self) -> bool:
        return bool(self.enabled and (self.register or self.rhythm or self.samples))


@dataclass
class RevealNode:
    """揭示链节点：clue→clue→truth。主角发现前置后，后置才解锁可揭示（探索驱动）。"""

    node_id: str
    fact_id: str | None = None
    kind: str = "clue"  # clue | truth
    sequence_order: int = 0
    prereq_node_ids: list[str] = field(default_factory=list)
    part_id: str | None = None
    description: str = ""
    discovered: bool = False
    discovered_chapter: int | None = None


@dataclass
class Dilemma:
    """内在冲突生成器的产物（§3 / §6.2）。"""

    target_agent: str
    situation: str
    colliding_pair: tuple[str, str]  # 相撞的两个元素（如 "复仇之欲" vs "不杀无辜"）
    stakes: str
    why_no_escape: str
    score: float = 0.0
    thread_id: str | None = None


@dataclass
class Action:
    """角色决策 Agent 的结构化输出（§6.1）。

    referenced_facts / referenced_entities 供校验层做"越权知情/实体存在"检查；
    若模型未显式给出，agent 层会从 target/dialogue 等推断或留空。
    """

    intent: str
    target: str | None = None
    dialogue: str = ""
    inner_thought: str = ""
    chosen_value: str = ""
    referenced_facts: list[str] = field(default_factory=list)
    referenced_entities: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Action":
        return cls(
            intent=str(data.get("intent", "")).strip(),
            target=(data.get("target") or None),
            dialogue=str(data.get("dialogue", "")),
            inner_thought=str(data.get("inner_thought", "")),
            chosen_value=str(data.get("chosen_value", "")),
            referenced_facts=list(data.get("referenced_facts", []) or []),
            referenced_entities=list(data.get("referenced_entities", []) or []),
        )
