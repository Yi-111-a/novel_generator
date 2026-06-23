# 后端服务

`server/` 目录提供 FastAPI 接口，负责把小说工程状态、续写工坊、Story Bible、章节草稿和 SSE 广播暴露给前端。

## 启动

在仓库根目录执行：

```bash
pip install -r requirements.txt
pip install -r server/requirements.txt
python -m uvicorn server.app:app --port 8000
```

默认 API 基址：

```text
http://localhost:8000
```

## 主要职责

- 项目管理
- 原创种子工坊
- Story Bible 构建
- 源文导入与切章
- 续写蒸馏任务
- 章节草稿生成 / 采纳 / 拒绝
- 实时 SSE 推流

## 续写相关接口

### 项目创建

`POST /api/projects`

当 `type=continuation` 时，项目创建后会立刻准备续写用写作仓库，不走原创种子流程。

### 原文导入

`POST /api/projects/{id}/continuation/import`

请求体支持：

- `text`
- `filePath`
- `filePaths`

### 原文章节查询

`GET /api/projects/{id}/continuation/source`

### 续写设置

- `GET /api/projects/{id}/continuation/settings`
- `PUT /api/projects/{id}/continuation/settings`

### 蒸馏任务

- `POST /api/projects/{id}/continuation/distill`
- `GET /api/projects/{id}/continuation/job`
- `GET /api/projects/{id}/continuation/stream`

### 锁定续写上下文

`POST /api/projects/{id}/continuation/lock`

锁定后项目会具备：

- `continuationReady=true`
- continuation snapshot
- continuation phase 更新

## 章节生成约束

对于续写项目，如果没有完成锁定：

- `POST /api/projects/{id}/chapters/drafts` 返回 `409`
- `POST /api/projects/{id}/chapters/auto-write` 返回 `409`

这是当前服务端最重要的保护之一。

## 数据与持久化

服务端项目快照会持久化续写元信息，项目自己的 SQLite 仓库会持久化：

- `project_meta` 续写字段
- `source_documents`
- `source_chapters`
- `story_bible_v2`
- `continuation_jobs`
- `chapter_drafts`
- `accepted_chapters`

## 续写实现落点

核心文件：

- [server/app.py](/C:/Users/yiyin/Desktop/novel_world/server/app.py)
- [server/projects.py](/C:/Users/yiyin/Desktop/novel_world/server/projects.py)
- [src/novel_engine/db.py](/C:/Users/yiyin/Desktop/novel_world/src/novel_engine/db.py)
- [src/novel_engine/repository.py](/C:/Users/yiyin/Desktop/novel_world/src/novel_engine/repository.py)
- [src/novel_engine/continuation/importer.py](/C:/Users/yiyin/Desktop/novel_world/src/novel_engine/continuation/importer.py)
- [src/novel_engine/continuation/snapshot.py](/C:/Users/yiyin/Desktop/novel_world/src/novel_engine/continuation/snapshot.py)
- [src/novel_engine/continuation/chapter_numbering.py](/C:/Users/yiyin/Desktop/novel_world/src/novel_engine/continuation/chapter_numbering.py)

## 运行与联调建议

- 前端本地调试时把 `VITE_ADAPTER=http`
- 若只验结构，可切 `mock`
- 续写链路联调顺序建议：
  1. 创建 continuation 项目
  2. 导入原文
  3. 保存 continuation settings
  4. distill
  5. lock
  6. create draft

## 测试

建议在仓库根目录运行：

```bash
pytest tests/test_continuation_chain.py
```

如果只做接口烟测，也可以直接跑前后端并在 UI 中走一遍续写工坊流程。
