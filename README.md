# Novel World

`Novel World` 是一个面向长篇小说创作与小说续写的工程化写作引擎。它的核心思路不是“把更多正文塞进 prompt”，而是把影响长篇连续性的关键状态拆出来，落到项目数据库、Story Bible、关系图谱、文风画像和章节快照里，让模型在一个被约束的上下文中继续创作。

这个仓库当前最重要的能力，已经不是早期那种“贴一段文本继续写”，而是完整的 `续写工坊` 闭环：

- 原创项目：种子工坊 -> 锁定设定 -> Story Bible -> 章节草稿 -> 采纳
- 续写项目：导入原作 -> 切章 -> 蒸馏世界/人物/关系/文风 -> 选择续写模式 -> 锁定上下文 -> 生成章节

如果你要快速理解这个仓库，可以把它看成两层系统：

- 写作层：负责起稿、续写、章节编号、上下文拼装、草稿采纳
- 蒸馏层：负责把原作文本转成可复用的结构化状态

---

## 1. 项目目标

长篇写作和续写最容易崩的地方，通常不是“单段文笔差”，而是这些结构性问题：

- 人物前后不一致
- 明明已经结束的线索又突然复活
- 世界规则被后文改写
- 新书和旧书之间缺少明确边界
- 文风像在模仿设定摘要，而不是模仿作品本身

这个项目的目标，就是把这些问题从“人工盯 prompt”变成“系统约束”：

- 用 `source_documents` / `source_chapters` 保存原作切章结果
- 用 `project_meta` 和 `story_bible_v2` 保存续写元信息与结构化前史
- 用 continuation snapshot 区分“接当前书”与“开同宇宙新书”
- 用 author sheet / style skill 让文风继承不只停留在提示词
- 用章节编号规则确保 `N+1` 或 `1` 的边界是明确的

---

## 2. 当前实现状态

当前仓库已经落地的续写能力包括：

- 续写项目类型与元数据持久化
- `txt` / `epub` / 多文件文本导入
- 原文章节切分与存储
- 双模式续写：
  - `continue_current_book`
  - `new_series_book`
- B1-B7 续写蒸馏任务结构
- 续写设置保存与读取
- 续写上下文锁定
- 续写模式下的运行时章节计划装配
- 锁定前禁止生成草稿
- 续写项目前端工坊入口
- 《龙族》专项蒸馏与起稿脚本

这意味着现在的代码已经不是纯规划稿，而是可以跑通“导入 -> 蒸馏 -> 锁定 -> 生成首章”的最小闭环。

---

## 3. 两条主工作流

### 3.1 原创项目

原创项目依然保留原来的主链路：

1. 在 Dashboard 创建 `original` 项目
2. 进入种子工坊完成基础设定
3. 锁定种子后构建 Story Bible
4. 进入大纲/阅读页生成章节草稿
5. 采纳草稿并持续推进

原创项目的重点是“从零生长”。

### 3.2 续写项目

续写项目走的是另一条入口，但后半段尽量与原创项目共享页面与写作链路：

1. 在 Dashboard 创建 `continuation` 项目
2. 进入 `续写工坊`
3. 导入原作文本或文件
4. 查看切章结果
5. 配置续写蒸馏参数与写作模式
6. 启动 B1-B7 蒸馏
7. 锁定续写上下文
8. 转入共享的 `Outline` / `Reading`
9. 生成、采纳、拒绝章节草稿

续写项目的重点不是“立刻生成正文”，而是先把原作变成一个可持续写作的工程状态。

---

## 4. 续写工坊的核心设计

### 4.1 为什么单独做“续写工坊”

因为续写与原创最大的差别，不在“多一个按钮”，而在“写之前必须把原作蒸馏成状态”。

续写工坊承担四件事：

- 接收原作输入
- 产生切章与原文快照
- 蒸馏出世界、人物、关系、文风、书末状态
- 锁定续写模式对应的写作起点

### 4.2 两种续写模式

#### `continue_current_book`

适合“原书停在某处，现在从下一章继续写”。

特点：

- 章节号从原书最后章节号之后开始
- 直接继承原书最后一章的尾部语境
- 更强调书末状态和未解线索的强绑定

章节号逻辑：

```python
next_chapter_no = latest_source_chapter_no + accepted_generated_chapters + 1
```

#### `new_series_book`

适合“沿用同一宇宙，但重新开一本书”。

特点：

- 章节号从 `1` 开始
- 原书成为前史，不强接最后一段正文
- 更适合同宇宙新主线、新起点或多年后时间线

章节号逻辑：

```python
next_chapter_no = accepted_generated_chapters + 1
```

### 4.3 锁定机制

续写项目在没有锁定上下文之前，后端会禁止正式起稿：

- `POST /api/projects/{id}/chapters/drafts` 返回 `409`
- `POST /api/projects/{id}/chapters/auto-write` 返回 `409`

这么做是为了避免系统在“接当前书”还是“开新书”都没确定的时候，生成一段上下文模糊的正文。

---

## 5. B1-B7 蒸馏步骤

续写工坊当前把蒸馏过程组织成 B1-B7 七个阶段，前端会按步骤展示进度。

### `B1` 导入与切章

负责：

- 读取 `text` / `filePath` / `filePaths`
- 导入 `txt`、`epub`、多文件文本
- 清洗正文
- 自动切章
- 写入 `source_documents` 和 `source_chapters`

输出重点：

- `source_text_hash`
- `latest_source_chapter_no`
- source chapter 列表

### `B2` 世界书蒸馏

负责把原作中的世界规则、地理、主题与约束整理进续写上下文。

对续写项目来说，`B2` 不是“补充材料”，而是后续知识图谱、角色关系和写作快照的世界底座。对于任何强世界观项目，后续蒸馏是否合格，首先取决于这里是否已经产出了可写作的 `world_config`，而不是只抽出几句题材摘要。

输出重点：

- `world_bible`
- `world_bible_sections`
- `story_bible_v2.world_config_json`

### `B3` 人物 / 地点 / 势力蒸馏

负责：

- 角色归纳
- 地点梳理
- 势力关系整理
- 将结构化实体写回仓库

输出重点：

- `entities`
- `persona`
- `character_cards`
- `locations`
- `factions`

### `B4` 系列状态蒸馏

这一层是续写特别关键的一层，因为它决定“原书结束时世界停在哪里”。

输出重点：

- `timeline_json`
- `open_threads_json`
- `last_state_json`
- `narrative_constraints_json`

### `B5` 关系图谱蒸馏

负责把原作中的人物、势力、地点、事件关系整理成可检索边。

这里的目标不是只做展示用图，而是产出后续可供检索、约束和写作使用的结构化关系。也就是说，蒸馏阶段要尽量把“谁和谁是什么关系、什么势力控制什么地点、什么事件改变了什么状态”落成图谱边，而不是留到正文生成时再临时猜。

输出重点：

- `graph_edges`

### `B6` 文风 / 经历蒸馏

复用现有的 style corpus 管线，从原作文本里提炼可复用文风画像；如果用户开启了经历层，还要继续从作者随笔、访谈或经历文本中抽取“作者生命经验模型”。

这一层现在不再只是“总结文风标签”，而是要回答三件事：

- 这位作者句子怎么写
- 这位作者习惯把什么样的人放进故事中央
- 这位作者为什么会反复写这种人、这种伤口和这种命运

经历层是通用能力，不绑定某一位作者。它可以读取作者随笔、访谈、书信、回忆录或传记材料，抽取“作者生命经验模型”，再把这种模型作为最高级风格先验注入续写。

输出重点：

- `author_sheets`
- `style_skill`
- `author_experience_sources`
- `author_experience_fragments`
- `author_life_models`
- `style_packet.experience_prior`

### `B7` 写作启动快照

这一步根据 `write_mode` 生成最终写作快照：

- 接当前书：偏向原书末尾、当前危机、书末人物状态
- 开同宇宙新书：偏向前史摘要、新书标题、主角策略、时间位置

从这一层开始，续写项目要把“结构蒸馏结果”真正装配成可写正文的上下文。对高等级续写来说，快照至少要同时拿到：

- 系列/世界配置
- 书末状态与未解线索
- 知识图谱摘要
- 当前 POV 与 style state
- 作者经历层的人格先验

这一步是“蒸馏结果”进入“正式写作”的最后一道门。

### 5.1 运行时续写章节计划

仅有 `B2-B7` 入库还不够。对 `continue_current_book` 这类高连续性续写来说，如果下一章在正式写作前没有被装配成一个带种子的 `ChapterPlan`，那么 `world_config`、书末状态和知识图谱就只能以很弱的背景层进入 prompt，正文仍可能退化成泛化续写。

当前仓库已经补上这一层：

- 当续写项目进入写作阶段，且目标章没有现成计划时，系统会先自动生成一个运行时 `ChapterPlan`
- 该计划只从现有蒸馏结果组装：
  - `open_threads`
  - `last_state`
  - `world_config`
  - `graph_edges`
  - 角色 / 地点 / 势力结构库
- 计划至少补齐这些硬约束：
  - `pov_agent`
  - `cast`
  - `location_ids`
  - `beat_goals`
  - `dramatic_question`
  - `exit_state`
  - `reveal_gate`

这一步不是代替正文生成，而是确保后续 SceneWriter 能真正拿到人物、地点和关系种子，让系统写的是“这个世界的下一章”，而不是“某种像样的后续段落”。

---

## 6. 数据模型与状态落点

### 6.1 服务端项目快照

续写项目在服务层会持久化续写元信息，包括：

- `source_text_hash`
- `continuation_hint`
- `series_id`
- `source_book_title`
- `current_book_title`
- `book_index`
- `write_mode`
- `chapter_start_no`
- `latest_source_chapter_no`
- `continuation_phase`

### 6.2 SQLite 项目仓库

当前续写相关状态主要落在项目自己的 SQLite 中。

#### `project_meta`

已经扩展的续写字段：

- `source_text_hash`
- `continuation_hint`
- `series_id`
- `source_book_title`
- `current_book_title`
- `book_index`
- `write_mode`
- `chapter_start_no`
- `latest_source_chapter_no`
- `continuation_ready`
- `continuation_phase`
- `time_position`
- `protagonist_strategy`
- `inherit_unresolved_threads`

#### 原文相关表

- `source_documents`
- `source_chapters`

#### 写作与蒸馏相关表

- `story_bible_v2`
- `world_bible`
- `world_bible_sections`
- `entities`
- `persona`
- `character_cards`
- `locations`
- `factions`
- `graph_edges`
- `author_sheets`
- `style_skill`
- `chapter_drafts`
- `accepted_chapters`

#### `continuation_jobs`

用于记录续写蒸馏任务。

主要字段：

- `id`
- `project_id`
- `phase`
- `progress`
- `total`
- `status`
- `error`
- `config_json`
- `created_at`
- `updated_at`

---

## 7. 代码结构

下面是当前与续写最相关的代码区域。

```text
src/novel_engine/
  continuation/
    importer.py
    chapter_numbering.py
    settings.py
    snapshot.py
  story_bible/
    bible_builder.py
    chapter_context.py
    chapter_writer.py
    drafts.py
  repository.py
  db.py

server/
  app.py
  projects.py

frontend/
  src/views/ContinuationWorkshop.tsx
  src/components/ContinuationOutlinePanel.tsx
  src/components/ContinuationSourcePanel.tsx
  src/components/ContinuationProgress.tsx
  src/components/ContinuationModePicker.tsx
  src/components/Layouts.tsx
  src/router.tsx

scripts/
  distill_longzu_project.py
  start_longzu_continuation.py

tests/
  test_continuation_chain.py
```

### 后端核心职责

- `continuation/importer.py`
  - 读取文本
  - 导入 `epub`
  - 合并多文件
  - 切章
  - 落库

- `continuation/chapter_numbering.py`
  - 统一处理两种续写模式下的章节号

- `continuation/snapshot.py`
  - 生成写作启动快照

- `story_bible/chapter_writer.py`
  - 按模式拼接 `prev_tail`、原书尾部、前史摘要等写作上下文

- `server/projects.py`
  - 串起项目级续写工作流

- `server/app.py`
  - 提供前端调用的 REST API

### 前端核心职责

- `ContinuationWorkshop`
  - 续写页面入口

- `ContinuationOutlinePanel`
  - 主工坊 UI
  - 负责拉取原文章节、续写设置、蒸馏任务、草稿与采纳结果

- `Layouts`
  - 控制续写项目在锁定前后的导航行为

---

## 8. API 总览

### 8.1 续写专用接口

- `POST /api/projects/{id}/continuation/import`
- `GET /api/projects/{id}/continuation/source`
- `POST /api/projects/{id}/continuation/distill`
- `GET /api/projects/{id}/continuation/job`
- `GET /api/projects/{id}/continuation/stream`
- `GET /api/projects/{id}/continuation/settings`
- `PUT /api/projects/{id}/continuation/settings`
- `POST /api/projects/{id}/continuation/lock`

### 8.2 共用写作接口

续写项目在锁定之后，开始复用常规写作接口：

- `POST /api/projects/{id}/story-bible/build`
- `GET /api/projects/{id}/story-bible/status`
- `GET /api/projects/{id}/story-bible`
- `POST /api/projects/{id}/chapters/drafts`
- `GET /api/projects/{id}/chapters/drafts`
- `POST /api/projects/{id}/chapters/drafts/{draft_id}/accept`
- `POST /api/projects/{id}/chapters/drafts/{draft_id}/reject`
- `POST /api/projects/{id}/chapters/auto-write`
- `GET /api/projects/{id}/chapters/accepted`

更细的续写接口说明见 [docs/continuation-workshop.md](/C:/Users/yiyin/Desktop/novel_world/docs/continuation-workshop.md)。

---

## 9. 快速启动

### 9.1 后端

```bash
pip install -r requirements.txt
pip install -r server/requirements.txt
python -m uvicorn server.app:app --port 8000
```

### 9.2 前端

```bash
cd frontend
npm install
npm run dev -- --port 5180
```

打开 [http://localhost:5180](http://localhost:5180)。

### 9.3 环境说明

如果要跑真实模型能力，而不是只看结构或 mock：

- 需要配置服务端 LLM 设置
- 需要可用的 API Key
- 需要本地 Python 依赖完整

如果只做 UI 或结构验证：

- 前端可切 `mock`
- 后端可只跑接口结构，不一定要真实生成正文

---

## 10. 如何手动走一遍续写链路

建议按下面顺序联调：

1. 启动后端
2. 启动前端
3. 新建 `continuation` 项目
4. 在续写工坊导入原作
5. 确认切章结果
6. 保存 continuation settings
7. 启动蒸馏
8. 等待任务进入可锁定状态
9. 锁定续写上下文
10. 进入 `Outline`
11. 生成章节草稿
12. 采纳或拒绝

如果你只是验证接口，也可以直接用脚本或 API 调用走完整流程。

---

## 11. 通用框架与《龙族》验证样本

仓库的产品定义应当始终是“通用续写蒸馏框架”，而不是“龙族项目生成器”。《龙族》当前只是因为同时具备强世界观、强人物关系和强作者风格，适合作为高压测试样本与回归样本。

也就是说：

- 架构设计必须对任意长篇续写项目成立
- 数据模型不能写死在某个作者或某个宇宙上
- 专项脚本、专项语料和专项提示词只能作为验证层，不应反向定义系统边界

当前仓库里已经补了一条《龙族》验证链路，目的是把本地语料快速变成一个可续写项目，并用来压测通用能力是否成立。

相关脚本：

- [scripts/distill_longzu_project.py](/C:/Users/yiyin/Desktop/novel_world/scripts/distill_longzu_project.py)
- [scripts/start_longzu_continuation.py](/C:/Users/yiyin/Desktop/novel_world/scripts/start_longzu_continuation.py)

### 启动方式

```bash
python scripts/start_longzu_continuation.py --accept
```

脚本会尝试完成：

- 创建续写项目
- 导入本地 `epub`
- 构建 style corpus
- 可选蒸馏经历层
- 设置续写模式
- 启动蒸馏
- 锁定上下文
- 生成第一章草稿
- 可选直接采纳

默认输出目录：

```text
outputs/longzu_continuation_run/
```

### 当前对《龙族》语料的保守假设

当前仓库里的本地 `龙族全套+共七册.epub`，更适合被当作“龙族宇宙合辑语料”，而不是百分百可直接视为“完整、严格顺序、无缺漏的七册正史输入”。

所以当前默认策略更稳妥地偏向：

- 把它蒸馏成同宇宙世界资料
- 默认采用 `new_series_book`
- 沿用“路明非视角、燃中带丧、双声道吐槽、命运短句”的风格基线

而不是强行假设这是官方正册的绝对连续文本，并直接冒充“正史第 N+1 册”。

### 当前已实现的通用经历层

当前仓库已经接入“作者经历层”作为最高级文风蒸馏入口，核心思路是：

- 原作正文负责蒸馏句法、叙事节奏和场景表达
- 作者随笔 / 经历文本负责蒸馏“这个作者是怎样的人”
- 最终在写作阶段把两者同时注入 `StylePacket`

目前已经落地的能力包括：

- 续写设置中可开启 `experienceLayerEnabled`
- 可指定 `experienceSourcePath`
- 可设置 `experienceStyleLevel=max`
- 后端会把作者经历文本切片、落库并生成 `author_life_model`
- 写作时会把该模型注入 `style_packet.experience_prior`

这是一种通用方法，不是江南专用模板。它的目标是从作者经历中抽出“哪些人会被这个作者不断写、怎样的伤口会反复出现、情感会怎样转成叙事”。  
江南只是当前测试样本之一。以这次《龙族》测试为例，这一层抽出的重点不是泛泛“语言优美”，而是：

- 主角先天性的自卑和羞耻感
- 想被看见、又提前为被拒绝做防御
- 以自嘲和怀旧包裹真实痛感
- 永远站在门外、作为旁观者凝视盛大命运

### 下一阶段的通用完成定义

后续对任何续写项目的蒸馏，都不再接受“只把文风蒸出来”这种半完成状态。对于开启经历层的续写项目，蒸馏完成的定义应当是：

1. `B2` 产出可写作的 `world_config`
2. `B3` 产出主要角色、地点、势力的结构化实体
3. `B4` 产出书末状态、时间线与未解线索
4. `B5` 产出可检索的知识图谱边
5. `B6` 同时产出 style corpus 与 author life model
6. `B7` 在快照里把以上结果一起装配进写作上下文

换句话说，之后的目标不是“风格更像”，而是“世界、关系、命运和人心同时对齐”。《龙族》只负责验证这套通用完成定义是否真的扛得住。

---

## 12. 测试现状

推荐运行：

```bash
pytest tests/
cd frontend && npm run typecheck
```

当前已经补充并通过的重点测试，集中在续写链路：

- 原文导入与切章
- 多模式章节号逻辑
- Story Bible 续写载荷
- 未锁定时禁止创建草稿
- continuation job 步骤结构

如果你只想验证最核心的一组，可以直接跑：

```bash
pytest tests/test_continuation_chain.py
```

---

## 13. 当前边界与后续方向

虽然续写工坊已经可跑通，但它还不是最终形态，当前边界主要有三类。

### 13.1 任务调度层

现在的蒸馏任务已经有 `continuation_jobs` 和阶段结构，但执行方式仍偏同步，后续可以继续升级为真正的后台异步任务。

### 13.2 Distiller 拆分粒度

目前很多续写蒸馏逻辑已经具备结构入口，但还可以继续拆得更清楚，比如：

- world distiller
- entity distiller
- graph distiller
- series distiller
- experience distiller
- style retriever / verifier

### 13.3 前端交互层

当前 UI 已经能工作，但仍然更偏工程联调态，后续可继续增强：

- 文件上传体验
- 失败恢复与重试
- 更完整的进度反馈
- 更细致的原文章节编辑能力

### 13.4 验证样本与回归集

现阶段《龙族》样本已经明显改善了“江南感”和“衰小孩感”，但它的意义主要是暴露通用框架还没补齐的硬问题：

- `龙族1.txt` 的自动切章仍需加强，避免把整本书吃成单章
- 续写工坊需要把“世界配置 + 知识图谱 + 经历层”作为同一次蒸馏任务的显式完成条件
- 前端还缺少风格诊断面板，用户还看不到经历层、图谱和 style state 的联合结果
- `snapshot` 展示层还应补齐 `activeLifeModelId` 等运行期标识

后续建议把《龙族》作为回归集之一，同时再补别的题材样本，验证这套框架不会只在单一作者上成立。

---

## 14. 文档索引

如果你接下来要继续推进续写系统，建议一起看这几份文档：

- [docs/continuation-workshop.md](/C:/Users/yiyin/Desktop/novel_world/docs/continuation-workshop.md)
- [server/README.md](/C:/Users/yiyin/Desktop/novel_world/server/README.md)
- [frontend/README.md](/C:/Users/yiyin/Desktop/novel_world/frontend/README.md)

---

## 15. 重开上下文提示词

如果需要在新对话里继续这个项目，可以直接复制下面这段提示词：

```text
请先阅读 README.md、docs/continuation-workshop.md 和 续写.txt，再继续这个项目。

这是一个“长篇小说续写工程系统”，不是单次 prompt 写文。它必须优先保持通用性；任何具体作者或作品都只能作为测试样本。

你需要遵守以下目标：

1. 续写项目的蒸馏完成，不等于只做文风蒸馏。
2. 对任何续写项目，蒸馏阶段都必须同步完成：
   - 世界配置 / world_config
   - 角色、地点、势力等结构化实体
   - 书末状态、时间线、未解线索
   - 知识图谱 graph_edges
   - style corpus
   - 作者经历层 / author life model
3. 作者经历层是最高级文风蒸馏入口。它是通用能力：应从作者随笔、访谈、书信或传记中分析作者是怎样的人，再把这种“人”的模式注入续写，而不是只模仿词句。
4. 当项目需要高度贴近某位作者时，目标不只是“语言优美”，而是：
   - 主角带有先天自卑、羞耻感和旁观者姿态
   - 想被看见，但提前为受伤做防御
   - 常用自嘲、怀旧、距离感包裹真实痛感
   - 命运感要从日常裂缝里慢慢长出来
5. 写作链路要服务于可持续续写，不要把系统退化回“给一段 prompt 直接生成正文”。

当前仓库已经实现的关键状态：
- continuation settings 已支持 experienceLayerEnabled / experienceSourcePath / experienceStyleLevel
- 后端已支持 author_experience_sources / fragments / author_life_models
- style_packet 已支持 experience_prior
- 《龙族》已经跑通过一版经历层样例，但它只是验证样本，不应成为系统的专用定义
- source txt 切章仍需继续修

你开始工作前，先给出明确方案，再动代码。
优先做高收益、能闭环验证的改动；改完后跑测试或最小验证，并汇报还剩什么没完成。
```

---

## License

MIT
