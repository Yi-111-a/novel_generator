"""FastAPI 应用：按前端 HttpAdapter 契约暴露 /api 路由（含 SSE）。

启动：uvicorn server.app:app --reload --port 8000
（需先 `pip install -r server/requirements.txt` 且仓库根在 PYTHONPATH，或用提供的 run 脚本。）
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

# 允许从仓库根 import novel_engine（src 布局）
ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from fastapi import FastAPI, HTTPException, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import StreamingResponse  # noqa: E402

from . import config_store, seedbuilder  # noqa: E402
from .projects import ProjectManager, set_config_provider  # noqa: E402

app = FastAPI(title="小说模拟引擎 API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 先设置配置读取器，再构造 manager（启动时恢复的项目据此用真实 LLM 重建引擎）
set_config_provider(config_store.load_config)
manager = ProjectManager()


def _project(project_id: str):
    try:
        return manager.get(project_id)
    except KeyError:
        raise HTTPException(404, f"未知项目：{project_id}")


# ---------------- 全局设置 ----------------
@app.get("/api/config")
async def get_config():
    return config_store.load_config()


@app.put("/api/config")
async def put_config(cfg: dict[str, Any]):
    config_store.save_config(cfg)
    return {"ok": True}


@app.post("/api/config/test")
async def test_config(cfg: dict[str, Any]):
    # 真实校验交给后端：有 key + base_url 即视为可用（可按需扩展为真连一次）。
    return {"ok": bool(cfg.get("llmApiKey") and cfg.get("baseUrl"))}


# ---------------- 项目 ----------------
@app.get("/api/projects")
async def list_projects():
    return manager.list()


@app.post("/api/projects")
async def create_project(body: dict[str, Any]):
    p = manager.create(body.get("title", "未命名小说"))
    p.ensure_loop()  # 后台循环常驻（seeding 时空转）
    return p.meta()


@app.patch("/api/projects/{project_id}")
async def rename_project(project_id: str, body: dict[str, Any]):
    _project(project_id)
    manager.rename(project_id, body.get("title", ""))
    return {"ok": True}


@app.delete("/api/projects/{project_id}")
async def delete_project(project_id: str):
    _project(project_id)
    manager.delete(project_id)
    return {"ok": True}


# ---------------- 种子工坊 ----------------
@app.get("/api/projects/{project_id}/seed/chat")
async def get_seed_chat(project_id: str):
    return _project(project_id).chat


@app.post("/api/projects/{project_id}/seed/chat")
async def post_seed_chat(project_id: str, body: dict[str, Any]):
    """SSE 流：先逐字吐回复 token，最后一条带更新后的 draft。"""
    p = _project(project_id)
    content = body.get("content", "")
    try:
        draft, reply = await p.run(p.advance_seed, content)
    except seedbuilder.SeedChatError as e:
        # 不做离线兜底：未配置 / API 未响应 → 直接报错给前端
        raise HTTPException(400, str(e))
    manager.persist()

    async def gen():
        for ch in reply:
            yield f"data: {json.dumps({'token': ch}, ensure_ascii=False)}\n\n"
            await asyncio.sleep(0.008)
        yield f"data: {json.dumps({'draft': draft}, ensure_ascii=False)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/api/projects/{project_id}/seed/draft")
async def get_seed_draft(project_id: str):
    return _project(project_id).draft


@app.put("/api/projects/{project_id}/seed/draft")
async def put_seed_draft(project_id: str, body: dict[str, Any]):
    from . import seedbuilder

    p = _project(project_id)
    body["completeness"] = seedbuilder.completeness(body)
    p.draft = body
    p._touch()
    manager.persist()
    return {"ok": True}


@app.post("/api/projects/{project_id}/seed/lock")
async def lock_seed(project_id: str):
    p = _project(project_id)
    await p.run(p.lock_and_build)
    p.ensure_loop()
    manager.persist()
    return {"ok": True}


# ---------------- 运行态 ----------------
@app.get("/api/projects/{project_id}/world")
async def get_world(project_id: str):
    p = _project(project_id)
    return await p.read_run(p.world)


@app.get("/api/projects/{project_id}/beats")
async def get_beats(project_id: str):
    p = _project(project_id)
    return await p.read_run(p.beats)


@app.get("/api/projects/{project_id}/threads")
async def get_threads(project_id: str):
    p = _project(project_id)
    return await p.read_run(p.threads)


@app.get("/api/projects/{project_id}/endings")
async def get_endings(project_id: str):
    p = _project(project_id)
    return await p.read_run(p.endings)


@app.get("/api/projects/{project_id}/personas")
async def get_personas(project_id: str):
    p = _project(project_id)
    return await p.read_run(p.personas)


@app.get("/api/projects/{project_id}/knowledge/{agent_id}")
async def get_knowledge(project_id: str, agent_id: str):
    p = _project(project_id)
    return await p.read_run(p.knowledge, agent_id)


@app.get("/api/projects/{project_id}/reader-knowledge")
async def get_reader_knowledge(project_id: str, upto: int | None = None):
    p = _project(project_id)
    return await p.read_run(p.reader, upto)


@app.get("/api/projects/{project_id}/foreshadows")
async def get_foreshadows(project_id: str):
    p = _project(project_id)
    return await p.read_run(p.foreshadows)


@app.get("/api/projects/{project_id}/scenes")
async def get_scenes(project_id: str):
    p = _project(project_id)
    return await p.read_run(p.scenes)


@app.get("/api/projects/{project_id}/chapters")
async def get_chapters(project_id: str):
    p = _project(project_id)
    return await p.read_run(p.chapters)


@app.post("/api/projects/{project_id}/finalize")
async def finalize(project_id: str):
    """§5 杀青并定稿重排（final cut）。"""
    p = _project(project_id)
    res = await p.run(p.finalize)
    manager.persist()
    return res


@app.get("/api/projects/{project_id}/plan")
async def get_plan(project_id: str):
    """规划层：大纲树 + 揭示链进度 + 库存（计划模式项目才有内容）。"""
    p = _project(project_id)
    return await p.read_run(p.plan)


@app.post("/api/projects/{project_id}/tone")
async def update_tone(project_id: str, body: dict[str, Any]):
    """§16 编辑/确认文风契约。body: {patch:{…}, confirm:bool}。"""
    p = _project(project_id)
    res = await p.run(p.update_tone, body.get("patch", {}) or {}, bool(body.get("confirm")))
    p.broadcast("delta", {"tone": "updated"})
    return res


@app.get("/api/projects/{project_id}/style-skill")
async def get_style_skill(project_id: str):
    """B0 文风模拟：当前启用的文风指纹（无则 null，回落 tone 基线）。"""
    p = _project(project_id)
    return await p.read_run(p.get_style_skill)


@app.post("/api/projects/{project_id}/style-skill")
async def ingest_style_skill(project_id: str, body: dict[str, Any]):
    """上传作品原文(mode=works)或现成文风(mode=skill)→蒸馏出文风指纹。body:{mode,text,name?,source?}。"""
    p = _project(project_id)
    res = await p.run(p.ingest_style_skill, body.get("mode", "works"),
                      body.get("text", "") or "", body.get("name", "") or "",
                      body.get("source", "") or "")
    if not res.get("ok"):
        raise HTTPException(400, "文风文本为空或摄取失败")
    p.broadcast("delta", {"style": "ingested"})
    return res


@app.patch("/api/projects/{project_id}/style-skill")
async def toggle_style_skill(project_id: str, body: dict[str, Any]):
    """启用/停用文风模拟（停用即回落 tone 基线，但保留指纹可再启用）。"""
    p = _project(project_id)
    res = await p.run(p.set_style_enabled, bool(body.get("enabled", True)))
    p.broadcast("delta", {"style": "toggled"})
    return res


@app.delete("/api/projects/{project_id}/style-skill")
async def delete_style_skill(project_id: str):
    """删除文风模拟 → 回落 tone_profile 基线。"""
    p = _project(project_id)
    res = await p.run(p.remove_style_skill)
    p.broadcast("delta", {"style": "deleted"})
    return res


@app.get("/api/projects/{project_id}/characters/{agent_id}/dossier")
async def get_dossier(project_id: str, agent_id: str):
    p = _project(project_id)
    md = await p.read_run(p.dossier, agent_id)
    return {"agentId": agent_id, "markdown": md}


# ---------------- 控制 ----------------
@app.post("/api/projects/{project_id}/control")
async def control(project_id: str, body: dict[str, Any]):
    p = _project(project_id)
    action = body.get("action")
    if action == "play":
        p.playing = True
        p.ensure_loop()
    elif action == "pause":
        p.playing = False
    elif action == "step":
        new_events = await p.run(p.step_once)
        for e in new_events:
            p.broadcast("sim", e)
        if new_events:
            p.broadcast("delta", {"tick": new_events[-1]["storyTime"]})
    else:
        raise HTTPException(400, f"未知控制动作：{action}")
    return {"ok": True}


@app.post("/api/projects/{project_id}/god")
async def god(project_id: str, action: dict[str, Any]):
    p = _project(project_id)
    await p.run(p.god_action, action)
    p.broadcast("delta", {"god": action.get("kind")})
    return {"ok": True}


@app.patch("/api/projects/{project_id}/plan/chapter/{chapter_id}")
async def edit_chapter(project_id: str, chapter_id: str, fields: dict[str, Any]):
    """编辑某章大纲（级联建相关人物/道具）。已写完的章返回 409（不可改）。"""
    p = _project(project_id)
    res = await p.run(p.edit_chapter, chapter_id, fields)
    if not res.get("ok") and res.get("error") == "written":
        raise HTTPException(409, "本章已写完，不能修改（如不满意可删除后重写）")
    if not res.get("ok"):
        raise HTTPException(400, res.get("error", "编辑失败"))
    p.broadcast("delta", {"planEdit": chapter_id})
    return res


@app.post("/api/projects/{project_id}/world-bible/{section}/deepen")
async def deepen_bible_section(project_id: str, section: str, body: dict[str, Any] | None = None):
    """W1-b 手动渐进深化：对指定世界观节追加 w1_deepened 子详述，可多次，可带 hint。"""
    p = _project(project_id)
    b = body or {}
    res = await p.run(p.deepen_bible_section, section, b.get("context", ""), b.get("hint", ""))
    if res.get("ok"):
        p.broadcast("delta", {"bibleDeepen": section})
    return res


# ---------------- LLM 对话日志 ----------------
@app.get("/api/projects/{project_id}/llm-logs")
async def get_llm_logs(project_id: str, limit: int = 200, caller: str | None = None):
    """返回该项目的 LLM 调用日志列表（最新在前）。"""
    p = _project(project_id)
    return await p.read_run(lambda: p.repo.list_llm_logs(limit=limit, caller=caller))


@app.get("/api/projects/{project_id}/llm-logs/{log_id}")
async def get_llm_log(project_id: str, log_id: int):
    """返回单条 LLM 日志详情（含完整 system/user/response）。"""
    p = _project(project_id)
    return await p.read_run(lambda: p.repo.get_llm_log(log_id))


@app.get("/api/projects/{project_id}/llm-logs-stats")
async def get_llm_log_stats(project_id: str):
    """LLM 日志统计：各 caller 的调用次数和总耗时。"""
    p = _project(project_id)
    return await p.read_run(lambda: p.repo.llm_log_stats())


# ---------------- 知识图谱 ----------------
@app.get("/api/projects/{project_id}/graph")
async def get_graph(project_id: str):
    """返回知识图谱的 nodes + edges 供前端可视化。"""
    p = _project(project_id)

    def _build():
        repo = p.repo
        nodes = []
        seen = set()
        name_of = {}

        # 地点层级 parent 映射
        loc_parent: dict[str, str] = {}
        loc_fn = getattr(repo, "list_locations", None)
        if loc_fn:
            for loc in loc_fn():
                if loc.parent:
                    loc_parent[loc.loc_id] = loc.parent

        for e in repo.list_entities():
            name_of[e.entity_id] = e.name
            node: dict = {"id": e.entity_id, "name": e.name, "type": e.type,
                          "attributes": e.attributes or {}}
            if e.type == "character":
                fid = (e.attributes or {}).get("faction_id", "")
                if fid:
                    node["factionId"] = fid
            elif e.type == "location":
                p_loc = loc_parent.get(e.entity_id, "")
                if p_loc:
                    node["parentLoc"] = p_loc
            nodes.append(node)
            seen.add(e.entity_id)

        # 势力节点（含 territory 用于前端定位）
        fac_fn = getattr(repo, "list_factions", None)
        if fac_fn:
            for f in fac_fn():
                name_of[f.faction_id] = f.name
                if f.faction_id not in seen:
                    nodes.append({"id": f.faction_id, "name": f.name, "type": "faction",
                                  "attributes": {"ideology": f.ideology, "goals": f.goals},
                                  "territory": f.territory or []})
                    seen.add(f.faction_id)
                else:
                    for n in nodes:
                        if n["id"] == f.faction_id:
                            n["territory"] = f.territory or []
                            n.setdefault("attributes", {}).update(
                                {"ideology": f.ideology, "goals": f.goals})
                            break

        edges_raw = repo.list_edges()
        edges = []
        for e in edges_raw:
            edge: dict = {
                "src": e.src, "dst": e.dst, "rel": e.rel,
                "intensity": e.intensity,
                "sinceChapter": e.since_chapter,
                "lastActiveChapter": e.last_active_chapter,
                "srcName": name_of.get(e.src, e.src),
                "dstName": name_of.get(e.dst, e.dst),
            }
            if e.meta and e.meta.get("note"):
                edge["note"] = e.meta["note"]
            edges.append(edge)
        return {"nodes": nodes, "edges": edges}

    return await p.read_run(_build)


@app.delete("/api/projects/{project_id}/plan/chapter/{chapter_id}")
async def delete_chapter(project_id: str, chapter_id: str):
    """删除某章（含已写正文）。删后该位置续写时会重新生成、可再编辑。"""
    p = _project(project_id)
    res = await p.run(p.delete_chapter, chapter_id)
    p.broadcast("delta", {"planDelete": chapter_id})
    return res


# ---------------- 实时 SSE ----------------
@app.get("/api/projects/{project_id}/stream")
async def stream(project_id: str, request: Request):
    p = _project(project_id)
    queue: asyncio.Queue = asyncio.Queue()
    p.subscribers.add(queue)

    async def gen():
        try:
            # 连接即提示一次，便于前端确认通道
            yield "event: delta\ndata: {\"connected\": true}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    kind, data = await asyncio.wait_for(queue.get(), timeout=15)
                    yield f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"  # 心跳，防代理断连
        finally:
            p.subscribers.discard(queue)

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.on_event("shutdown")
async def _shutdown():
    manager.dispose_all()
