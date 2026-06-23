"""FastAPI 搴旂敤锛氭寜鍓嶇 HttpAdapter 濂戠害鏆撮湶 /api 璺敱锛堝惈 SSE锛夈€?

鍚姩锛歶vicorn server.app:app --reload --port 8000
锛堥渶鍏?`pip install -r server/requirements.txt` 涓斾粨搴撴牴鍦?PYTHONPATH锛屾垨鐢ㄦ彁渚涚殑 run 鑴氭湰銆傦級
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

# 鍏佽浠庝粨搴撴牴 import novel_engine锛坰rc 甯冨眬锛?
ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from fastapi import FastAPI, HTTPException, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import StreamingResponse  # noqa: E402

from . import config_store, seedbuilder  # noqa: E402
from .projects import ProjectManager, set_config_provider  # noqa: E402

app = FastAPI(title="灏忚妯℃嫙寮曟搸 API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 鍏堣缃厤缃鍙栧櫒锛屽啀鏋勯€?manager锛堝惎鍔ㄦ椂鎭㈠鐨勯」鐩嵁姝ょ敤鐪熷疄 LLM 閲嶅缓寮曟搸锛?
set_config_provider(config_store.load_config)
manager = ProjectManager()


def _project(project_id: str):
    try:
        return manager.get(project_id)
    except KeyError:
        raise HTTPException(404, f"未知项目：{project_id}")
        '''
        raise HTTPException(404, f"鏈煡椤圭洰锛歿project_id}")


# ---------------- 鍏ㄥ眬璁剧疆 ----------------
        '''
@app.get("/api/config")
async def get_config():
    return config_store.load_config()


@app.put("/api/config")
async def put_config(cfg: dict[str, Any]):
    config_store.save_config(cfg)
    return {"ok": True}


@app.post("/api/config/test")
async def test_config(cfg: dict[str, Any]):
    # 鐪熷疄鏍￠獙浜ょ粰鍚庣锛氭湁 key + base_url 鍗宠涓哄彲鐢紙鍙寜闇€鎵╁睍涓虹湡杩炰竴娆★級銆?
    return {"ok": bool(cfg.get("llmApiKey") and cfg.get("baseUrl"))}


# ---------------- 椤圭洰 ----------------
@app.get("/api/projects")
async def list_projects():
    return manager.list()


@app.post("/api/projects")
async def create_project(body: dict[str, Any]):
    p = manager.create(body.get("title", "") or "未命名小说",
                       body.get("type", "original"),
                       template_id=str(body.get("templateId", "") or "").strip())
    # 缁啓椤圭洰涓嶈蛋绉嶅瓙宸ュ潑锛氬垱寤哄嵆寮曞绌轰笘鐣屽簱锛屼娇瀵煎叆/鑽夌閾剧珛鍗冲彲鐢紝骞惰惤鐩樹互渚块噸鍚仮澶嶃€?
    if p.project_type == "continuation":
        await p.run(p.ensure_writing_repo)
        manager.persist()
    p.ensure_loop()  # 鍚庡彴寰幆甯搁┗锛坰eeding 鏃剁┖杞級
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


# ---------------- 绉嶅瓙宸ュ潑 ----------------
@app.get("/api/projects/{project_id}/seed/chat")
async def get_seed_chat(project_id: str):
    return _project(project_id).chat


@app.post("/api/projects/{project_id}/seed/chat")
async def post_seed_chat(project_id: str, body: dict[str, Any]):
    """以 SSE 流返回种子对话结果。"""
    p = _project(project_id)
    content = body.get("content", "")
    try:
        draft, reply = await p.run(p.advance_seed, content)
    except seedbuilder.SeedChatError as e:
        # 涓嶅仛绂荤嚎鍏滃簳锛氭湭閰嶇疆 / API 鏈搷搴?鈫?鐩存帴鎶ラ敊缁欏墠绔?
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


# ---------------- 杩愯鎬?----------------
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


@app.get("/api/projects/{project_id}/scene-anchors")
async def get_scene_anchors(project_id: str):
    p = _project(project_id)
    return await p.read_run(p.scene_anchors)


@app.post("/api/projects/{project_id}/scene-anchors")
async def save_scene_anchor(project_id: str, payload: dict):
    p = _project(project_id)
    res = await p.run(p.save_scene_anchor, payload)
    manager.persist()
    return res


@app.delete("/api/projects/{project_id}/scene-anchors/{scene_id}")
async def delete_scene_anchor(project_id: str, scene_id: str):
    p = _project(project_id)
    res = await p.run(p.delete_scene_anchor, scene_id)
    manager.persist()
    return res


@app.post("/api/projects/{project_id}/finalize")
async def finalize(project_id: str):
    """执行终稿整理（final cut）。"""
    p = _project(project_id)
    res = await p.run(p.finalize)
    manager.persist()
    return res


@app.get("/api/projects/{project_id}/plan")
async def get_plan(project_id: str):
    """返回项目规划层数据。"""
    p = _project(project_id)
    return await p.read_run(p.plan)


@app.get("/api/templates")
def list_templates():
    """返回题材模板列表。"""
    from novel_engine import templates as _tmpls
    return {"templates": _tmpls.list_cards()}


@app.post("/api/projects/{project_id}/tone")
async def update_tone(project_id: str, body: dict[str, Any]):
    """编辑或确认文风契约。"""
    p = _project(project_id)
    res = await p.run(p.update_tone, body.get("patch", {}) or {}, bool(body.get("confirm")))
    p.broadcast("delta", {"tone": "updated"})
    return res


@app.get("/api/projects/{project_id}/style-skill")
async def get_style_skill(project_id: str):
    """返回当前启用的文风模拟配置。"""
    p = _project(project_id)
    return await p.read_run(p.get_style_skill)


@app.post("/api/projects/{project_id}/style-skill")
async def ingest_style_skill(project_id: str, body: dict[str, Any]):
    """导入作品原文或现成文风配置。"""
    p = _project(project_id)
    res = await p.run(p.ingest_style_skill, body.get("mode", "works"),
                      body.get("text", "") or "", body.get("name", "") or "",
                      body.get("source", "") or "")
    if not res.get("ok"):
        raise HTTPException(400, "文风文本为空或提取失败")
    p.broadcast("delta", {"style": "ingested"})
    return res


@app.patch("/api/projects/{project_id}/style-skill")
async def toggle_style_skill(project_id: str, body: dict[str, Any]):
    """启用或停用文风模拟。"""
    p = _project(project_id)
    res = await p.run(p.set_style_enabled, bool(body.get("enabled", True)))
    p.broadcast("delta", {"style": "toggled"})
    return res


@app.delete("/api/projects/{project_id}/style-skill")
async def delete_style_skill(project_id: str):
    """删除文风模拟并回落到 tone profile。"""
    p = _project(project_id)
    res = await p.run(p.remove_style_skill)
    p.broadcast("delta", {"style": "deleted"})
    return res


# ---------------- S1 Author Writing Sheet ----------------
@app.post("/api/projects/{project_id}/style/distill")
async def distill_author_sheet(project_id: str, body: dict[str, Any]):
    """从作品原文蒸馏作者写作表。"""
    p = _project(project_id)
    res = await p.run(p.distill_author_sheet,
                      body.get("text", "") or "",
                      body.get("name", "") or "",
                      body.get("genre", "") or "")
    if not res.get("ok"):
        raise HTTPException(400, "文本为空或蒸馏失败")
    p.broadcast("delta", {"style": "aws_distilled"})
    return res


@app.get("/api/projects/{project_id}/style/sheets")
async def list_author_sheets(project_id: str):
    p = _project(project_id)
    return await p.read_run(p.list_author_sheets)


@app.get("/api/projects/{project_id}/style/sheets/{sheet_id}")
async def get_author_sheet(project_id: str, sheet_id: int):
    p = _project(project_id)
    res = await p.read_run(p.get_author_sheet, sheet_id)
    if res is None:
        raise HTTPException(404, "鏈壘鍒拌鏂囬鐢诲儚")
    return res


@app.delete("/api/projects/{project_id}/style/sheets/{sheet_id}")
async def delete_author_sheet(project_id: str, sheet_id: int):
    p = _project(project_id)
    res = await p.run(p.delete_author_sheet, sheet_id)
    return res




@app.post("/api/projects/{project_id}/continuation/import")
async def import_continuation_source(project_id: str, body: dict[str, Any]):
    p = _project(project_id)
    res = await p.run(p.import_source_text, body)
    manager.persist()
    return res


@app.get("/api/projects/{project_id}/continuation/source")
async def get_continuation_source(project_id: str):
    p = _project(project_id)
    return await p.read_run(p.continuation_source)


@app.post("/api/projects/{project_id}/continuation/distill")
async def start_continuation_distill(project_id: str, body: dict[str, Any]):
    p = _project(project_id)
    res = await p.run(p.start_continuation_distill, body)
    manager.persist()
    return res


@app.get("/api/projects/{project_id}/continuation/job")
async def get_continuation_job(project_id: str):
    p = _project(project_id)
    return await p.read_run(p.continuation_job_status)


@app.get("/api/projects/{project_id}/continuation/style-diagnostics")
async def get_continuation_style_diagnostics(project_id: str):
    p = _project(project_id)
    return await p.read_run(p.style_diagnostics)


@app.get("/api/projects/{project_id}/continuation/stream")
async def continuation_stream(project_id: str, request: Request):
    return await stream(project_id, request)


@app.get("/api/projects/{project_id}/continuation/settings")
async def get_continuation_settings(project_id: str):
    p = _project(project_id)
    return await p.read_run(p.get_continuation_settings)


@app.put("/api/projects/{project_id}/continuation/settings")
async def put_continuation_settings(project_id: str, body: dict[str, Any]):
    p = _project(project_id)
    res = await p.run(p.set_continuation_settings, body)
    manager.persist()
    return res


@app.post("/api/projects/{project_id}/continuation/lock")
async def lock_continuation(project_id: str):
    p = _project(project_id)
    res = await p.run(p.lock_continuation)
    manager.persist()
    return res


@app.get("/api/projects/{project_id}/characters/{agent_id}/dossier")
async def get_dossier(project_id: str, agent_id: str):
    p = _project(project_id)
    md = await p.read_run(p.dossier, agent_id)
    return {"agentId": agent_id, "markdown": md}


# ---------------- 鎺у埗 ----------------
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
        raise HTTPException(400, f"unknown control action: {action}")
    status = await p.read_run(p.story_bible_status)
    settings = await p.read_run(p.get_writing_settings)
    if (
        action == "play"
        and status.get("pendingDraftId")
        and settings.get("requireHumanAcceptance")
    ):
        p.playing = False
    payload = {
        "runningSim": p.playing,
        "pendingDraftId": status.get("pendingDraftId"),
        "pendingChapterNo": status.get("pendingChapterNo"),
    }
    p.broadcast("delta", payload)
    manager.persist()
    return {
        "ok": True,
        **payload,
        "autoPausedReason": (
            "pending_acceptance"
            if (not p.playing and status.get("pendingDraftId"))
            else None
        ),
    }

@app.post("/api/projects/{project_id}/god")
async def god(project_id: str, action: dict[str, Any]):
    p = _project(project_id)
    await p.run(p.god_action, action)
    p.broadcast("delta", {"god": action.get("kind")})
    return {"ok": True}


@app.patch("/api/projects/{project_id}/plan/chapter/{chapter_id}")
async def edit_chapter(project_id: str, chapter_id: str, fields: dict[str, Any]):
    """编辑未完成章节的大纲字段。"""
    p = _project(project_id)
    res = await p.run(p.edit_chapter, chapter_id, fields)
    if not res.get("ok") and res.get("error") == "written":
        raise HTTPException(409, "鏈珷宸插啓瀹岋紝涓嶈兘淇敼锛堝涓嶆弧鎰忓彲鍒犻櫎鍚庨噸鍐欙級")
    if not res.get("ok"):
        raise HTTPException(400, res.get("error", "缂栬緫澶辫触"))
    p.broadcast("delta", {"planEdit": chapter_id})
    return res


@app.post("/api/projects/{project_id}/plan/chapter/{chapter_id}/replan")
async def replan_chapter(project_id: str, chapter_id: str):
    """Rebuild one unwritten chapter using the current safe planning context."""
    p = _project(project_id)
    res = await p.run(p.replan_chapter, chapter_id)
    if not res.get("ok") and res.get("error") == "written":
        raise HTTPException(409, "本章已写完，不能重新规划")
    if not res.get("ok"):
        raise HTTPException(400, res.get("error", "重新规划失败"))
    p.broadcast("delta", {"planReplan": chapter_id})
    return res


@app.patch("/api/projects/{project_id}/plan/disclosures/{entity_id}")
async def update_disclosure(project_id: str, entity_id: str, fields: dict[str, Any]):
    p = _project(project_id)
    res = await p.run(p.update_disclosure, entity_id, fields)
    if not res.get("ok"):
        raise HTTPException(404, res.get("error", "披露日程实体不存在"))
    p.broadcast("delta", {"disclosureUpdate": entity_id})
    return res


@app.post("/api/projects/{project_id}/world-bible/{section}/deepen")
async def deepen_bible_section(project_id: str, section: str, body: dict[str, Any] | None = None):
    """渐进深化指定 Story Bible 小节。"""
    p = _project(project_id)
    b = body or {}
    res = await p.run(p.deepen_bible_section, section, b.get("context", ""), b.get("hint", ""))
    if res.get("ok"):
        p.broadcast("delta", {"bibleDeepen": section})
    return res


# ---------------- LLM 瀵硅瘽鏃ュ織 ----------------
@app.get("/api/projects/{project_id}/llm-logs")
async def get_llm_logs(project_id: str, limit: int = 200, caller: str | None = None):
    """返回项目的 LLM 调用日志。"""
    p = _project(project_id)
    return await p.read_run(lambda: p.repo.list_llm_logs(limit=limit, caller=caller))


@app.get("/api/projects/{project_id}/llm-logs/{log_id}")
async def get_llm_log(project_id: str, log_id: int):
    """返回单条 LLM 日志详情。"""
    p = _project(project_id)
    return await p.read_run(lambda: p.repo.get_llm_log(log_id))


@app.get("/api/projects/{project_id}/llm-logs-stats")
async def get_llm_log_stats(project_id: str):
    """返回 LLM 日志统计。"""
    p = _project(project_id)
    return await p.read_run(lambda: p.repo.llm_log_stats())


# ---------------- 鐭ヨ瘑鍥捐氨 ----------------
@app.get("/api/projects/{project_id}/graph")
async def get_graph(project_id: str):
    """返回知识图谱节点和边。"""
    p = _project(project_id)

    def _build():
        repo = p.repo
        nodes = []
        seen = set()
        name_of = {}

        # 鍦扮偣灞傜骇 parent 鏄犲皠
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

        # 鍔垮姏鑺傜偣锛堝惈 territory 鐢ㄤ簬鍓嶇瀹氫綅锛?
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
    """删除章节，使该位置可以重新生成。"""
    p = _project(project_id)
    res = await p.run(p.delete_chapter, chapter_id)
    p.broadcast("delta", {"planDelete": chapter_id})
    return res


# ---------------- 瀹炴椂 SSE ----------------
@app.get("/api/projects/{project_id}/stream")
async def stream(project_id: str, request: Request):
    p = _project(project_id)
    queue: asyncio.Queue = asyncio.Queue()
    p.subscribers.add(queue)

    async def gen():
        try:
            # 杩炴帴鍗虫彁绀轰竴娆★紝渚夸簬鍓嶇纭閫氶亾
            yield "event: delta\ndata: {\"connected\": true}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    kind, data = await asyncio.wait_for(queue.get(), timeout=15)
                    yield f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"  # 蹇冭烦锛岄槻浠ｇ悊鏂繛
        finally:
            p.subscribers.discard(queue)

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/api/projects/{project_id}/writing/settings")
async def get_writing_settings(project_id: str):
    p = _project(project_id)
    return await p.read_run(p.get_writing_settings)


@app.put("/api/projects/{project_id}/writing/settings")
async def put_writing_settings(project_id: str, body: dict[str, Any]):
    p = _project(project_id)
    return await p.run(p.put_writing_settings, body)


@app.post("/api/projects/{project_id}/story-bible/build")
async def build_story_bible(project_id: str):
    p = _project(project_id)
    return await p.run(p.build_story_bible)


@app.get("/api/projects/{project_id}/story-bible/status")
async def get_story_bible_status(project_id: str):
    p = _project(project_id)
    return await p.read_run(p.story_bible_status)


@app.get("/api/projects/{project_id}/story-bible")
async def get_story_bible(project_id: str):
    p = _project(project_id)
    return await p.read_run(p.get_story_bible)


@app.post("/api/projects/{project_id}/source/import-text")
async def import_source_text(project_id: str, body: dict[str, Any]):
    p = _project(project_id)
    res = await p.run(p.import_source_text, body)
    manager.persist()  # 鐘舵€佺炕鎴?writing 鍚庤惤鐩橈紝淇濊瘉閲嶅惎鍙仮澶?
    return res


@app.get("/api/projects/{project_id}/source/chapters")
async def get_source_chapters(project_id: str):
    p = _project(project_id)
    return await p.read_run(p.source_chapters)


@app.patch("/api/projects/{project_id}/source/chapters/{chapter_id}")
async def update_source_chapter(project_id: str, chapter_id: int, body: dict[str, Any]):
    p = _project(project_id)
    return await p.run(p.update_source_chapter, chapter_id, body)


@app.post("/api/projects/{project_id}/source/resplit")
async def resplit_source(project_id: str):
    p = _project(project_id)
    res = await p.run(p.resplit_source)
    manager.persist()
    return res


@app.post("/api/projects/{project_id}/chapters/drafts")
async def create_chapter_draft(project_id: str, body: dict[str, Any]):
    p = _project(project_id)
    res = await p.run(p.create_chapter_draft, body)
    if not res.get("ok", True) and res.get("error") == "continuation_not_locked":
        raise HTTPException(409, "璇峰厛鍦ㄧ画鍐欏伐鍧婇攣瀹氬啓浣滀笂涓嬫枃锛屽啀鐢熸垚绔犺妭")
    return res


@app.get("/api/projects/{project_id}/chapters/drafts")
async def list_chapter_drafts(project_id: str):
    p = _project(project_id)
    return await p.read_run(p.list_chapter_drafts)


@app.post("/api/projects/{project_id}/chapters/drafts/{draft_id}/accept")
async def accept_chapter_draft(project_id: str, draft_id: int):
    p = _project(project_id)
    try:
        return await p.run(p.accept_chapter_draft, draft_id)
    except ValueError as exc:
        if str(exc) == "draft_blocked_by_audit":
            raise HTTPException(409, "本章存在剧情越界或硬伤，请先按审计意见重写")
        raise


@app.post("/api/projects/{project_id}/chapters/drafts/{draft_id}/reject")
async def reject_chapter_draft(project_id: str, draft_id: int):
    p = _project(project_id)
    return await p.run(p.reject_chapter_draft, draft_id)


@app.post("/api/projects/{project_id}/chapters/drafts/{draft_id}/force-accept")
async def force_accept_chapter_draft(project_id: str, draft_id: int, body: dict[str, Any]):
    p = _project(project_id)
    try:
        return await p.run(
            p.force_accept_chapter_draft,
            draft_id,
            str(body.get("reason", "") or ""),
        )
    except ValueError as exc:
        if str(exc) == "force_accept_reason_required":
            raise HTTPException(422, "寮哄埗鎺ュ彈蹇呴』濉啓鍘熷洜")
        raise


@app.post("/api/projects/{project_id}/chapters/auto-write")
async def auto_write_chapters(project_id: str, body: dict[str, Any]):
    p = _project(project_id)
    res = await p.run(p.auto_write_chapters, body)
    if not res.get("ok", True) and res.get("error") == "continuation_not_locked":
        raise HTTPException(409, "璇峰厛鍦ㄧ画鍐欏伐鍧婇攣瀹氬啓浣滀笂涓嬫枃锛屽啀鑷姩杩炲啓")
    return res


@app.get("/api/projects/{project_id}/chapters/accepted")
async def get_accepted_chapters(project_id: str):
    p = _project(project_id)
    return await p.read_run(p.accepted_chapters)


@app.on_event("shutdown")
async def _shutdown():
    manager.dispose_all()
