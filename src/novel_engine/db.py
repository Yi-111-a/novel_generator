"""SQLite 连接与 schema。

设计原则（§0）：世界状态库是唯一真相源，且历史不可变。facts/events 只追加。
schema 保留了设计文档 §1 的字段（含 M1 暂不使用的列，留空即可），便于后续里程碑扩展。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS world_bible (
    id              INTEGER PRIMARY KEY CHECK (id = 1),  -- 单行
    setting_core    TEXT NOT NULL DEFAULT '',
    geography       TEXT NOT NULL DEFAULT '{}',
    culture         TEXT NOT NULL DEFAULT '{}',
    physics_rules   TEXT NOT NULL DEFAULT '[]',          -- json 数组：不可违反的硬约束
    protagonist_want TEXT NOT NULL DEFAULT '',
    theme           TEXT NOT NULL DEFAULT '',
    exposition_release_rules TEXT NOT NULL DEFAULT '[]'  -- §4.3 背景何时允许渗入
);

CREATE TABLE IF NOT EXISTS entities (
    entity_id    TEXT PRIMARY KEY,
    type         TEXT NOT NULL,
    name         TEXT NOT NULL,
    attributes   TEXT NOT NULL DEFAULT '{}',
    created_tick INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS facts (
    fact_id           TEXT PRIMARY KEY,
    fact_type         TEXT NOT NULL,
    canonical_content TEXT NOT NULL,
    structured        TEXT NOT NULL DEFAULT '{}',
    story_time        INTEGER NOT NULL DEFAULT 0,
    location_id       TEXT,
    involved_entities TEXT NOT NULL DEFAULT '[]',
    source_event_id   TEXT,
    embedding         BLOB                                -- M1 不用，预留
);

CREATE TABLE IF NOT EXISTS events (
    event_id    TEXT PRIMARY KEY,
    story_time  INTEGER NOT NULL DEFAULT 0,
    actors      TEXT NOT NULL DEFAULT '[]',
    action_type TEXT NOT NULL,
    payload     TEXT NOT NULL DEFAULT '{}',
    location_id TEXT,
    perceivers  TEXT NOT NULL DEFAULT '[]',
    drama_score REAL,                                     -- M1 不用，预留
    beat_id     TEXT
);

CREATE TABLE IF NOT EXISTS agent_knowledge (
    agent_id        TEXT NOT NULL,
    fact_id         TEXT NOT NULL,
    version_content TEXT NOT NULL,
    confidence      REAL NOT NULL DEFAULT 1.0,
    learned_tick    INTEGER NOT NULL DEFAULT 0,
    source_event_id TEXT,
    PRIMARY KEY (agent_id, fact_id)
);

CREATE TABLE IF NOT EXISTS persona (
    agent_id       TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    want           TEXT NOT NULL DEFAULT '',
    "values"       TEXT NOT NULL DEFAULT '[]',
    fatal_flaw     TEXT NOT NULL DEFAULT '',
    obstacles      TEXT NOT NULL DEFAULT '[]',
    cost_threshold TEXT NOT NULL DEFAULT '{}',
    voice          TEXT NOT NULL DEFAULT '',
    mannerisms     TEXT NOT NULL DEFAULT '[]',
    motif_objects  TEXT NOT NULL DEFAULT '[]',
    arc_state      TEXT NOT NULL DEFAULT '{}',
    cost_ledger    TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS threads (
    thread_id         TEXT PRIMARY KEY,
    central_question  TEXT NOT NULL DEFAULT '',
    involved_agents   TEXT NOT NULL DEFAULT '[]',
    priority_weight   REAL NOT NULL DEFAULT 0.5,
    current_tension   REAL NOT NULL DEFAULT 0.0,
    last_advanced_tick INTEGER NOT NULL DEFAULT 0,
    status            TEXT NOT NULL DEFAULT 'open'
);

CREATE TABLE IF NOT EXISTS beats (
    beat_id            TEXT PRIMARY KEY,
    sequence_order     INTEGER NOT NULL DEFAULT 0,
    type               TEXT NOT NULL DEFAULT 'decision',
    goal               TEXT NOT NULL DEFAULT '',
    threads            TEXT NOT NULL DEFAULT '[]',
    target_tension     REAL NOT NULL DEFAULT 0.5,
    target_ending_link TEXT,
    status             TEXT NOT NULL DEFAULT 'planned'
);

CREATE TABLE IF NOT EXISTS reader_knowledge (
    fact_id                TEXT PRIMARY KEY,
    revealed_version       TEXT NOT NULL,
    revealed_discourse_pos INTEGER NOT NULL DEFAULT 0,
    via_pov                TEXT
);

CREATE TABLE IF NOT EXISTS scenes (
    scene_id        TEXT PRIMARY KEY,
    discourse_order INTEGER NOT NULL,
    source_events   TEXT NOT NULL DEFAULT '[]',
    pov             TEXT,
    target_tension  REAL NOT NULL DEFAULT 0.5,
    prose_text      TEXT NOT NULL DEFAULT '',
    newly_revealed  TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS foreshadows (
    foreshadow_id         TEXT PRIMARY KEY,
    question              TEXT NOT NULL,
    linked_fact_id        TEXT NOT NULL,
    planted_discourse_pos INTEGER NOT NULL DEFAULT 0,
    must_resolve          INTEGER NOT NULL DEFAULT 1,
    target_payoff_beat    TEXT,
    status                TEXT NOT NULL DEFAULT 'open',
    payoff_discourse_pos  INTEGER
);

CREATE TABLE IF NOT EXISTS endings (
    ending_id          TEXT PRIMARY KEY,
    summary            TEXT NOT NULL DEFAULT '',
    theme_expression   TEXT NOT NULL DEFAULT '',
    required_conditions TEXT NOT NULL DEFAULT '[]',
    active_weight      REAL NOT NULL DEFAULT 0.5,
    status             TEXT NOT NULL DEFAULT 'candidate'
);

-- ===== 规划层（大纲驱动，仅新建项目使用；旧项目这些表留空不影响） =====
-- 三层大纲：part（部分，3-5 个）→ arc（小部分，每 5-10 章）→ chapter_plan（逐章计划）。
-- 滚动生成：写完一个 arc 才生成下一个 arc 的逐章计划，便于吸收已发生的涌现。

CREATE TABLE IF NOT EXISTS parts (
    part_id        TEXT PRIMARY KEY,
    sequence_order INTEGER NOT NULL DEFAULT 0,
    title          TEXT NOT NULL DEFAULT '',
    goal           TEXT NOT NULL DEFAULT '',        -- 本部分要达成/改变什么
    region         TEXT NOT NULL DEFAULT '',        -- 地域范围（本部分章节地点须属于此）
    reveal_node_ids TEXT NOT NULL DEFAULT '[]',     -- 本部分计划上线的揭示链节点
    status         TEXT NOT NULL DEFAULT 'planned'  -- planned | active | done
);

CREATE TABLE IF NOT EXISTS arcs (
    arc_id          TEXT PRIMARY KEY,
    part_id         TEXT NOT NULL,
    sequence_order  INTEGER NOT NULL DEFAULT 0,
    title           TEXT NOT NULL DEFAULT '',
    summary         TEXT NOT NULL DEFAULT '',
    target_chapters INTEGER NOT NULL DEFAULT 5,     -- 本小部分目标章数（5-10）
    focus_agents    TEXT NOT NULL DEFAULT '[]',     -- [{agent_id, weight}] 本段戏份权重（可主讲配角）
    status          TEXT NOT NULL DEFAULT 'planned' -- planned | active | done
);

CREATE TABLE IF NOT EXISTS chapter_plans (
    chapter_id      TEXT PRIMARY KEY,
    arc_id          TEXT NOT NULL,
    sequence_order  INTEGER NOT NULL DEFAULT 0,     -- 全书章号（1-based）
    title           TEXT NOT NULL DEFAULT '',       -- 写满后由 LLM 命名
    cast            TEXT NOT NULL DEFAULT '[]',     -- 本章出场人物 agent_id（防凭空出现）
    location_ids    TEXT NOT NULL DEFAULT '[]',     -- 本章地点（须属于所在 part.region）
    available_items TEXT NOT NULL DEFAULT '[]',     -- 本章可动用物品 object_id（来自 cast 的 inventory）
    items_present   TEXT NOT NULL DEFAULT '[]',     -- §13.1 进入本章时在场的道具台账（继承前章未消耗 + cast 携带）= 叙述白名单
    items_introduced TEXT NOT NULL DEFAULT '[]',    -- §13.1 本章新登场道具（须有来源，防凭空）
    items_consumed  TEXT NOT NULL DEFAULT '[]',     -- §13.1 本章被销毁/失去的道具
    beat_goals      TEXT NOT NULL DEFAULT '[]',     -- 本章要推进的节拍目标 [text]
    reveal_gate     TEXT NOT NULL DEFAULT '[]',     -- 本章允许揭示的 fact_id（探索驱动揭示链的闸门）
    knowledge_delta TEXT NOT NULL DEFAULT '{}',     -- {agent_id:[fact_id], "reader":[fact_id]} 章末新知
    summary         TEXT NOT NULL DEFAULT '',       -- 写满后回填的本章摘要（供下一章细纲参考）
    scene_ids       TEXT NOT NULL DEFAULT '[]',     -- 本章已渲染的场
    target_scenes   INTEGER NOT NULL DEFAULT 3,     -- 目标场数（2-4）
    role            TEXT NOT NULL DEFAULT 'rising',  -- 起承转合功能：setup|rising|twist|climax|resolution
    target_tension  REAL NOT NULL DEFAULT 0.5,       -- 由 role 推导的目标张力（张弛曲线）
    dramatic_question    TEXT NOT NULL DEFAULT '',   -- §2 本章必答的戏剧问题（文本，叙述/展示用）
    resolution_predicate TEXT NOT NULL DEFAULT '',   -- §2 收束判定 DSL，命中即"问题被回答"
    min_scenes      INTEGER NOT NULL DEFAULT 2,      -- §2 收束下界（防太短）；target_scenes 作上界
    target_words    INTEGER NOT NULL DEFAULT 2400,   -- §13 本章目标字数（治"内容太少"；建议 1800-3500）
    ending_hook     TEXT NOT NULL DEFAULT '',        -- §13.2 章末钩子（act-out）：指向后文的悬念
    hook_type       TEXT NOT NULL DEFAULT '',        -- cliffhanger|dramatic_irony|new_question|reversal_tease
    pov_agent       TEXT NOT NULL DEFAULT '',         -- 问题6：本章主讲视角（按 focus_agents 轮换）
    exit_state      TEXT NOT NULL DEFAULT '',         -- 问题4：本章必须达成的世界状态变化
    audited         INTEGER NOT NULL DEFAULT 0,       -- 审计闸门：0 未审 / 1 已通过（衔接·转场·道具人物合规）
    conflict_type   TEXT NOT NULL DEFAULT '',         -- S1 本章冲突类型（轮换，治"七章同一支舞"）
    status          TEXT NOT NULL DEFAULT 'planned' -- planned | active | done
);

-- 物品归属：当前持有者 + 状态。物品可转移/丢失（held→转他人 / lost 彻底消失）。
-- 角色取物校验：动作引用的 object 必须在该角色名下 held，或本章明确获得（获得即写入此表）。
CREATE TABLE IF NOT EXISTS inventory (
    object_id        TEXT PRIMARY KEY,              -- 对应 entities 中 type=object 的实体
    holder_agent_id  TEXT,                          -- 当前持有者；NULL = 无主/已消失
    status           TEXT NOT NULL DEFAULT 'held',  -- held | transferred | lost
    acquired_chapter INTEGER NOT NULL DEFAULT 0,    -- 当前持有者获得它的章号
    note             TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS character_chapter_logs (
    agent_id      TEXT NOT NULL,
    chapter_seq   INTEGER NOT NULL DEFAULT 0,
    actions       TEXT NOT NULL DEFAULT '',
    psychology    TEXT NOT NULL DEFAULT '',
    intention     TEXT NOT NULL DEFAULT '',
    items_changed TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY (agent_id, chapter_seq)
);
CREATE INDEX IF NOT EXISTS idx_character_logs_agent_chapter
    ON character_chapter_logs (agent_id, chapter_seq);

CREATE TABLE IF NOT EXISTS batch_audits (
    chapter_seq  INTEGER PRIMARY KEY,
    result_json  TEXT NOT NULL DEFAULT '{}',
    summary_json TEXT NOT NULL DEFAULT '{}',
    created_tick INTEGER NOT NULL DEFAULT 0
);

-- 揭示链：把世界真相拆成 clue→clue→truth 的有向图。主角在剧情中"撞到"前置节点后，
-- 后置节点才解锁可揭示（探索驱动）。reveal_gate 只能放 discovered=1 或本章解锁的节点对应 fact。
CREATE TABLE IF NOT EXISTS reveal_chain (
    node_id            TEXT PRIMARY KEY,
    fact_id            TEXT,                          -- 指向世界库的真相 fact（可空：纯结构线索）
    kind               TEXT NOT NULL DEFAULT 'clue',  -- clue | truth
    sequence_order     INTEGER NOT NULL DEFAULT 0,
    prereq_node_ids    TEXT NOT NULL DEFAULT '[]',    -- 必须先发现的前置节点
    part_id            TEXT,                          -- 计划在哪个部分上线（可空）
    description        TEXT NOT NULL DEFAULT '',      -- 这一步发现了什么
    discovered         INTEGER NOT NULL DEFAULT 0,    -- 主角是否已发现
    discovered_chapter INTEGER                        -- 在第几章发现
);

-- §1 选角层：出生即建卡。功能位(slot_key)唯一 → 同一职能永远同一张卡，绝不重抽（治"乱写名字"）。
-- 按戏份分级 tier：extra 只填前段，supporting 补中段，lead 全填。与 persona 互补（persona=驱动核心）。
CREATE TABLE IF NOT EXISTS character_cards (
    card_id          TEXT PRIMARY KEY,
    agent_id         TEXT,                          -- 关联 persona.agent_id / entities.entity_id
    tier             TEXT NOT NULL DEFAULT 'extra', -- lead | supporting | extra
    slot_key         TEXT UNIQUE,                   -- 功能位指纹，如 'tavern_old_waiter'
    name             TEXT NOT NULL DEFAULT '',
    one_liner        TEXT NOT NULL DEFAULT '',      -- 场上职能一句话
    voice_register   TEXT NOT NULL DEFAULT '',      -- 语域/腔调（所有 tier 必填）
    defining_trait   TEXT NOT NULL DEFAULT '',      -- 一个定义性特征（所有 tier 必填）
    core_desire      TEXT NOT NULL DEFAULT '',      -- supporting 起
    verbal_habits    TEXT NOT NULL DEFAULT '',      -- 1-2 个语言习惯
    key_relation     TEXT NOT NULL DEFAULT '',
    backstory        TEXT NOT NULL DEFAULT '',      -- lead 起
    fatal_flaw       TEXT NOT NULL DEFAULT '',
    motif_objects    TEXT NOT NULL DEFAULT '[]',    -- JSON，落入 inventory
    relationship_map TEXT NOT NULL DEFAULT '{}',    -- JSON
    arc              TEXT NOT NULL DEFAULT '',
    -- W4 分层人物卡：三维度（生理/社会/心理），主角极详、主配精简
    appearance       TEXT NOT NULL DEFAULT '',      -- 生理：外貌/身材/年龄/标志物
    social_role      TEXT NOT NULL DEFAULT '',      -- 社会：出身/家庭/阶层/隶属势力/社会关系网
    psychology       TEXT NOT NULL DEFAULT '',      -- 心理：性格/三观/恐惧/欲望细化
    created_at       INTEGER NOT NULL DEFAULT 0
);

-- §4.3 叙述嗓音连续性：project 级单行锚点，跨批渲染对齐语气、累积意象词、统一禁用词。
CREATE TABLE IF NOT EXISTS style_anchor (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    tone_sample   TEXT NOT NULL DEFAULT '',   -- 几句定调参考正文（首场成稿即定）
    motif_lexicon TEXT NOT NULL DEFAULT '[]', -- 已用核心意象词（JSON，去重，保持呼应）
    banned_words  TEXT NOT NULL DEFAULT '[]'  -- 统一禁用词（JSON，复用 _title_blacklist）
);

-- §12.3 地点一等实体：从世界圣经地理落出的具体地点（方位/气候/危险/连通/势力/固有道具）。
-- loc_id == 对应 location 类型 entity 的 entity_id（与既有事件/章地点约束兼容）。
CREATE TABLE IF NOT EXISTS locations (
    loc_id              TEXT PRIMARY KEY,
    part_id             TEXT,
    name                TEXT NOT NULL DEFAULT '',
    geo_full            TEXT NOT NULL DEFAULT '',   -- 完整描写：方位/气候/建筑/声光气味/危险/通路
    connects_to         TEXT NOT NULL DEFAULT '[]', -- JSON，相连地点 loc_id（地理拓扑，防瞬移）
    controlling_faction TEXT NOT NULL DEFAULT '',
    notable_items       TEXT NOT NULL DEFAULT '[]', -- JSON，该地固有道具 object_id（落入 inventory）
    -- W2 地理层新增：多级地图 + 两级(summary/detail) + 风土人情
    level               TEXT NOT NULL DEFAULT '',   -- 层级：大区域/街道/建筑/其他
    parent              TEXT NOT NULL DEFAULT '',   -- 上级地点 loc_id（层级关系）
    culture_local       TEXT NOT NULL DEFAULT '',   -- 本地风土人情/典型活动/常驻人群
    summary             TEXT NOT NULL DEFAULT '',   -- 1–2句·常驻注入（W2 填）
    detail              TEXT NOT NULL DEFAULT ''    -- 综合描述·按需检索（W2 填）
);

-- W3 势力系统：game-like 一等实体（独立、富设定、据地理因地制宜、势力间关系）。
-- 关系暂存 relations(JSON：[{target_faction_id, kind, intensity, note}])；W5 会迁到 graph_edges。
-- 核心成员存 key_members(JSON：[{name, role, agent_id?, note}])；落 character_cards 时回填 agent_id。
CREATE TABLE IF NOT EXISTS factions (
    faction_id          TEXT PRIMARY KEY,
    name                TEXT NOT NULL DEFAULT '',
    ideology            TEXT NOT NULL DEFAULT '',   -- 信条/价值
    goals               TEXT NOT NULL DEFAULT '',   -- 短期/长期目标
    methods             TEXT NOT NULL DEFAULT '',   -- 手段：暗杀/渗透/公关…
    territory           TEXT NOT NULL DEFAULT '[]', -- JSON：控制的 canon loc_id 列表
    structure           TEXT NOT NULL DEFAULT '',   -- 组织架构
    key_members         TEXT NOT NULL DEFAULT '[]', -- JSON：[{name, role, agent_id?, note}]
    history             TEXT NOT NULL DEFAULT '',   -- 该势力的历史故事
    relations           TEXT NOT NULL DEFAULT '[]', -- JSON：[{target_faction_id, kind, intensity, note}]
                                                    -- kind: allied|hostile|infiltrates|neutral|tributary
    secret              TEXT NOT NULL DEFAULT '',   -- 不可告人的（reveal_chain 可挂）
    summary             TEXT NOT NULL DEFAULT '',   -- 1–2句·常驻注入
    detail              TEXT NOT NULL DEFAULT '',   -- 综合描述·按需检索
    source              TEXT NOT NULL DEFAULT 'w3', -- w3 | user | w3_deepened
    created_at          INTEGER NOT NULL DEFAULT 0
);

-- W5 知识图谱：人物/势力/地点/事件构成的图，静态边锁定时建，动态边 FactExtractor 增量加。
-- 注意力：每条边带 intensity(0-1) 与 last_active_chapter，剧情活跃时升、久未活跃自然衰减。
-- 检索（W6 用）：按 intensity×recency 排序拉子图，避免全塞。
CREATE TABLE IF NOT EXISTS graph_edges (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    src                  TEXT NOT NULL,    -- entity_id / faction_id
    rel                  TEXT NOT NULL,    -- member_of|controls|allied|hostile|infiltrates|neutral|tributary
                                            -- |related_to|located_in|knows
    dst                  TEXT NOT NULL,
    meta                 TEXT NOT NULL DEFAULT '{}',  -- JSON: {cause_chapter, note, valence?}
    since_chapter        INTEGER NOT NULL DEFAULT 0,
    until_chapter        INTEGER,           -- NULL=still active
    intensity            REAL NOT NULL DEFAULT 0.5,   -- 0–1，注意力权重
    last_active_chapter  INTEGER NOT NULL DEFAULT 0,
    UNIQUE(src, rel, dst) ON CONFLICT REPLACE
);
CREATE INDEX IF NOT EXISTS idx_graph_src ON graph_edges(src);
CREATE INDEX IF NOT EXISTS idx_graph_dst ON graph_edges(dst);
CREATE INDEX IF NOT EXISTS idx_graph_rel ON graph_edges(rel);

-- LLM 对话日志：完整记录每次 LLM 调用的 system/user/response
CREATE TABLE IF NOT EXISTS llm_logs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           REAL NOT NULL DEFAULT 0,    -- unix timestamp
    caller       TEXT NOT NULL DEFAULT '',    -- 调用方标识（scene_writer/planner/fact_extractor 等）
    system_msg   TEXT NOT NULL DEFAULT '',
    user_msg     TEXT NOT NULL DEFAULT '',
    response     TEXT NOT NULL DEFAULT '',
    temperature  REAL,
    elapsed_ms   INTEGER NOT NULL DEFAULT 0,  -- 耗时毫秒
    meta         TEXT NOT NULL DEFAULT '{}'   -- JSON: {chapter_id, scene_pos, ...}
);

-- §12 世界圣经全量存档：逐字保存用户/LLM 的世界观分节原文，不摘要、不覆写。
-- 与单行 world_bible（结构化索引）互补：这里是"全文"，供规划层按需检索喂入。
CREATE TABLE IF NOT EXISTS world_bible_sections (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    section     TEXT NOT NULL DEFAULT '',   -- geography|factions|history|rules|lexicon|cosmology…
    title       TEXT NOT NULL DEFAULT '',
    body_full   TEXT NOT NULL DEFAULT '',   -- 原文全量，不压缩
    summary     TEXT NOT NULL DEFAULT '',   -- W1 两级：1–2 句·常驻注入（仅 source='w1' 行填）
    source      TEXT NOT NULL DEFAULT 'user', -- user | llm_expanded | w1 | w1_deepened
    created_at  INTEGER NOT NULL DEFAULT 0
);

-- §16 文风契约（Tone Contract）：闸门⓪，先于世界圣经与总纲。project 级单行，
-- 锁定时由用户确认，全程只读、不可中途漂移。同时约束规划/表演/渲染三层。
CREATE TABLE IF NOT EXISTS tone_profile (
    id                 INTEGER PRIMARY KEY CHECK (id = 1),  -- 单行
    genre              TEXT NOT NULL DEFAULT '',   -- comedy|horror|xuanhuan_powerfantasy|tragedy|mystery|literary…
    primary_effect     TEXT NOT NULL DEFAULT '',   -- 每场必交付的主效果：laugh|dread|catharsis_satisfaction|grief|curiosity
    register           TEXT NOT NULL DEFAULT '',   -- 语域：诙谐口语|阴冷凝重|直白爽利|典雅书面
    sentence_rhythm    TEXT NOT NULL DEFAULT '',   -- 句法节奏：短句高频|长短交错|绵长铺陈
    diction_do         TEXT NOT NULL DEFAULT '[]', -- JSON：鼓励的词类/修辞
    diction_dont       TEXT NOT NULL DEFAULT '[]', -- JSON：禁忌（出现即判不合格）
    device_kit         TEXT NOT NULL DEFAULT '[]', -- JSON：类型手法包
    pacing             TEXT NOT NULL DEFAULT '',   -- 爽文=快/payoff密；恐怖=慢热递进；文学=舒缓
    tension_curve_bias TEXT NOT NULL DEFAULT '',   -- 张弛曲线偏置：递进到爆发|锯齿|波浪
    reveal_cadence     TEXT NOT NULL DEFAULT '',   -- 揭示节奏：克制|快速|均匀延迟
    complexity         TEXT NOT NULL DEFAULT '',   -- 复杂度：低|中|高
    tone_reference     TEXT NOT NULL DEFAULT '',   -- 1-2 段定调样例正文（渲染锚）
    confirmed          INTEGER NOT NULL DEFAULT 0, -- 用户是否已确认基调（确认后只读）
    era_logic          TEXT NOT NULL DEFAULT '{}'  -- B0.6 时代隔离墙(JSON)：三旋钮+现代禁用词+强制归因
);

-- B0 文风模拟（Style Skill）：project 级单行。用户上传作品蒸馏 / 上传现成文风得到的文风指纹。
-- 存在且 enabled 时叠加并优先于 tone_profile。安全注入：只学腔调，样例内容严禁照搬。
CREATE TABLE IF NOT EXISTS style_skill (
    id           INTEGER PRIMARY KEY CHECK (id = 1),  -- 单行
    name         TEXT NOT NULL DEFAULT '',
    source       TEXT NOT NULL DEFAULT '',
    register     TEXT NOT NULL DEFAULT '',
    rhythm       TEXT NOT NULL DEFAULT '',
    devices      TEXT NOT NULL DEFAULT '[]',  -- JSON
    diction_do   TEXT NOT NULL DEFAULT '[]',  -- JSON
    diction_dont TEXT NOT NULL DEFAULT '[]',  -- JSON
    motifs       TEXT NOT NULL DEFAULT '[]',  -- JSON
    samples      TEXT NOT NULL DEFAULT '[]',  -- JSON：2-4 段短样例
    metrics      TEXT NOT NULL DEFAULT '{}',  -- JSON：6 维量化指纹
    enabled      INTEGER NOT NULL DEFAULT 1
);

-- §4.2 情绪余温：每角色一条轻量状态，跨场传递；按 decay 随拍衰减，禁无过渡情绪跳切。
CREATE TABLE IF NOT EXISTS emotional_state (
    agent_id     TEXT PRIMARY KEY,
    emotion      TEXT NOT NULL DEFAULT '',
    intensity    REAL NOT NULL DEFAULT 0,     -- 0..1
    cause        TEXT NOT NULL DEFAULT '',
    decay        REAL NOT NULL DEFAULT 0.25,  -- 每拍衰减
    updated_tick INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_knowledge_agent ON agent_knowledge (agent_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_fact ON agent_knowledge (fact_id);
CREATE INDEX IF NOT EXISTS idx_arcs_part ON arcs (part_id);
CREATE INDEX IF NOT EXISTS idx_chapter_arc ON chapter_plans (arc_id);
CREATE INDEX IF NOT EXISTS idx_inventory_holder ON inventory (holder_agent_id);
CREATE INDEX IF NOT EXISTS idx_cards_agent ON character_cards (agent_id);
"""


def connect(db_path: str | Path = ":memory:", check_same_thread: bool = True) -> sqlite3.Connection:
    """打开（必要时创建）数据库并建表，返回连接。

    使用 Row 工厂便于按列名取值；启用外键。
    check_same_thread=False 供服务层把单个连接交给某个专属线程使用时放开校验
    （服务层自己保证串行访问）。
    """
    conn = sqlite3.connect(str(db_path), check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL + 忙等待：允许"写连接(模拟循环)"与"读连接(API 读)"并发，读不必排在写后面；
    # 文件库才生效，:memory: 自动忽略。busy_timeout 避免偶发写提交瞬间的 locked 报错。
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
    except sqlite3.OperationalError:
        pass
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """对已存在的库幂等补列（CREATE TABLE IF NOT EXISTS 不会给旧表加列）。"""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(chapter_plans)").fetchall()}
    if "role" not in cols:
        conn.execute("ALTER TABLE chapter_plans ADD COLUMN role TEXT NOT NULL DEFAULT 'rising'")
    if "target_tension" not in cols:
        conn.execute("ALTER TABLE chapter_plans ADD COLUMN target_tension REAL NOT NULL DEFAULT 0.5")
    if "dramatic_question" not in cols:
        conn.execute("ALTER TABLE chapter_plans ADD COLUMN dramatic_question TEXT NOT NULL DEFAULT ''")
    if "resolution_predicate" not in cols:
        conn.execute("ALTER TABLE chapter_plans ADD COLUMN resolution_predicate TEXT NOT NULL DEFAULT ''")
    if "min_scenes" not in cols:
        conn.execute("ALTER TABLE chapter_plans ADD COLUMN min_scenes INTEGER NOT NULL DEFAULT 2")
    if "ending_hook" not in cols:
        conn.execute("ALTER TABLE chapter_plans ADD COLUMN ending_hook TEXT NOT NULL DEFAULT ''")
    if "hook_type" not in cols:
        conn.execute("ALTER TABLE chapter_plans ADD COLUMN hook_type TEXT NOT NULL DEFAULT ''")
    if "items_present" not in cols:
        conn.execute("ALTER TABLE chapter_plans ADD COLUMN items_present TEXT NOT NULL DEFAULT '[]'")
    if "items_introduced" not in cols:
        conn.execute("ALTER TABLE chapter_plans ADD COLUMN items_introduced TEXT NOT NULL DEFAULT '[]'")
    if "items_consumed" not in cols:
        conn.execute("ALTER TABLE chapter_plans ADD COLUMN items_consumed TEXT NOT NULL DEFAULT '[]'")
    if "target_words" not in cols:
        conn.execute("ALTER TABLE chapter_plans ADD COLUMN target_words INTEGER NOT NULL DEFAULT 2400")
    if "pov_agent" not in cols:  # 问题6：本章主讲视角（按 focus_agents 轮换，让配角有主讲场）
        conn.execute("ALTER TABLE chapter_plans ADD COLUMN pov_agent TEXT NOT NULL DEFAULT ''")
    if "exit_state" not in cols:  # 问题4（第三批）：本章必须达成的世界状态变化
        conn.execute("ALTER TABLE chapter_plans ADD COLUMN exit_state TEXT NOT NULL DEFAULT ''")
    if "audited" not in cols:  # 审计闸门：本章是否已通过（衔接/转场/道具人物合规）
        conn.execute("ALTER TABLE chapter_plans ADD COLUMN audited INTEGER NOT NULL DEFAULT 0")
    if "conflict_type" not in cols:  # S1 冲突类型轮换（治"七章同一支舞"）
        conn.execute("ALTER TABLE chapter_plans ADD COLUMN conflict_type TEXT NOT NULL DEFAULT ''")
    if "beat_povs" not in cols:  # POV 跟着节拍走：每个 beat 的视角人物
        conn.execute("ALTER TABLE chapter_plans ADD COLUMN beat_povs TEXT NOT NULL DEFAULT '[]'")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS character_chapter_logs (
            agent_id      TEXT NOT NULL,
            chapter_seq   INTEGER NOT NULL DEFAULT 0,
            actions       TEXT NOT NULL DEFAULT '',
            psychology    TEXT NOT NULL DEFAULT '',
            intention     TEXT NOT NULL DEFAULT '',
            items_changed TEXT NOT NULL DEFAULT '[]',
            PRIMARY KEY (agent_id, chapter_seq)
        )"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_character_logs_agent_chapter
           ON character_chapter_logs (agent_id, chapter_seq)"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS batch_audits (
            chapter_seq  INTEGER PRIMARY KEY,
            result_json  TEXT NOT NULL DEFAULT '{}',
            summary_json TEXT NOT NULL DEFAULT '{}',
            created_tick INTEGER NOT NULL DEFAULT 0
        )"""
    )
    # B0.6 时代隔离墙：tone_profile 旧库补 era_logic 列
    tcols = {r["name"] for r in conn.execute("PRAGMA table_info(tone_profile)").fetchall()}
    if tcols and "era_logic" not in tcols:
        conn.execute("ALTER TABLE tone_profile ADD COLUMN era_logic TEXT NOT NULL DEFAULT '{}'")
    # W1 World Skill：world_bible_sections 旧库补 summary 列（两级 summary/detail）
    wcols = {r["name"] for r in conn.execute("PRAGMA table_info(world_bible_sections)").fetchall()}
    if wcols and "summary" not in wcols:
        conn.execute("ALTER TABLE world_bible_sections ADD COLUMN summary TEXT NOT NULL DEFAULT ''")
    # W4 分层人物卡：character_cards 旧库补 3 个新列（appearance/social_role/psychology）
    ccols = {r["name"] for r in conn.execute("PRAGMA table_info(character_cards)").fetchall()}
    if ccols:
        for _c in ("appearance", "social_role", "psychology"):
            if _c not in ccols:
                conn.execute(f"ALTER TABLE character_cards ADD COLUMN {_c} TEXT NOT NULL DEFAULT ''")
    # W2 地理层：locations 旧库补 5 个新列（level/parent/culture_local/summary/detail）
    lcols = {r["name"] for r in conn.execute("PRAGMA table_info(locations)").fetchall()}
    if lcols:
        for _col, _defn in [
            ("level", "TEXT NOT NULL DEFAULT ''"),
            ("parent", "TEXT NOT NULL DEFAULT ''"),
            ("culture_local", "TEXT NOT NULL DEFAULT ''"),
            ("summary", "TEXT NOT NULL DEFAULT ''"),
            ("detail", "TEXT NOT NULL DEFAULT ''"),
        ]:
            if _col not in lcols:
                conn.execute(f"ALTER TABLE locations ADD COLUMN {_col} {_defn}")
