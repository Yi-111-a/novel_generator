# Novel World — Multi-Agent Novel Simulation Engine

<div align="center">

[中文](#中文) · [English](#english)

</div>

---

<a id="中文"></a>

## 中文

> 把"写小说"建模成一个**多 Agent 模拟 + 世界状态机 + 渐进知识图谱**，让长篇连载里那些常见的脏问题——人物前后不一致、道具说没就没又突然出现、势力名字挂在墙上从不出场、世界观一次性砸光——在系统层面被约束住，而不是靠 prompt 兜底。

### 与常见 LLM 写作工具的对比

市面上大多数 LLM 写作工具是 **「prompt + 长上下文 + 一次性大纲」**。Novel World 走的是另一条路：

| 维度 | 常见做法 | 本项目做法 |
| --- | --- | --- |
| **状态** | 全部塞在 prompt / 长上下文里 | **SQLite 状态库 + 知识图谱**，prose 写完后 LLM 抽事实回写，下一章按真实状态生成 |
| **人物连贯** | 靠人设卡 + 历史正文 | **逐章人物日志**（行为/心理/意图）按角色累积，写下一章时注入近 5 章日志 |
| **道具/线索** | 全靠 LLM 记忆 | **物品台账** `held/transferred/lost/consumed/destroyed/sacrificed` 完整生命周期 |
| **大纲** | 一次性生成全书 | **Part → Arc → Chapter 三级懒生成**，每章回看真实状态 |
| **世界观** | 第 1 章全部讲完 | **渐进细化**：前 3 章铺基础面，冲突 milestone 处按需深化 |
| **势力/地理** | 设定挂着不用 | **势力一等公民**：成员轮换进 cast、压力曲线影响 beat、关系进图谱 |
| **检索** | RAG over 正文 | **分层 RAG**：常驻层 + 种子层 + 关键词层 + **图谱子图**层，token 预算装配 |
| **审计** | 人工 review | **单章闸门 + 每 10 章批量审计**：物品/势力/世界观/伏笔/人物连贯 + LLM 压缩摘要 |
| **图谱** | 静态可视化 | **动态时间轴**：边带 `sinceChapter`/`lastActiveChapter`，注意力衰减，按章节范围过滤 |

一句话：**让结构保证一致性，让 LLM 只负责风格和叙事。**

### 架构总览

```
              ┌─────────────────────────────────────────────────┐
              │                   FastAPI Server                │
              │  /api/projects · /seed · /world · /graph · /run │
              └───────────────┬─────────────────────────────────┘
                              │
              ┌───────────────▼─────────────────┐        ┌──────────────────┐
              │         Planner (大纲生成)       │◀──────▶│   World Bible    │
              │  Part/Arc/Chapter 懒生成         │        │  渐进 deepen     │
              │  + 势力压力 + 世界观 + 库存继承   │        └──────────────────┘
              └───────────────┬─────────────────┘
                              │ ChapterPlan
              ┌───────────────▼─────────────────┐
              │        Director (导演循环)       │
              │  按 beat 驱动 scripted 模式      │
              └───────┬───────────────┬─────────┘
                      │               │
              ┌───────▼──────┐  ┌─────▼────────────┐
              │ SceneWriter  │  │  FactExtractor   │
              │ + 分层 RAG   │  │  facts/inventory │
              │ + 人物日志   │  │  reveals/beats   │
              └───────┬──────┘  └─────┬────────────┘
                      │               │
                      ▼               ▼
                  prose ────▶ 单章 audit ────▶ 回写状态库
                                    │
                          每 10 章 ──┴─▶ BatchAuditor + 压缩摘要
```

**前端**（React + TS + Vite + Tailwind + Zustand）：种子向导、人物/势力/地点配置面板、章节阅读器、知识图谱可视化（Canvas 力布局 + 时间轴过滤）。

### 目录结构

```
src/novel_engine/
  models.py            # 数据模型：Entity / Fact / ChapterPlan / InventoryItem /
                       # Faction / GraphEdge / CharacterChapterLog / BatchAudit
  repository.py        # SQLite CRUD
  db.py                # DDL
  planner.py           # 三级懒大纲 + 势力压力 + 世界观交代 + 库存继承
  director.py          # 导演循环 + 章末审计触发
  worldbible.py        # 世界圣经 + deepen_section 渐进细化
  style_skill.py       # 文风蒸馏 / 量化 / 闸门
  narration/
    scene_writer.py    # SceneSpec → prose + 人物日志注入
    fact_extractor.py  # prose → SceneDelta + character_beats
    retrieval.py       # 分层 RAG：常驻 + 种子 + 关键词 + 图谱子图
    audit.py           # 单章审计闸门
    batch_audit.py     # 每 10 章批量审计 + 压缩摘要
  llm/
    deepseek.py        # DeepSeek (OpenAI 兼容) 适配
    mock.py            # 离线 mock
server/
  app.py               # FastAPI 入口
  dossier.py           # 人物档案 .md 镜像
frontend/
  src/components/
    KnowledgeGraph.tsx  # Canvas 力布局图谱 + 时间轴过滤
tests/
  test_character_chapter_logs.py
  test_batch_audit.py
  test_item_ledger.py
```

### 关键能力

**1. 逐章人物日志** — 每章为在场角色累积「行为/心理/意图/物品变化」，下一章自动注入近 5 章摘要。解决跨章人物行为矛盾。

**2. 物品生命周期约束** — `held → transferred → lost → consumed/destroyed/sacrificed` 完整状态机。跨章继承自动过滤已销毁道具，RAG 不再"复活"已消耗物品。

**3. 每 10 章批量审计** — 物品一致性、势力使用率、世界观覆盖、伏笔进度、人物连贯，LLM 压缩为三轴摘要供后续参考。

**4. 知识图谱时间轴** — 边带 `sinceChapter`/`lastActiveChapter`，前端"当前章节/全量结构"切换 + 章节范围滑块。

**5. 势力融入大纲** — Arc/Chapter prompt 注入势力概览 + 成员轮换 + 势力压力曲线。

**6. 渐进世界观交代** — 前 3 章自动铺基础面，milestone 处主动 deepen，不再第 1 章倒豆子。

### 技术栈

- **后端**：Python 3.10+ / FastAPI / SQLite / OpenAI SDK（DeepSeek 兼容端点）
- **前端**：React 18 / TypeScript / Vite / Tailwind CSS / Zustand / Recharts / Lucide
- **LLM**：默认 DeepSeek，`LLM_PROVIDER=mock` 可全离线测试

### 快速开始

```bash
# 后端
pip install -r requirements.txt
pip install fastapi uvicorn
cp .env.example .env   # 编辑 .env，填 DEEPSEEK_API_KEY
uvicorn server.app:app --port 8000

# 前端
cd frontend && npm install
npm run dev -- --port 5180
```

打开 `http://localhost:5180`。

```bash
# 测试
pytest tests/
```

### 设计取舍

**为什么不用 LangChain / LlamaIndex？** — 长篇生成的核心难点是跨章状态一致性，不是单次 RAG 召回。通用框架的 chain/agent 抽象掩盖了"状态在哪里"。本项目把 SQLite + 自定义检索分层做成一等公民，prompt 反而成了薄壳。

**为什么用 SQLite 而不是向量库？** — 小说体量下事实量 1k–10k，关键词 + 图谱子图 + 实体 ID 直查够用，向量召回的延迟和不可解释远大于收益。

**为什么不一次性生成全书大纲？** — 一次性大纲到第 5 章就和真实状态漂移。懒生成每章回看真实库存/日志/势力/世界观进度，结构层面根治漂移。

### 项目状态

- ✅ W0：设定保真闸门 + 主角 POV 偏置 + 地点对账门
- ✅ W1：世界观引擎核心（多级生成 + 校验回路 + 渐进细化）
- ✅ W2：地理层（canon 地点两级 + 风土人情）
- ✅ W3：势力系统（多级生成 + 关系图 + 核心成员落卡）
- ✅ W4：分层人物卡引擎（三维度 + 小传 + 弧线 + 校验回路）
- ✅ W5：知识图谱（静态边 + FactExtractor 增量 + 注意力衰减）
- ✅ W6：RAG 注入（子图检索 + Lorebook token 预算）
- ✅ 逐章人物日志 + 批量审计 + 物品生命周期 + 图谱时间轴

---

<a id="english"></a>

## English

> Model "writing a novel" as a **multi-agent simulation + world state machine + progressive knowledge graph**, so the common pain points of long-form serialized fiction — character inconsistency, vanishing/resurrecting props, factions that exist only on paper, world-building info-dumped in chapter 1 — are constrained at the system level, not papered over with prompt engineering.

### How This Differs from Typical LLM Writing Tools

Most LLM writing tools follow the pattern: **"prompt + long context + one-shot outline"**. Novel World takes a fundamentally different approach:

| Dimension | Common Approach | Our Approach |
| --- | --- | --- |
| **State** | Everything in prompt / context window | **SQLite state DB + knowledge graph** — LLM extracts facts after each chapter, next chapter generated from real state |
| **Character Continuity** | Character cards + past prose | **Per-chapter character logs** (actions/psychology/intentions) accumulated per character, last 5 chapters injected into POV/present character context |
| **Props / Clues** | Rely on LLM memory | **Item ledger** with full lifecycle: `held/transferred/lost/consumed/destroyed/sacrificed` |
| **Outline** | Generated all at once | **Part → Arc → Chapter lazy generation** — each chapter looks back at real state |
| **World-building** | Dumped in chapter 1 | **Progressive deepening**: basics in first 3 chapters, detail added at conflict milestones |
| **Factions / Geography** | Settings that never get used | **Factions as first-class citizens**: member rotation into cast, pressure curves affect beats, relationships as graph edges |
| **Retrieval** | RAG over raw prose | **Layered RAG**: resident (world overview/protagonist) + seed (cast/location) + keyword + **graph subgraph**, assembled within token budget |
| **Auditing** | Manual review | **Per-chapter gate + batch audit every 10 chapters**: item consistency, faction usage, world coverage, foreshadow progress, character continuity + LLM-compressed summary |
| **Graph** | Static visualization | **Dynamic timeline**: edges carry `sinceChapter`/`lastActiveChapter`, attention decay, frontend chapter-range slider filtering |

In one sentence: **Let structure guarantee consistency; let the LLM focus on style and narrative.**

### Architecture Overview

```
              ┌─────────────────────────────────────────────────┐
              │                   FastAPI Server                │
              │  /api/projects · /seed · /world · /graph · /run │
              └───────────────┬─────────────────────────────────┘
                              │
              ┌───────────────▼─────────────────┐        ┌──────────────────┐
              │           Planner               │◀──────▶│   World Bible    │
              │  Part/Arc/Chapter lazy gen       │        │  Progressive     │
              │  + faction pressure + inventory  │        │  deepening       │
              └───────────────┬─────────────────┘        └──────────────────┘
                              │ ChapterPlan
              ┌───────────────▼─────────────────┐
              │           Director              │
              │  Beat-driven scripted mode       │
              └───────┬───────────────┬─────────┘
                      │               │
              ┌───────▼──────┐  ┌─────▼────────────┐
              │ SceneWriter  │  │  FactExtractor   │
              │ + Layered RAG│  │  facts/inventory │
              │ + Char logs  │  │  reveals/beats   │
              └───────┬──────┘  └─────┬────────────┘
                      │               │
                      ▼               ▼
                  prose ────▶ Per-ch audit ────▶ Write back to state DB
                                    │
                        Every 10 ch ┴─▶ BatchAuditor + compressed summary
```

**Frontend** (React + TS + Vite + Tailwind + Zustand): seed wizard, character/faction/location config panels, chapter reader, knowledge graph visualization (custom Canvas force layout + timeline filtering).

### Key Capabilities

**1. Per-Chapter Character Logs** — After each chapter, extracts and stores "actions / psychology / intentions / item changes" for every present character. Automatically injects last 5 chapters of logs for POV and on-scene characters when writing the next chapter.

**2. Item Lifecycle Constraints** — Full state machine: `held → transferred → lost → consumed/destroyed/sacrificed`. Cross-chapter inheritance filters out destroyed items. RAG retrieval skips consumed entities.

**3. Batch Audit Every 10 Chapters** — Item consistency, faction member usage rate, world-bible coverage, foreshadow progress, character log coherence. LLM compresses into plot/foreshadow/character three-axis summary for future reference.

**4. Knowledge Graph Timeline** — Edges carry `sinceChapter`/`lastActiveChapter`. Frontend provides "current chapter / full structure" toggle + chapter range slider. Combined with attention bump/decay mechanics.

**5. Faction Integration into Outlines** — Arc/Chapter prompts receive faction overview + member rotation + faction pressure curves.

**6. Progressive World-building** — First 3 chapters auto-insert foundational exposition beats. Director proactively deepens world-bible sections at plot milestones.

### Tech Stack

- **Backend**: Python 3.10+ / FastAPI / SQLite / OpenAI SDK (DeepSeek-compatible endpoint)
- **Frontend**: React 18 / TypeScript / Vite / Tailwind CSS / Zustand / Recharts / Lucide
- **LLM**: DeepSeek by default; set `LLM_PROVIDER=mock` for fully offline testing

### Quick Start

```bash
# Backend
pip install -r requirements.txt
pip install fastapi uvicorn
cp .env.example .env   # Edit .env, fill in DEEPSEEK_API_KEY
uvicorn server.app:app --port 8000

# Frontend
cd frontend && npm install
npm run dev -- --port 5180
```

Open `http://localhost:5180`.

```bash
# Tests
pytest tests/
```

### Design Tradeoffs

**Why not LangChain / LlamaIndex?** — The core challenge of long-form generation is cross-chapter state consistency, not single-query RAG recall quality. Generic chain/agent abstractions obscure "where state lives." This project makes SQLite + custom layered retrieval first-class citizens; prompts become a thin shell.

**Why SQLite instead of a vector DB?** — At novel scale, extracted facts are in the 1k–10k range. Keyword + graph subgraph + entity ID lookup is sufficient. Vector recall adds latency and opacity that outweigh benefits.

**Why not generate the full outline at once?** — One-shot outlines drift from real state by chapter 5. Lazy generation lets each chapter look back at real inventory, character logs, faction pressure, and world-deepening progress — a structural cure for drift.

### Project Status

- ✅ W0: Setting fidelity gate + protagonist POV bias + location reconciliation
- ✅ W1: World engine core (multi-level generation + validation loop + progressive deepening)
- ✅ W2: Geography layer (canon locations + local customs)
- ✅ W3: Faction system (multi-level generation + relationship graph + key members)
- ✅ W4: Layered character card engine (3 dimensions + bio + arc + validation)
- ✅ W5: Knowledge graph (static edges + FactExtractor incremental + attention decay)
- ✅ W6: RAG injection (subgraph retrieval + Lorebook token budget)
- ✅ Per-chapter character logs + batch audit + item lifecycle + graph timeline

---

## License

MIT
