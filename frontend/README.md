# 前端

前端使用 React 18 + TypeScript + Vite，负责把原创与续写两条工作流统一到一个项目界面里。

## 启动

```bash
cd frontend
npm install
npm run dev -- --port 5180
```

默认开发地址：

```text
http://localhost:5180
```

## 适配器

前端通过 `EngineAdapter` 访问数据层，当前有两套实现：

- `HttpAdapter`
- `MockAdapter`

通过 `.env` 控制：

```text
VITE_ADAPTER=http
VITE_API_BASE_URL=http://localhost:8000
```

## 当前续写入口

续写项目已不再走旧的单独“继续写一段”面板，而是走统一的 `续写工坊` 入口：

- Dashboard 新建 `continuation` 项目
- 未锁定前进入 `/p/:projectId/continuation`
- 锁定后进入共享的 `/outline` 与 `/read`

## 关键页面与组件

- [src/views/Dashboard.tsx](/C:/Users/yiyin/Desktop/novel_world/frontend/src/views/Dashboard.tsx)
- [src/views/ContinuationWorkshop.tsx](/C:/Users/yiyin/Desktop/novel_world/frontend/src/views/ContinuationWorkshop.tsx)
- [src/components/ContinuationOutlinePanel.tsx](/C:/Users/yiyin/Desktop/novel_world/frontend/src/components/ContinuationOutlinePanel.tsx)
- [src/components/ContinuationSourcePanel.tsx](/C:/Users/yiyin/Desktop/novel_world/frontend/src/components/ContinuationSourcePanel.tsx)
- [src/components/ContinuationProgress.tsx](/C:/Users/yiyin/Desktop/novel_world/frontend/src/components/ContinuationProgress.tsx)
- [src/components/ContinuationModePicker.tsx](/C:/Users/yiyin/Desktop/novel_world/frontend/src/components/ContinuationModePicker.tsx)
- [src/components/Layouts.tsx](/C:/Users/yiyin/Desktop/novel_world/frontend/src/components/Layouts.tsx)
- [src/router.tsx](/C:/Users/yiyin/Desktop/novel_world/frontend/src/router.tsx)

## 续写工坊页面职责

### Source

- 导入原文
- 展示已切章节
- 查看 source 统计信息

### Distill Progress

- 展示 B1-B7 任务步骤
- 展示当前蒸馏配置
- 轮询任务状态

### Writing Mode

- 切换 `continue_current_book`
- 切换 `new_series_book`
- 保存续写设置
- 锁定上下文

### Draft Actions

- 生成草稿
- 拒绝草稿
- 采纳草稿

## 重要交互约束

- 续写项目在 `continuationReady=false` 时，草稿生成按钮会被禁用
- 续写项目锁定前默认不进入共享大纲页
- 锁定完成后，续写项目与原创项目共用 `Outline` / `Reading`

## 开发建议

- 做前端改动时优先保持原创与续写共用主页面，避免重新长出一套平行 UI
- 若新增续写接口，先同步更新：
  - `src/types.ts`
  - `src/adapters/EngineAdapter.ts`
  - `src/adapters/HttpAdapter.ts`
  - `src/adapters/MockAdapter.ts`

## 检查

```bash
npm run typecheck
```
