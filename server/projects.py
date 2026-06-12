"""项目管理 + 模拟循环 + SSE 广播。

隔离模型：每个项目一套 Repository（独立 in-memory SQLite）+ 一个**单线程执行器**。
所有引擎/数据库操作都在该项目自己的线程里串行执行 → sqlite 线程安全，且项目间互不干扰。
后台 asyncio 任务驱动 Director 推进事件流，并把新事件经 SSE 广播给订阅者。
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from novel_engine import db
from novel_engine.agent import CharacterAgent
from novel_engine.config import LLMConfig
from novel_engine.consistency import InCharacterChecker
from novel_engine.dilemma import DilemmaGenerator
from novel_engine.director import Director
from novel_engine.llm import build_client
from novel_engine.llm.logging_wrapper import LoggingLLMClient
from novel_engine.llm.mock import MockClient
from novel_engine.memory import MemoryStore
from novel_engine.monitors import Monitors
from novel_engine.narration.editor import Editor
from novel_engine.narration.foreshadow import ForeshadowLedger
from novel_engine.planner import Planner
from novel_engine.propagation import Propagator
from novel_engine.repository import Repository
from novel_engine.tone import build_tone_profile
from novel_engine.validator import Validator
from novel_engine.worldsmith import WorldSmith

from . import config_store, dossier, schemas, seedbuilder

SIM_INTERVAL = 2.5  # 秒/拍

# 持久化目录：项目索引 + 每项目一个世界 DB 文件（重启可恢复）
DATA_DIR = Path(config_store.DATA_DIR)
PROJECTS_DIR = DATA_DIR / "projects"
INDEX_PATH = DATA_DIR / "projects.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


PEAK_TENSION = 0.66  # 高潮阈值（与 narration.tension.HIGH 对齐）
MIN_SCENES_PER_CHAPTER = 2  # 一章至少多少场，避免单场成章

_CN_DIGITS = "零一二三四五六七八九"


def _num_cn(n: int) -> str:
    if n <= 0:
        return "零"
    if n < 10:
        return _CN_DIGITS[n]
    if n < 20:
        return "十" + (_CN_DIGITS[n % 10] if n % 10 else "")
    if n < 100:
        return _CN_DIGITS[n // 10] + "十" + (_CN_DIGITS[n % 10] if n % 10 else "")
    return str(n)


def group_chapters(scene_rows: list[tuple[str, float]]) -> list[dict[str, Any]]:
    """按"高潮处断章"把场分组：累积到高张力峰值场（且≥最小章长）即收束成一章。

    scene_rows: [(sceneId, targetTension), ...]，按话语顺序。
    返回章列表（含进行中的最后一章）。
    """
    chapters: list[list[str]] = []
    cur: list[str] = []
    for sid, tension in scene_rows:
        cur.append(sid)
        if tension >= PEAK_TENSION and len(cur) >= MIN_SCENES_PER_CHAPTER:
            chapters.append(cur)
            cur = []
    out: list[dict[str, Any]] = []
    for i, ch in enumerate(chapters, 1):
        out.append({"index": i, "title": f"第{_num_cn(i)}章", "sceneIds": ch, "status": "done", "climaxSceneId": ch[-1]})
    if cur:  # 未到高潮的尾部 → 进行中
        i = len(chapters) + 1
        out.append({"index": i, "title": f"第{_num_cn(i)}章", "sceneIds": cur, "status": "ongoing", "climaxSceneId": None})
    return out


class Project:
    def __init__(self, title: str):
        self.id = f"proj_{uuid.uuid4().hex[:8]}"
        self.title = title or "未命名小说"
        self.status = "seeding"  # seeding | writing | completed
        self.created_at = _now()
        self.updated_at = _now()

        self.chat: list[dict[str, Any]] = [
            {"role": "assistant", "content": "我们一起来播下这部小说的种子吧。先聊聊你想要的世界、主题、主角与冲突——任何一点都行。", "at": _now()}
        ]
        self.draft: dict[str, Any] = seedbuilder.empty_draft()

        # 引擎组件（锁定后建立）
        self.repo: Repository | None = None
        self.director: Director | None = None
        self.planner: Planner | None = None
        self.worldsmith: WorldSmith | None = None
        self._llm = None
        self._gen_llm = None
        self._narr_llm = None
        self._theme = ""

        # 世界状态落盘路径（锁定后用文件 DB，重启可恢复）
        self.db_path = str(PROJECTS_DIR / f"{self.id}.db")

        # 写执行器：模拟循环 + 落库 + 渲染都在这一条线程串行（sqlite 写连接 self.repo）
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"eng-{self.id}")
        # 读执行器：API 的只读查询走这里 + 独立只读连接 self.read_repo，
        # 不再排在模拟循环的 LLM 拍后面 → 播放时切标签/加载不再卡。
        self.read_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"rd-{self.id}")
        self.read_repo: Repository | None = None
        self.playing = False
        self._task: asyncio.Task | None = None
        self.subscribers: set[asyncio.Queue] = set()
        self._rendered_at = -1  # 上次渲染时的事件数
        # 计数缓存（供 /api/projects 列表零 DB 访问；由写线程在每拍后刷新）
        self._scenes_n = 0
        self._chapters_n = 0

    # ---------- 通用：写调用丢进写线程 / 读调用丢进读线程 ----------
    async def run(self, fn: Callable, *args):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self.executor, lambda: fn(*args))

    async def read_run(self, fn: Callable, *args):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self.read_executor, lambda: fn(*args))

    def _rt(self) -> Repository | None:
        """读用仓储：优先独立只读连接，未建好则退回写连接。"""
        return self.read_repo or self.repo

    def _open_read_repo(self) -> None:
        if self.repo is not None and Path(self.db_path).exists():
            try:
                self.read_repo = Repository(db.connect(self.db_path, check_same_thread=False))
            except Exception:
                self.read_repo = None

    def meta(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "runningSim": self.playing,
            "sceneCount": self._scenes_n,
            "chapterCount": self._chapters_n,
        }

    def _touch(self):
        self.updated_at = _now()

    def _refresh_counts(self) -> None:
        """在写线程刷新计数缓存（meta/列表用，避免在请求线程上读 DB）。"""
        if not self.repo:
            return
        try:
            self._scenes_n = len(self.repo.list_scenes())
            if self.repo.list_parts():
                self._chapters_n = sum(1 for c in self.repo.list_chapter_plans() if c.status == "done")
            else:
                rows = [(s.scene_id, s.target_tension) for s in self.repo.list_scenes()]
                self._chapters_n = sum(1 for c in group_chapters(rows) if c["status"] == "done")
        except Exception:
            pass

    def _scene_count(self) -> int:
        if not self.repo:
            return 0
        try:
            return len(self.repo.list_scenes())
        except Exception:
            return 0

    def _chapter_count(self) -> int:
        """已完成章数（不强制渲染，只看已渲染的场）。"""
        if not self.repo:
            return 0
        try:
            if self.repo.list_parts():  # 计划模式：按章计划统计已完成章
                return sum(1 for c in self.repo.list_chapter_plans() if c.status == "done")
            rows = [(s.scene_id, s.target_tension) for s in self.repo.list_scenes()]
            return sum(1 for c in group_chapters(rows) if c["status"] == "done")
        except Exception:
            return 0

    def chapters(self) -> list[dict[str, Any]]:
        """章节分组。计划模式：按 chapter_plans（含章名/出场人物）归集场；否则沿用高潮断章。"""
        rt = self._rt()
        if not rt:
            return []
        chs = self._chapters_from_plans(rt) if rt.list_parts() else \
            group_chapters([(s.scene_id, s.target_tension) for s in rt.list_scenes()])
        # §5 final cut：杀青后用 in medias res——把全局最高潮场作为「序章·钩子」前置
        if self.status == "completed":
            scenes = rt.list_scenes()
            if scenes:
                peak = max(scenes, key=lambda s: s.target_tension)
                chs = [{
                    "index": 0, "title": "序章 · 钩子（in medias res）",
                    "sceneIds": [peak.scene_id], "status": "done",
                    "climaxSceneId": peak.scene_id, "isPrologue": True,
                }] + chs
        return chs

    def finalize(self) -> dict[str, Any]:
        """§5 杀青并定稿重排（final cut）：标记完结 + 触发 in medias res 阅读重排。"""
        rt = self._rt()
        if not rt or not rt.list_scenes():
            return {"ok": False, "reason": "尚无成稿场景，无法定稿"}
        self.status = "completed"
        self.playing = False
        self._touch()
        return {"ok": True, "status": self.status}

    def _chapters_from_plans(self, rt: Repository) -> list[dict[str, Any]]:
        """把已渲染的场按"其来源事件所属章号(beat_id)"归到对应 chapter_plan。"""
        # scene → chapter_id：取该场首个来源事件的 beat_id
        ev_chapter = {e.event_id: e.beat_id for e in rt.list_events()}
        scenes_by_ch: dict[str, list[tuple[int, str]]] = {}
        for s in rt.list_scenes():
            cid = next((ev_chapter.get(ev) for ev in s.source_events if ev_chapter.get(ev)), None)
            if cid:
                scenes_by_ch.setdefault(cid, []).append((s.discourse_order, s.scene_id))
        out: list[dict[str, Any]] = []
        plans = rt.list_chapter_plans()
        names = {p.agent_id: p.name for p in rt.list_personas()}
        idx = 0
        for plan in plans:
            sids = [sid for _, sid in sorted(scenes_by_ch.get(plan.chapter_id, []))]
            if not sids and plan.status != "done":
                continue  # 尚无成稿且未收束的未来章节不显示
            idx += 1
            cast_names = [names.get(a, a) for a in plan.cast]
            out.append({
                "index": idx,
                "title": plan.title or f"第{_num_cn(idx)}章",
                "sceneIds": sids,
                "status": "done" if plan.status == "done" else "ongoing",
                "climaxSceneId": sids[-1] if sids else None,
                "cast": cast_names,
                "beatGoals": plan.beat_goals,
            })
        return out

    # ---------- 种子工坊 ----------
    def advance_seed(self, user_message: str) -> tuple[dict[str, Any], str]:
        """真实 LLM 共创：抽取并更新结构化种子。失败直接抛错，不做离线兜底。"""
        self.chat.append({"role": "user", "content": user_message, "at": _now()})
        # cocreate 在无 LLM / API 不响应时抛 SeedChatError
        self.draft, reply = seedbuilder.cocreate(self.draft, self.chat, user_message, self._build_seed_llm())
        self.chat.append({"role": "assistant", "content": reply, "at": _now()})
        self._touch()
        return self.draft, reply

    def _build_seed_llm(self):
        cfg = _current_config()
        if cfg.get("llmApiKey"):
            try:
                raw = build_client(LLMConfig(provider="deepseek", model=cfg["modelName"], base_url=cfg["baseUrl"], api_key=cfg["llmApiKey"]))
                conn = self.repo.conn if self.repo else None
                if conn is not None:
                    return LoggingLLMClient(raw, conn, caller="seed")
                return raw
            except Exception:
                return None
        return None

    # ---------- 锁定并构建世界 ----------
    def lock_and_build(self) -> None:
        PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
        self.repo = seedbuilder.build_repo_from_draft(self.draft, db_path=self.db_path)
        self._theme = self.draft.get("worldBible", {}).get("theme", "")
        # 新机制：锁定时生成三层大纲（揭示链/Part/Arc+逐章计划）。这是新项目的脊柱，
        # 旧项目（重启恢复，无 parts）不会走这里 → 行为保持原样。
        self._make_llms()
        # §16.6 闸门⓪：先定文风契约（贯穿规划/表演/渲染三层），再生成总纲。
        # genre 取自草稿 worldBible.genre；缺省由 build_tone_profile 按题材预设/默认补全。
        wb = self.draft.get("worldBible", {}) or {}
        build_tone_profile(self.repo, llm=self._gen_llm,
                           genre=wb.get("genre", ""), theme=self._theme,
                           setting_hint=wb.get("settingCore", "")[:200])
        # P4a：先为种子角色建卡，再从世界圣经/主角欲望抽取被点名的缺席核心人物建卡，
        # 让这些人物能进入揭示链与选角（须在 build_master 之前，因揭示链依赖 personas）。
        dossier.ensure_cards_for_personas(self.repo)
        try:
            from novel_engine.casting import (
                cast_named_characters, infer_and_store_genders, lock_aliases,
                lock_motif_canon, lock_hidden_identities)
            cast_named_characters(
                self.repo,
                bible_text="\n".join(filter(None, [
                    wb.get("settingCore", ""), wb.get("geography", ""), wb.get("culture", "")])),
                want_text=wb.get("protagonistWant", ""),
                llm=self._gen_llm,
            )
            # 显式判定每个角色本人性别并存储（修叙述层性别错乱）
            infer_and_store_genders(self.repo, llm=self._gen_llm)
            # 固化随身道具的材质/刻字设定（修"同一支钢笔刻字前后矛盾"）
            lock_motif_canon(self.repo, llm=self._gen_llm)
            # B-Fix：先确定性抽取显式化名（主角对外用化名/真名只对己），再做反派隐藏身份
            lock_aliases(self.repo, wb.get("protagonistWant", ""),
                         wb.get("settingCore", ""), wb.get("theme", ""))
            # 问题5：固化隐藏身份并埋入揭示链（须在 build_master 之前，让身份 fact 进揭示链）
            lock_hidden_identities(self.repo, llm=self._gen_llm)
            # W1 World Skill：世界观分节全量化 + 两级(summary/detail) + 多级生成 + 校验回路
            # （取代旧 expand_world_bible），下游规划/写作都拿到厚而权威的设定。
            from novel_engine.worldbible import (build_factions, build_geography,
                                                  build_world_skill, lock_canonical_geography)
            build_world_skill(self.repo, llm=self._gen_llm, theme=wb.get("theme", ""))
            # W0 设定保真闸门：把世界圣经地理里的真实地点固化成 canon location 实体，
            # planner 只能从中选/细化、子地点须从属（治"发明遮面街/镜面塔"漂移）。须在 build_master 前。
            lock_canonical_geography(self.repo, llm=self._gen_llm)
            # W2 地理层：对 canon 地点做厚做权威（两级 summary/detail + 风土人情 + 层级 + 校验回路）
            build_geography(self.repo, llm=self._gen_llm, theme=wb.get("theme", ""))
            # W3 势力系统：据世界观+地理生成 3-5 个独特势力（一等实体）+ 关系图 + 核心成员落卡
            build_factions(self.repo, llm=self._gen_llm, theme=wb.get("theme", ""))
            # W4 分层人物卡：主角极详 + 主配加厚（三维度+小传+弧线+校验回路）
            from novel_engine.casting import enrich_character_cards
            enrich_character_cards(self.repo, llm=self._gen_llm, theme=wb.get("theme", ""))
            # W5 知识图谱·静态边：人物→势力 / 势力→地点 / 势力↔势力 / 地点→上级（纯本地，无 LLM）
            from novel_engine.worldbible import build_static_graph
            build_static_graph(self.repo)
        except Exception:
            pass
        planner = Planner(self.repo, llm=self._gen_llm, theme=self._theme,
                          worldsmith=WorldSmith(self.repo, llm=self._narr_llm, theme=self._theme))
        planner.build_master()       # 揭示链 + Part 划分 + 地点 + 库存（按题材由 LLM 自定 3-5 部）
        # 惰性大纲（治"一直在播种"）：只生成总体大纲(全 Arc 骨架) + 第一部章纲，几分钟即可开写；
        # 后续各部章纲在演到时由 director._activate_part_of → planner.ensure_part_chapters 懒生成。
        planner.build_lazy_outline()
        self._build_engine()
        dossier.ensure_cards_for_personas(self.repo)       # §1 种子角色批量建卡(lead/supporting)
        dossier.write_all(self.id, self.repo, chapter=0)  # ⑤ 人物档案初版（含选角卡身份）
        self._open_read_repo()
        self._refresh_counts()
        self.status = "writing"
        self.playing = True
        self._touch()

    def _make_llms(self) -> None:
        """根据配置装配 LLM（有 key 用 DeepSeek，否则 Mock + 关闭生成/叙述 LLM）。
        所有 LLM 调用经 LoggingLLMClient 包装，自动记录到 llm_logs 表。"""
        cfg = _current_config()
        key = cfg.get("llmApiKey")
        if key:
            raw = build_client(LLMConfig(provider="deepseek", model=cfg["modelName"], base_url=cfg["baseUrl"], api_key=key))
            conn = self.repo.conn if self.repo else None
            if conn is not None:
                real = LoggingLLMClient(raw, conn, caller="default")
            else:
                real = raw
            self._llm, self._gen_llm, self._narr_llm = real, real, real
        else:
            self._llm, self._gen_llm, self._narr_llm = MockClient(), None, None

    def _build_engine(self) -> None:
        """在 self.repo 之上装配 LLM / 记忆 / 世界生成器 / 导演（锁定与重启恢复共用）。"""
        assert self.repo is not None
        self._make_llms()
        repo = self.repo
        self.worldsmith = WorldSmith(repo, llm=self._narr_llm, theme=self._theme)
        # 计划模式：仅当世界已有 Part（=新机制项目）才挂 planner，旧项目不受影响。
        planned = bool(repo.list_parts())
        self.planner = (
            Planner(repo, llm=self._gen_llm, theme=self._theme, worldsmith=self.worldsmith)
            if planned else None
        )
        # B1 大纲驱动重构 scripted 开关（默认关；env NOVEL_ENGINE_MODE=scripted 手动 opt-in；
        # 新项目默认 scripted 留 B6 翻）。
        engine_mode = os.environ.get("NOVEL_ENGINE_MODE", "sim").strip().lower()
        engine_mode = "scripted" if (engine_mode == "scripted" and planned) else "sim"
        if engine_mode == "scripted":
            # B5 清理：scripted 纯走 SceneWriter+FactExtractor+Controller，
            # **不构造** dilemma/agent/consistency/memory（模拟核心）。
            from novel_engine.narration.controller import Controller
            from novel_engine.narration.fact_extractor import FactExtractor
            from novel_engine.narration.scene_writer import SceneWriter
            self.director = Director(
                repo, worldsmith=self.worldsmith, planner=self.planner,
                writer=SceneWriter(repo, llm=self._narr_llm),
                extractor=FactExtractor(repo, llm=self._gen_llm),
                controller=Controller(repo, llm=self._gen_llm),
                mode="scripted",
            )
        else:
            memory = MemoryStore(repo)
            consistency = InCharacterChecker(repo, llm=self._gen_llm)  # §3 出戏检测
            self.director = Director(
                repo,
                DilemmaGenerator(repo, llm=self._gen_llm, theme=self._theme),
                CharacterAgent(repo, self._llm, memory=memory, memory_k=6),
                Validator(repo),
                Monitors(repo, flaw_max_free=2),
                Propagator(repo, memory=memory),
                worldsmith=self.worldsmith,
                structural_every=0 if planned else 6,  # 计划模式不随机塞角色，按章计划登场
                planner=self.planner,
                consistency=consistency,
                mode="sim",
            )

    def reopen_from_disk(self) -> None:
        """重启后从已落盘的世界 DB 恢复引擎（暂停态，等用户按播放）。"""
        if self.status != "writing" or not Path(self.db_path).exists():
            return
        self.repo = Repository(db.connect(self.db_path, check_same_thread=False))
        self._theme = self.draft.get("worldBible", {}).get("theme", "")
        self._build_engine()
        self._open_read_repo()
        self._refresh_counts()
        # 默认暂停；若配置开启"恢复后自动继续播放"则继续
        self.playing = bool(_current_config().get("autoResume"))

    # ---------- 持久化用：可序列化快照 ----------
    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "chat": self.chat,
            "draft": self.draft,
        }

    @classmethod
    def from_snapshot(cls, snap: dict[str, Any]) -> "Project":
        p = cls(snap.get("title", "未命名小说"))
        p.id = snap["id"]
        p.status = snap.get("status", "seeding")
        p.created_at = snap.get("createdAt", _now())
        p.updated_at = snap.get("updatedAt", _now())
        p.chat = snap.get("chat", p.chat)
        p.draft = snap.get("draft", p.draft)
        p.db_path = str(PROJECTS_DIR / f"{p.id}.db")
        return p

    # ---------- 模拟一拍：返回新事件（已序列化） ----------
    def step_once(self) -> list[dict[str, Any]]:
        if not self.director or not self.repo:
            return []
        before = len(self.repo.list_events())
        step = self.director.step()
        # 计划模式：本章写满 → 立刻据本章内容取章名（在引擎线程内，可安全调 LLM）
        if self.planner is not None and getattr(step, "chapter_done", False) and step.chapter_id:
            try:
                self.planner.name_chapter(step.chapter_id)
                # ⑤ 刷新本章出场人物的档案（状态随情节演进）
                ch = self.repo.get_chapter_plan(step.chapter_id)
                if ch:
                    for aid in ch.cast:
                        dossier.write_dossier(self.id, self.repo, aid, chapter=ch.sequence_order)
            except Exception:
                pass
        # 渲染移到写线程（这里）：读路径不再触发 LLM。增量限批，无积压时近乎零成本。
        try:
            self._maybe_render()
        except Exception:
            pass
        self._refresh_counts()
        events = self.repo.list_events()
        new = events[before:]
        return [schemas.event_out(e, self.repo.get_event_drama_score(e.event_id)) for e in new]

    # ---------- 叙述渲染（增量+限批：每次只渲染少量新场，绝不重渲已有场） ----------
    RENDER_BATCH = 4  # 每次取场最多新渲染几场，保证「阅读」快速返回、内容逐步增长

    def _maybe_render(self):
        if not self.repo:
            return
        # B4 §6.21 修复：scripted 模式下场已由 SceneWriter 写好、Controller 内联把关，
        # **不跑 Editor**（其章级审计会用 narrator 基于薄事件重渲、覆盖 scripted 正文）。
        # 仍跑无 LLM 的伏笔回收。
        scripted = getattr(self.director, "mode", "sim") == "scripted"
        produced = True
        if not scripted:
            rendered_ids = {ev for s in self.repo.list_scenes() for ev in s.source_events}
            produced = bool(Editor(
                self.repo, llm=self._narr_llm, theme=self._theme,
                threshold=0.5, reveal_budget=1, max_rewrites=2,
            ).render_incremental(rendered_ids, max_new=self.RENDER_BATCH))
        if produced:
            # 读者读到的真相 → 命中的 must_resolve 伏笔回收（plant→payoff）
            ledger = ForeshadowLedger(self.repo)
            for rk in self.repo.list_reader_knowledge():
                ledger.pay_off_for_fact(rk.fact_id, rk.revealed_discourse_pos)

    # ---------- 上帝动作 ----------
    def god_action(self, action: dict[str, Any]) -> None:
        if not self.repo:
            return
        kind = action.get("kind")
        rt = self.repo
        if kind == "edit_fact":
            rt.conn.execute("UPDATE facts SET canonical_content=? WHERE fact_id=?", (action["newContent"], action["factId"]))
            rt.conn.commit()
        elif kind == "reveal_to_reader":
            f = rt.get_fact(action["factId"])
            if f and not rt.reader_knows(f.fact_id):
                from novel_engine.models import ReaderKnowledge

                rt.reveal_to_reader(ReaderKnowledge(f.fact_id, f.canonical_content, len(rt.list_scenes()), "god"))
        elif kind == "hide_from_reader":
            rt.conn.execute("DELETE FROM reader_knowledge WHERE fact_id=?", (action["factId"],))
            rt.conn.commit()
        elif kind == "set_thread_priority":
            rt.conn.execute("UPDATE threads SET priority_weight=? WHERE thread_id=?", (action["weight"], action["threadId"]))
            rt.conn.commit()
        elif kind == "add_entity":
            # 上帝手动新增角色/物品：交给 WorldSmith 落地（含 persona/账本/登场事件）
            if self.worldsmith is not None:
                tick = rt.list_events()[-1].story_time + 1 if rt.list_events() else 1
                etype = action.get("entityType", "character")
                name = action.get("name") or None
                if etype == "object":
                    self.worldsmith.introduce_object(tick, name=name)
                else:
                    self.worldsmith.introduce_character(tick, name=name)
        elif kind == "add_event":
            from novel_engine.models import Event

            p = action.get("payload", {})
            ev = Event(
                event_id=f"ev_{uuid.uuid4().hex[:8]}",
                story_time=rt.list_events()[-1].story_time + 1 if rt.list_events() else 1,
                actors=p.get("actors", []) or [],
                action_type=p.get("actionType", "神迹"),
                payload={"note": p.get("payload", "导演注入的事件。")},
                location_id=p.get("locationId"),
                perceivers=p.get("perceivers", []) or [],
            )
            rt.append_event(ev)
        self._touch()

    # ---------- 大纲编辑 / 删除（写操作，走写执行器） ----------
    def _make_planner(self):
        return Planner(self.repo, llm=self._gen_llm, theme=self._theme,
                       worldsmith=WorldSmith(self.repo, llm=self._narr_llm, theme=self._theme))

    def edit_chapter(self, chapter_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        """编辑某章大纲（级联建相关人物/道具）。**已写完的不能改**。"""
        if not self.repo:
            return {"ok": False, "error": "no_repo"}
        if self.repo.chapter_is_written(chapter_id):
            return {"ok": False, "error": "written"}  # 已写完不能改
        ch = self._make_planner().edit_chapter(
            chapter_id,
            title=fields.get("title"), dramatic_question=fields.get("dramaticQuestion"),
            beat_goals=fields.get("beatGoals"), cast_names=fields.get("castNames"),
            location_name=fields.get("locationName"), conflict_type=fields.get("conflictType"),
            exit_state=fields.get("exitState"), item_names=fields.get("itemNames"),
        )
        self._refresh_counts()
        self._touch()
        return {"ok": ch is not None}

    def delete_chapter(self, chapter_id: str) -> dict[str, Any]:
        """删除某章（含已写正文）。删后该 Arc 空出名额，续写时会重新生成一章（可再编辑）。"""
        if not self.repo:
            return {"ok": False, "error": "no_repo"}
        res = self.repo.delete_chapter_cascade(chapter_id)
        self._refresh_counts()
        self._touch()
        return {"ok": True, **res}

    # ---------- 读取（序列化；全部走只读连接 self._rt()，不触发渲染、不与模拟循环抢线程） ----------
    def world(self) -> dict[str, Any]:
        rt = self._rt()
        if not rt:
            return {"facts": [], "events": [], "tick": 0}
        events = rt.list_events()
        return {
            "facts": [schemas.fact_out(f) for f in rt.list_facts()],
            "events": [schemas.event_out(e, rt.get_event_drama_score(e.event_id)) for e in events],
            "tick": max((e.story_time for e in events), default=0),
        }

    def plan(self) -> dict[str, Any]:
        """规划层快照：大纲树（Part→Arc→章计划）+ 揭示链进度 + 库存。前端「大纲」页用。"""
        rt = self._rt()
        if not rt or not rt.list_parts():
            return {"planned": False, "parts": [], "arcs": [], "chapters": [],
                    "revealChain": [], "inventory": []}
        names = {p.agent_id: p.name for p in rt.list_personas()}
        ent_names = {e.entity_id: e.name for e in rt.list_entities()}
        tp = rt.get_tone_profile()
        # §11 provisional 派生：章所属 Part 尚未 active/done（还没演到）→ 该章为预演稿，演到时复核。
        part_status = {p.part_id: p.status for p in rt.list_parts()}
        arc_part = {a.arc_id: a.part_id for a in rt.list_arcs()}
        # 预计算"已写正文"的章 id 集合（O(场)一次，避免逐章扫场）：供前端"已写完不能改"闸门
        written_ids: set[str] = set()
        for s in rt.list_scenes():
            for eid in s.source_events:
                ev = rt.get_event(eid)
                if ev and ev.beat_id:
                    written_ids.add(ev.beat_id)
                    break

        def _chapter_dict(c):
            d = schemas.chapter_plan_out(c, names)
            d["provisional"] = part_status.get(arc_part.get(c.arc_id), "planned") == "planned"
            d["conflictType"] = c.conflict_type
            d["exitState"] = c.exit_state
            d["written"] = (c.status == "done" or bool(c.audited) or c.chapter_id in written_ids)
            d["itemsPresentNames"] = [ent_names.get(o, o) for o in c.items_present]
            d["locationName"] = ent_names.get(c.location_ids[0], "") if c.location_ids else ""
            d["beatPovNames"] = [names.get(a, ent_names.get(a, a)) for a in (c.beat_povs or [])]
            return d

        return {
            "planned": True,
            "toneProfile": schemas.tone_profile_out(tp) if tp.is_set() else None,
            "styleSkill": (schemas.style_skill_out(rt.get_style_skill())
                           if rt.get_style_skill().is_set() else None),
            "parts": [schemas.part_out(p) for p in rt.list_parts()],
            "arcs": [schemas.arc_out(a) for a in rt.list_arcs()],
            "chapters": [_chapter_dict(c) for c in rt.list_chapter_plans()],
            "revealChain": [schemas.reveal_node_out(n) for n in rt.list_reveal_nodes()],
            "inventory": [schemas.inventory_out(i, ent_names.get(i.object_id, "")) for i in rt.list_inventory()],
            "locations": [schemas.location_out(l) for l in rt.list_locations()],
            "factions": [schemas.faction_out(f) for f in rt.list_factions()],
            "characterCards": [schemas.character_card_out(c) for c in rt.list_cards()],
            "bibleSections": rt.list_bible_sections(),
        }

    def deepen_bible_section(self, section: str, context: str = "",
                             hint: str = "") -> dict[str, Any]:
        """W1-b 手动渐进深化：对指定世界观节追加 w1_deepened 子详述，可多次。"""
        from novel_engine.worldbible import deepen_section
        rt = self.repo
        if rt is None:
            return {"ok": False, "error": "no_repo"}
        llm = self._gen_llm()
        if llm is None:
            return {"ok": False, "error": "no_llm"}
        ok = deepen_section(rt, llm=llm, section=section,
                            context=context or f"用户手动深化「{section}」节",
                            hint=hint)
        return {"ok": ok, "section": section}

    def update_tone(self, patch: dict[str, Any], confirm: bool = False) -> dict[str, Any]:
        """§16 编辑/确认文风契约（写路径）。确认后只读，再写无效。"""
        from novel_engine.models import ToneProfile

        rt = self.repo
        if rt is None:
            return {}
        cur = rt.get_tone_profile()
        if not cur.confirmed:
            g = lambda k, d: patch.get(k, d)  # noqa: E731
            rt.set_tone_profile(ToneProfile(
                genre=g("genre", cur.genre), primary_effect=g("primaryEffect", cur.primary_effect),
                register=g("register", cur.register), sentence_rhythm=g("sentenceRhythm", cur.sentence_rhythm),
                diction_do=g("dictionDo", cur.diction_do), diction_dont=g("dictionDont", cur.diction_dont),
                device_kit=g("deviceKit", cur.device_kit), pacing=g("pacing", cur.pacing),
                tension_curve_bias=g("tensionCurveBias", cur.tension_curve_bias),
                reveal_cadence=g("revealCadence", cur.reveal_cadence),
                complexity=g("complexity", cur.complexity),
                tone_reference=g("toneReference", cur.tone_reference), confirmed=False,
                era_logic=g("eraLogic", cur.era_logic),
            ))
            if confirm:
                rt.confirm_tone_profile()
        return schemas.tone_profile_out(rt.get_tone_profile())

    # ---------------- B0 文风模拟（style_skill） ----------------
    def get_style_skill(self) -> dict[str, Any] | None:
        rt = self._rt()
        if rt is None:
            return None
        sk = rt.get_style_skill()
        return schemas.style_skill_out(sk) if sk.is_set() else None

    def ingest_style_skill(self, mode: str, text: str, name: str = "",
                           source: str = "") -> dict[str, Any]:
        """摄取文风（写路径，调 LLM 蒸馏）。mode=works→上传作品原文；mode=skill→现成 SKILL.md。"""
        from novel_engine import style_skill as ss

        rt = self.repo
        if rt is None or not (text or "").strip():
            return {"ok": False, "error": "empty"}
        if mode == "skill":
            prof = ss.parse_style_skill(self._gen_llm, text)
        else:
            prof = ss.distill_from_works(self._gen_llm, text, name=name, source=source)
        rt.set_style_skill(prof)
        return {"ok": True, "styleSkill": schemas.style_skill_out(prof)}

    def set_style_enabled(self, enabled: bool) -> dict[str, Any]:
        if self.repo is not None:
            self.repo.set_style_skill_enabled(bool(enabled))
        return {"ok": True, "styleSkill": self.get_style_skill()}

    def remove_style_skill(self) -> dict[str, Any]:
        if self.repo is not None:
            self.repo.delete_style_skill()
        return {"ok": True}

    def dossier(self, agent_id: str) -> str:
        """某角色的 .md 档案（缺文件则按 DB 现状即时构建）。"""
        rt = self._rt()
        if not rt:
            return ""
        return dossier.read_dossier(self.id, rt, agent_id)

    def beats(self):
        rt = self._rt()
        return [schemas.beat_out(b) for b in rt.list_beats()] if rt else []

    def threads(self):
        rt = self._rt()
        return [schemas.thread_out(t) for t in rt.list_threads()] if rt else []

    def endings(self):
        rt = self._rt()
        return [schemas.ending_out(e) for e in rt.list_endings()] if rt else []

    def personas(self):
        rt = self._rt()
        return [schemas.persona_out(p) for p in rt.list_personas()] if rt else []

    def knowledge(self, agent_id: str):
        rt = self._rt()
        return [schemas.knowledge_out(k) for k in rt.get_agent_ledger(agent_id)] if rt else []

    def reader(self, upto: int | None):
        rt = self._rt()
        if not rt:
            return []
        return [
            schemas.reader_out(r)
            for r in rt.list_reader_knowledge()
            if upto is None or r.revealed_discourse_pos <= upto
        ]

    def foreshadows(self):
        rt = self._rt()
        return [schemas.foreshadow_out(f) for f in rt.list_foreshadows()] if rt else []

    def scenes(self):
        rt = self._rt()
        return [schemas.scene_out(s) for s in rt.list_scenes()] if rt else []

    # ---------- SSE 广播 ----------
    def broadcast(self, kind: str, data: Any):
        msg = (kind, data)
        for q in list(self.subscribers):
            try:
                q.put_nowait(msg)
            except Exception:
                pass

    async def sim_loop(self):
        """后台循环：playing 时持续推进。订阅与否都不影响其运行。"""
        while True:
            try:
                if self.playing and self.status == "writing":
                    new_events = await self.run(self.step_once)
                    for e in new_events:
                        self.broadcast("sim", e)
                    if new_events:
                        self.broadcast("delta", {"tick": new_events[-1]["storyTime"]})
                        self._touch()
                    await asyncio.sleep(SIM_INTERVAL)
                else:
                    await asyncio.sleep(0.5)
            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(SIM_INTERVAL)

    def ensure_loop(self):
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.sim_loop())

    def dispose(self):
        if self._task:
            self._task.cancel()
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.read_executor.shutdown(wait=False, cancel_futures=True)
        if self.read_repo is not None:
            try:
                self.read_repo.conn.close()
            except Exception:
                pass


# —— 全局配置访问（避免循环导入，运行时读取） ——
_config_provider: Callable[[], dict[str, Any]] = lambda: {}


def set_config_provider(fn: Callable[[], dict[str, Any]]):
    global _config_provider
    _config_provider = fn


def _current_config() -> dict[str, Any]:
    try:
        return _config_provider() or {}
    except Exception:
        return {}


class ProjectManager:
    def __init__(self):
        self.projects: dict[str, Project] = {}
        self._load_from_disk()

    # ---------- 持久化 ----------
    def _load_from_disk(self) -> None:
        if not INDEX_PATH.exists():
            return
        try:
            snaps = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
        except Exception:
            return
        for snap in snaps:
            try:
                p = Project.from_snapshot(snap)
            except Exception:
                continue
            # 先无条件登记，避免"引擎重建失败"导致该项目从索引消失（产生孤儿 DB）
            self.projects[p.id] = p
            try:
                p.reopen_from_disk()  # writing 项目从落盘 DB 恢复引擎（暂停态）
            except Exception:
                pass

    def persist(self) -> None:
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            INDEX_PATH.write_text(
                json.dumps([p.snapshot() for p in self.projects.values()], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    def list(self) -> list[dict[str, Any]]:
        return sorted((p.meta() for p in self.projects.values()), key=lambda m: m["updatedAt"], reverse=True)

    def create(self, title: str) -> Project:
        p = Project(title)
        self.projects[p.id] = p
        self.persist()
        return p

    def get(self, project_id: str) -> Project:
        p = self.projects.get(project_id)
        if not p:
            raise KeyError(project_id)
        return p

    def rename(self, project_id: str, title: str):
        p = self.get(project_id)
        p.title = title
        p._touch()
        self.persist()

    def delete(self, project_id: str):
        p = self.projects.pop(project_id, None)
        if p:
            p.dispose()
            try:
                Path(p.db_path).unlink(missing_ok=True)
            except Exception:
                pass
            try:  # 清理人物档案目录 projects/<id>/
                import shutil

                shutil.rmtree(PROJECTS_DIR / p.id, ignore_errors=True)
            except Exception:
                pass
        self.persist()

    def dispose_all(self):
        for p in self.projects.values():
            p.dispose()
