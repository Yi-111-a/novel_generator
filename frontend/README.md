# 小说模拟引擎 · 前端

React 18 + Vite + TypeScript + Tailwind + Zustand + React Router + Recharts + lucide-react。
对接小说模拟引擎后端（REST + SSE），也可离线用内置 Mock 预览全部界面。

## 快速开始

```bash
cd frontend
npm install
cp .env.example .env     # 默认 VITE_ADAPTER=mock，开箱即可离线预览
npm run dev
```

打开后默认带一个内置项目「断剑·青冥旧案」（修真复仇，主角沈砚），已在"写作中"，
可直接浏览模拟/阅读/账本检查器；也可在仪表盘新建小说，走一遍种子工坊。

## 适配器：一键切换 http / mock

数据访问抽象为 `EngineAdapter`（`src/adapters/EngineAdapter.ts`），所有项目内方法带 `projectId`：

- **`HttpAdapter`（主实现）**：对接真后端 REST + SSE。端点约定见 `HttpAdapter.ts` 顶部注释；
  聊天用 `fetch` 流式解析 SSE，模拟用每项目独立的 `EventSource`。
- **`MockAdapter`（离线兜底）**：模块级单例，内置修真复仇世界 + 假 SSE 事件流 + 流式聊天共创。

切换：`.env` 里设 `VITE_ADAPTER=http`（默认，连真后端）或 `mock`（离线）。
真后端地址用 `VITE_API_BASE_URL`（开发时 vite 会把 `/api` 代理过去）。

## 三个核心特性

1. **种子工坊**（`views/SeedWorkshop.tsx`）：左聊天共创、右实时种子草稿 + 完成度清单；
   达标（`completeness.ready`）才解锁"完成种子 → 开始写作"，点击锁定不可变层、转入 `writing` 并启动模拟。
2. **多部小说并行**：每个项目独立工作区；切换项目不中断其它项目正在跑的模拟
   （Mock 的模拟计时器在适配器层独立运行，与 React 订阅解耦）。
3. **既是成品也是开发工具**：顶栏「开发者模式」开关。关：干净成品流程；
   开：显示「账本检查器」、阅读视图叠加标注、上帝控制台显示更多调试信息。

## 视图

- `/` 我的小说（仪表盘）：卡片网格、状态/进度/呼吸点、新建/重命名/删除（二次确认）。
- `/p/:id/seed` 种子工坊 · `/world` 世界配置 · `/sim` 模拟(上帝视角) · `/read` 阅读 · `/ledger` 账本检查器(dev)。
- `/settings` 全局设置（API Key/Base URL/Model + 测试连接）。

状态门禁：`seeding` 时模拟/阅读/检查器锁定并提示"先完成种子"。

## 设计取舍（合理假设）

- Mock 的运行态/聊天/草稿存内存（仅 `ApiConfig` 进 localStorage），刷新后重置；够做 UI 预览。
- `mystery_set / irony_set / conflict_pairs` 在检查器里由 facts + 各角色账本 + 读者账本客户端推导。
- 世界配置的"信息不对称"校验在种子层用"角色数 < 2"作近似预警（种子层无逐 agent 已知事实）。
- 诚实性闸门警告：存在 `mustResolve && status==='open'` 的伏笔即红色提示。

## 脚本

```bash
npm run dev        # 开发
npm run build      # tsc -b && vite build（生产构建）
npm run typecheck  # 仅类型检查
```
