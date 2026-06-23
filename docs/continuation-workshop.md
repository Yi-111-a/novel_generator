# 续写工坊说明

本文档对应当前仓库里已经实现的 `续写工坊` 版本，而不是 `续写.txt` 里的纯规划态方案。

## 目标

续写项目不再只是“给一段文本继续写”，而是把原作导入为一个可持续写作的工程状态：

- 原作文本被切章并持久化
- 世界观、人物、关系、书末状态进入 Story Bible
- 用户显式选择写作模式
- 系统生成对应的启动快照
- 只有锁定上下文后才允许产出章节

## 两种写作模式

### `continue_current_book`

用于接着当前这本书往后写。

- 章节号从 `latest_source_chapter_no + 1` 开始
- 默认继承原书最后章节的尾部文本 `prev_tail`
- 强绑定原书结尾状态与未解线索

章节号计算：

```python
next_chapter_no = latest_source_chapter_no + accepted_generated_chapters + 1
```

### `new_series_book`

用于基于同一宇宙重开一本新书。

- 章节号从 `1` 开始
- 不硬接原书最后一段文本
- 原作更多作为“前史”而不是“上一页”

章节号计算：

```python
next_chapter_no = accepted_generated_chapters + 1
```

## 前端入口

- Dashboard 中创建 `continuation` 类型项目
- 项目未锁定前默认进入 `/p/:id/continuation`
- 锁定完成后再进入共享的 `/outline` 与 `/read`

主要文件：

- [frontend/src/views/ContinuationWorkshop.tsx](/C:/Users/yiyin/Desktop/novel_world/frontend/src/views/ContinuationWorkshop.tsx)
- [frontend/src/components/ContinuationOutlinePanel.tsx](/C:/Users/yiyin/Desktop/novel_world/frontend/src/components/ContinuationOutlinePanel.tsx)
- [frontend/src/components/Layouts.tsx](/C:/Users/yiyin/Desktop/novel_world/frontend/src/components/Layouts.tsx)

## 后端接口

### 导入原文

`POST /api/projects/{id}/continuation/import`

支持：

- `text`
- `filePath`
- `filePaths`

写入：

- `source_documents`
- `source_chapters`
- `project_meta.source_text_hash`
- `project_meta.latest_source_chapter_no`

### 查看原文章节

`GET /api/projects/{id}/continuation/source`

### 启动蒸馏

`POST /api/projects/{id}/continuation/distill`

当前返回的任务结构按 B1-B7 组织，并落到 `continuation_jobs`。

对于高等级续写项目，`distill` 的含义不是“跑一下文风提取”，而是把后续写作真正依赖的结构状态一次性准备到位。实际完成标准应包括：

- `B2` 世界配置可写
- `B3` 实体可检索
- `B4` 书末状态与未解线索明确
- `B5` 图谱边可检索
- `B6` 文风与经历层同时可用
- `B7` 写作快照能装配这些状态

### 运行时续写章节计划

这里要特别强调一个实现边界：蒸馏完成，不等于这些状态已经真的进入正文生成。

如果续写项目在进入写作阶段时，下一章没有一个正式的 `ChapterPlan`，系统就会退化成自由续写。那样即使 `world_config`、`open_threads`、`graph_edges` 都已经入库，SceneWriter 也往往拿不到足够强的种子，只能以弱背景方式使用这些信息。

因此当前实现新增了一层运行时装配：

- 续写模式下，如果目标章还没有计划，后端会先自动创建一个运行时 `ChapterPlan`
- 该计划只读取已有蒸馏结果，不反向改写蒸馏层
- 计划会显式补齐：
  - `pov_agent`
  - `cast`
  - `location_ids`
  - `beat_goals`
  - `dramatic_question`
  - `exit_state`
  - `reveal_gate`
- 数据来源固定来自：
  - `open_threads`
  - `last_state`
  - `world_config`
  - `graph_edges`
  - 角色 / 地点 / 势力结构库

这样做的目的，是把“书末状态、未解线索、知识图谱摘要”真正压进下一章，而不是只放在 Story Bible 里做展示。

### 查询蒸馏任务

`GET /api/projects/{id}/continuation/job`

### 读取 / 保存续写设置

- `GET /api/projects/{id}/continuation/settings`
- `PUT /api/projects/{id}/continuation/settings`

关键字段包括：

- `writeMode`
- `sourceBookTitle`
- `currentBookTitle`
- `bookIndex`
- `timePosition`
- `protagonistStrategy`
- `inheritUnresolvedThreads`
- `continuationHint`
- `experienceLayerEnabled`
- `experienceLayerMode`
- `experienceSourcePath`
- `experienceStyleLevel`

### 锁定续写上下文

`POST /api/projects/{id}/continuation/lock`

锁定后会生成 continuation snapshot，并把项目标记为 `continuationReady=true`。

## 数据落点

### `project_meta`

已扩展字段：

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
- `experience_layer_enabled`
- `experience_layer_mode`
- `experience_source_path`
- `experience_style_level`
- `active_life_model_id`

### 经历层相关表

当用户开启最高级文风蒸馏时，还会写入：

- `author_experience_sources`
- `author_experience_fragments`
- `author_life_models`

这部分不是附属展示，而是后续 `StylePacket` 里的经历先验来源。

### `continuation_jobs`

用于记录续写蒸馏任务状态。

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

## 续写锁定前的约束

续写项目在 `continuationReady=false` 时：

- `POST /chapters/drafts` 会返回 `409`
- `POST /chapters/auto-write` 会返回 `409`

目的是防止还没决定“接原书”还是“开新书”就直接写正文。

## Story Bible 注入

当前续写上下文会进入：

- `continuation_meta`
- `continuation_snapshot`
- `world_config`
- `timeline_json`
- `open_threads_json`
- `last_state_json`
- `narrative_constraints_json`
- `author_life_model`
- `style_state`

在当前实现里，这些状态不只存在于快照展示层，也会继续影响运行时章节计划与场景写作：

- `open_threads` 和 `last_state` 会参与下一章 `ChapterPlan` 装配
- `graph_edges` 和实体库会为 `cast / location / reveal_gate` 提供种子
- `continuation_snapshot` 会把书末状态和未解线索继续传入 `SceneSpec`
- 最终让 SceneWriter 的检索链路拿到真实的角色、地点和关系约束

并在 `chapter_writer` 中影响：

- `prev_tail` 选择
- 起始章节号
- 前史摘要注入方式
- POV 与叙述风格装配
- 主角人格先验和情绪推进方式

## 验证样本：《龙族》

《龙族》目前是仓库里的验证样本之一，不是系统目标本身。如果要直接跑这组验证样本，可使用：

- [scripts/distill_longzu_project.py](/C:/Users/yiyin/Desktop/novel_world/scripts/distill_longzu_project.py)
- [scripts/start_longzu_continuation.py](/C:/Users/yiyin/Desktop/novel_world/scripts/start_longzu_continuation.py)

默认思路：

- 优先蒸馏本地 `epub` 的世界和文风
- 默认使用 `new_series_book`
- 保留“路明非视角、燃中带丧、双声道吐槽、命运短句”的基准提示

如果开启经历层，还应同时指定作者经历文本，例如：

- `龙与少年游：江南随笔精选+(江南)+(Z-Library).epub`

并把它视为“作者人格与生命经验语料”，而不是普通正文补料。

这组验证样本的作用，是验证通用框架在“强世界观 + 强人物关系 + 强作者风格”条件下是否仍然成立。对《龙族》这组测试来说，蒸馏时需要同时完成三件事：

1. 把《龙族》正文蒸成世界配置、角色状态、时间线和知识图谱
2. 把江南随笔蒸成作者经历层 / author life model
3. 在 `B7` 写作快照里把两者联合装配，供续写正文调用

## 当前边界

已经实现的是“可跑通的最小闭环”：

- 导入
- 蒸馏
- 锁定
- 起稿

还不是完全体：

- 蒸馏任务目前偏同步
- 细粒度 distiller 仍有继续拆分空间
- UI 里还没有做更重的文件上传交互打磨

## 重开上下文建议

如果要在新对话里继续推进续写工坊，建议明确告诉模型：

- 先读 `README.md`、`docs/continuation-workshop.md`、`续写.txt`
- 蒸馏不是只做风格，而是同步完成世界配置、实体、时间线、知识图谱和经历层
- 系统必须保持通用性，具体作品只能作为测试样本
- 如果在做《龙族》验证，再结合江南随笔抽取作者人格模式
- 开始动代码前先给出方案
