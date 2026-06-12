# 小说模拟引擎 · 后端服务层（FastAPI · REST + SSE）

把 M1–M4 引擎封装成 HTTP 服务，按前端 `HttpAdapter` 的端点契约暴露。
每个项目（小说）一套**隔离**的世界状态（独立 in-memory SQLite + 独立单线程执行器），
后台 asyncio 循环驱动导演推进事件流，并经 SSE 实时广播。

## 启动

```bash
pip install -r server/requirements.txt        # fastapi/uvicorn（引擎依赖见根 requirements）
python -m uvicorn server.app:app --port 8000  # 仓库根目录执行
```

前端对接：`frontend/.env` 设 `VITE_ADAPTER=http`、`VITE_API_BASE_URL=http://localhost:8000`，
开发时 vite 会把 `/api` 代理过来。

## 冒烟测试

```bash
python -m server.smoke   # 走一遍 创建→种子→锁定→单步→各读取→上帝动作→删除
```

## 端点（对应 HttpAdapter）

- `GET/PUT /api/config`，`POST /api/config/test`
- `GET/POST /api/projects`，`PATCH/DELETE /api/projects/{id}`
- `GET /api/projects/{id}/seed/chat`，`POST .../seed/chat`（**SSE**：逐字 token，末条带 draft）
- `GET/PUT .../seed/draft`，`POST .../seed/lock`
- `GET .../world | beats | threads | endings | personas`
- `GET .../knowledge/{agentId}`，`GET .../reader-knowledge?upto=N`
- `GET .../foreshadows | scenes`
- `POST .../control {action: play|pause|step}`，`POST .../god`（GodAction）
- `GET .../stream`（**SSE**：`event: sim` / `event: delta`）

## 工作机制

- **种子工坊**：`seedbuilder` 规则渐进填充草稿并推导完成度；配置了 LLM 则用它生成更自然的共创回复。
- **锁定**：`SeedDraft → Repository`（世界圣经/角色/实体/故事线/候选结局/初始账本与信息差/must_resolve 伏笔）。
- **写作循环**：`Director` 每 ~2.5s 推进一拍（角色决策→校验→落库→传播→写回），新事件经 SSE 广播。
- **叙述渲染**：`getScenes/reader/foreshadows` 时按需重建场景与读者账本（M3/M4 `Editor`，含张弛曲线/喘息），并回收被揭真相命中的伏笔。
- **隔离**：每项目独立执行器线程，sqlite 串行访问；切换/订阅互不影响其它项目的模拟。

## 合理假设

- 项目与世界状态存内存（重启清空）；仅 `ApiConfig` 落 `server/.data/config.json`。
- 未配置 LLM key 时：角色用确定性 Mock（产出较平的事件），两难/叙述走规则模板；
  配置 DeepSeek key 后，角色/两难/散文改由真实模型驱动，戏剧度与文笔显著提升。
- `mystery/irony/conflict` 落差由前端检查器据三账本客户端推导；后端提供原料接口。
