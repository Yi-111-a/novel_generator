"""项目管理 + 章节草稿循环 + SSE 广播。

隔离模型：每个项目一套 Repository（独立 in-memory SQLite）+ 一个**单线程执行器**。
所有引擎/数据库操作都在该项目自己的线程里串行执行 → sqlite 线程安全，且项目间互不干扰。
后台 asyncio 任务驱动 story_bible 草稿管线，并把新章节产生的事件经 SSE 广播给订阅者。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from novel_engine import db
from novel_engine.config import LLMConfig
from novel_engine.llm import build_client
from novel_engine.llm.logging_wrapper import LoggingLLMClient
from novel_engine.llm.mock import MockClient
from novel_engine.models import ContinuationJobRecord, SceneAnchor
from novel_engine.planner import Planner
from novel_engine.repository import Repository
from novel_engine.tone import build_tone_profile
from novel_engine.worldsmith import WorldSmith
from novel_engine.continuation import (
    build_continuation_snapshot,
    extract_unified_blocks,
    get_knowledge_package,
    import_into_repo,
    import_uploaded_into_repo,
    normalize_write_mode,
    reduce_unified_distillation,
    resolve_chapter_start_no,
    review_and_augment,
    synthesize_knowledge_package,
)

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
CONTINUATION_PHASE_STEPS: list[tuple[str, str]] = [
    ("B1", "导入·清洗·分章"),
    ("B2", "章节整块·统一抽取"),
    ("B3", "程序归并·状态演算"),
    ("B4", "全局小说知识包"),
]


def _style_diagnostics_payload(summary: dict[str, Any]) -> dict[str, Any]:
    return dict(summary or {})


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


def _normalize_distill_config(body: dict[str, Any] | None) -> dict[str, Any]:
    raw = body or {}
    return {
        "targetChunkChars": max(10000, min(120000, int(raw.get("targetChunkChars", 40000) or 40000))),
        "maxChaptersPerChunk": max(1, min(60, int(raw.get("maxChaptersPerChunk", 25) or 25))),
        "distillWorkers": max(1, min(12, int(raw.get("distillWorkers", 4) or 4))),
        "globalInputMaxChars": max(
            200000,
            min(1500000, int(raw.get("globalInputMaxChars", 1200000) or 1200000)),
        ),
    }


def _build_continuation_steps(current_code: str, *, done: bool) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen_current = False
    for code, label in CONTINUATION_PHASE_STEPS:
        if done:
            status = "done"
        elif code == current_code:
            status = "running"
            seen_current = True
        elif not seen_current:
            status = "done"
        else:
            status = "pending"
        out.append({"code": code, "label": label, "status": status})
    return out


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
    def __init__(self, title: str, project_type: str = "original"):
        self.id = f"proj_{uuid.uuid4().hex[:8]}"
        self.project_type = project_type or "original"
        self.title = title or "未命名小说"
        self.status = "seeding"  # seeding | writing | completed
        self.analysis_status = "idle"
        self.created_at = _now()
        self.updated_at = _now()

        self.chat: list[dict[str, Any]] = [
            {"role": "assistant", "content": "我们一起来播下这部小说的种子吧。先聊聊你想要的世界、主题、主角与冲突——任何一点都行。", "at": _now()}
        ]
        self.draft: dict[str, Any] = seedbuilder.empty_draft()

        # 写作组件（锁定后建立）
        self.repo: Repository | None = None
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
        """读用仓储：优先独立只读连接，未建好则退回写连接；都没有时若 db 文件存在则懒加载。"""
        if self.read_repo or self.repo:
            return self.read_repo or self.repo
        # 后端重启后第一次访问：脚本/上次会话写过 db 文件 → 懒加载，避免 plan() 返空。
        try:
            if Path(self.db_path).exists():
                self.repo = Repository(db.connect(self.db_path, check_same_thread=False))
                return self.repo
        except Exception:
            pass
        return None

    def _open_read_repo(self) -> None:
        if self.repo is not None and Path(self.db_path).exists():
            try:
                self.read_repo = Repository(db.connect(self.db_path, check_same_thread=False))
            except Exception:
                self.read_repo = None

    def meta(self) -> dict[str, Any]:
        continuation_ready = False
        continuation_phase = ""
        if self.repo is not None:
            try:
                cmeta = self.repo.get_continuation_meta()
                continuation_ready = cmeta.continuation_ready
                continuation_phase = cmeta.continuation_phase
            except Exception:
                pass
        return {
            "id": self.id,
            "title": self.title,
            "type": self.project_type,
            "status": self.status,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "runningSim": self.playing,
            "sceneCount": self._scenes_n,
            "chapterCount": self._chapters_n,
            "continuationReady": continuation_ready,
            "continuationPhase": continuation_phase,
        }

    def _touch(self):
        self.updated_at = _now()

    def _refresh_counts(self) -> None:
        """在写线程刷新计数缓存（meta/列表用，避免在请求线程上读 DB）。"""
        if not self.repo:
            return
        try:
            self._scenes_n = len(self.repo.list_scenes())
            accepted = self.repo.list_accepted_chapters()
            if accepted:
                self._chapters_n = len(accepted)
                return
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
            accepted = self.repo.list_accepted_chapters()
            if accepted:
                return len(accepted)
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
        accepted = rt.list_accepted_chapters()
        if accepted:
            scenes = rt.list_scenes()
            out: list[dict[str, Any]] = []
            for idx, ch in enumerate(accepted):
                scene = scenes[idx] if idx < len(scenes) else None
                out.append({
                    "index": ch.chapter_no,
                    "title": ch.title or f"第{ch.chapter_no}章",
                    "sceneIds": [scene.scene_id] if scene else [],
                    "status": "done",
                    "climaxSceneId": scene.scene_id if scene else None,
                    "cast": [],
                })
            return out
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
        names = {p.agent_id: rt.get_character_display_name(p.agent_id, p.name) for p in rt.list_personas()}
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
        self._open_or_seed_build_repo()
        assert self.repo is not None
        self._theme = self.draft.get("worldBible", {}).get("theme", "")
        self._make_llms()
        wb = self.draft.get("worldBible", {}) or {}
        template_id = str(self.draft.get("templateId", "")).strip()

        self._run_build_stage(
            "T0_tone_profile",
            lambda: build_tone_profile(
                self.repo, llm=self._gen_llm,
                genre=wb.get("genre", ""), theme=self._theme,
                setting_hint=wb.get("settingCore", "")[:200],
                template_id=template_id,
            ),
            artifact_done=lambda: bool((self.repo.get_tone_profile().genre or "").strip()),
        )
        self._run_build_stage(
            "C1_seed_character_cards",
            lambda: dossier.ensure_cards_for_personas(self.repo),
            artifact_done=lambda: len(self.repo.list_cards()) >= max(1, len(self.repo.list_personas())),
        )

        def _cast_and_lock() -> None:
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
            lock_hidden_identities(self.repo, llm=self._gen_llm)

        self._run_build_stage(
            "C2_cast_and_locks",
            _cast_and_lock,
            artifact_done=lambda: len(self.repo.list_personas()) > len(wb.get("personas", []) or self.draft.get("personas", []) or []),
            optional=True,
        )

        from novel_engine.worldbible import (build_factions, build_geography,
                                              build_world_skill, lock_canonical_geography,
                                              seed_factions_from_world_bible)
        self._run_build_stage(
            "W1_world_skill",
            lambda: build_world_skill(self.repo, llm=self._gen_llm, theme=wb.get("theme", "")),
            artifact_done=lambda: any(r.get("source") == "w1" for r in self.repo.list_bible_sections()),
            optional=True,
        )
        self._run_build_stage(
            "W2_geography",
            lambda: (lock_canonical_geography(self.repo, llm=self._gen_llm),
                     build_geography(self.repo, llm=self._gen_llm, theme=wb.get("theme", ""))),
            artifact_done=lambda: len(self.repo.list_locations()) > 0,
            optional=True,
        )
        self._run_build_stage(
            "W3_factions",
            lambda: (build_factions(self.repo, llm=self._gen_llm, theme=wb.get("theme", "")),
                     seed_factions_from_world_bible(self.repo, wb)),
            artifact_done=lambda: len(self.repo.list_factions()) > 0,
            optional=True,
        )
        self._run_build_stage(
            "C3_promote_faction_personas",
            self._promote_faction_personas,
            artifact_done=lambda: len(self.repo.list_factions()) == 0
                                  or len(self.repo.list_personas()) > len(self.draft.get("personas", []) or []),
            optional=True,
        )

        def _enrich_cards() -> None:
            from novel_engine.casting import enrich_character_cards
            enrich_character_cards(self.repo, llm=self._gen_llm, theme=wb.get("theme", ""))

        self._run_build_stage(
            "W4_character_cards",
            _enrich_cards,
            artifact_done=lambda: len(self.repo.list_cards()) >= max(1, len(self.repo.list_personas())),
            optional=True,
        )
        self._run_build_stage(
            "W5_static_graph",
            self._build_static_graph_stage,
            artifact_done=lambda: len(self.repo.list_edges()) > 0,
            optional=True,
        )
        try:
            seed_factions_from_world_bible(self.repo, wb)
        except Exception:
            pass

        planner = Planner(self.repo, llm=self._gen_llm, theme=self._theme,
                          worldsmith=WorldSmith(self.repo, llm=self._narr_llm, theme=self._theme),
                          template_id=template_id)
        self._run_build_stage(
            "P1_master_volumes",
            planner.build_master,
            artifact_done=lambda: len(self.repo.list_parts()) > 0,
        )
        self._run_build_stage(
            "P2_lazy_outline",
            planner.build_lazy_outline,
            artifact_done=lambda: len(self.repo.list_arcs()) > 0 and len(self.repo.list_chapter_plans()) > 0,
        )
        self._run_build_stage("S1_story_bible", self._build_original_story_bible, optional=True)
        self._build_engine()
        self._run_build_stage("E1_dossier", self._finalize_build_dossier, optional=True)
        self._open_read_repo()
        self._refresh_counts()
        self.status = "writing"
        # 锁定只表示世界与规划已经就绪，不应暗中触发正文生成。
        # 正文必须由用户显式点击 play/step/起稿接口后才开始；对要求人工采纳
        # 或 Agent 手写正文的项目，这也避免 lock 返回后 ChapterWriter 抢跑。
        self.playing = False
        self._touch()

    def _open_or_seed_build_repo(self) -> None:
        db_path = Path(self.db_path)
        if db_path.exists():
            candidate = Repository(db.connect(self.db_path, check_same_thread=False))
            if self._repo_has_build_state(candidate):
                self.repo = candidate
            else:
                candidate.conn.close()
                self.repo = seedbuilder.build_repo_from_draft(self.draft, db_path=self.db_path)
        else:
            self.repo = seedbuilder.build_repo_from_draft(self.draft, db_path=self.db_path)
        self.repo.set_project_meta(project_type=self.project_type, project_status="writing", analysis_status="ready")
        self.repo.set_writing_settings(self.repo.get_writing_settings())
        self.repo.mark_build_checkpoint("W0_seed_repo", "done", meta=self._build_stage_meta())

    def _repo_has_build_state(self, repo: Repository) -> bool:
        try:
            if repo.get_build_checkpoints():
                return True
            wb = repo.get_world_bible()
            return bool(
                (getattr(wb, "setting", "") or "").strip()
                or repo.list_personas()
                or repo.list_bible_sections()
                or repo.list_parts()
            )
        except Exception:
            return False

    def _run_build_stage(self, stage: str, fn: Callable, *,
                         artifact_done: Callable[[], bool] | None = None,
                         optional: bool = False):
        assert self.repo is not None
        if self.repo.build_checkpoint_status(stage) == "done":
            return None
        if artifact_done is not None:
            try:
                if artifact_done():
                    self.repo.mark_build_checkpoint(stage, "done", meta={**self._build_stage_meta(), "detectedExisting": True})
                    return None
            except Exception:
                pass
        self.repo.mark_build_checkpoint(stage, "running", meta=self._build_stage_meta())
        try:
            result = fn()
            self.repo.mark_build_checkpoint(stage, "done", meta=self._build_stage_meta())
            return result
        except Exception as exc:
            self.repo.mark_build_checkpoint(stage, "failed", error=str(exc), meta=self._build_stage_meta())
            try:
                self.repo.add_bible_section("diagnostics", f"构建阶段失败：{stage}", str(exc), source="system")
            except Exception:
                pass
            if not optional:
                raise
            return None

    def _build_stage_meta(self) -> dict[str, Any]:
        if not self.repo:
            return {}
        def _safe(fn, default=0):
            try:
                return fn()
            except Exception:
                return default
        return {
            "llmLogs": _safe(lambda: self.repo.conn.execute("SELECT count(*) FROM llm_logs").fetchone()[0]),
            "personas": _safe(lambda: len(self.repo.list_personas())),
            "cards": _safe(lambda: len(self.repo.list_cards())),
            "factions": _safe(lambda: len(self.repo.list_factions())),
            "locations": _safe(lambda: len(self.repo.list_locations())),
            "parts": _safe(lambda: len(self.repo.list_parts())),
            "arcs": _safe(lambda: len(self.repo.list_arcs())),
            "chapterPlans": _safe(lambda: len(self.repo.list_chapter_plans())),
        }

    def _promote_faction_personas(self) -> None:
        from novel_engine.casting import promote_faction_members_to_personas
        promote_faction_members_to_personas(self.repo)

    def _build_static_graph_stage(self) -> None:
        from novel_engine.worldbible import build_static_graph
        build_static_graph(self.repo)

    def _build_original_story_bible(self) -> None:
        try:
            from novel_engine.story_bible import StoryBibleBuilder
            StoryBibleBuilder(self.repo).build_for_original(
                title=self.title,
                theme=self._theme,
                source_text=json.dumps(self.draft, ensure_ascii=False),
            )
        except Exception:
            raise

    def _finalize_build_dossier(self) -> None:
        dossier.ensure_cards_for_personas(self.repo)
        try:
            from novel_engine.story_contract import apply_story_contract_card_overrides, load_story_contract
            apply_story_contract_card_overrides(self.repo, load_story_contract(self.repo))
        except Exception:
            pass
        dossier.write_all(self.id, self.repo, chapter=0)

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
        """在 self.repo 之上装配新章节链所需的 LLM。"""
        assert self.repo is not None
        self._make_llms()

    def reopen_from_disk(self) -> None:
        """重启后从已落盘的世界 DB 恢复引擎（暂停态，等用户按播放）。"""
        if self.status != "writing" or not Path(self.db_path).exists():
            return
        self.repo = Repository(db.connect(self.db_path, check_same_thread=False))
        meta = self.repo.get_project_meta()
        self.project_type = meta.get("project_type", self.project_type)
        self.analysis_status = meta.get("analysis_status", self.analysis_status)
        self._theme = self.draft.get("worldBible", {}).get("theme", "")
        self._build_engine()
        self._open_read_repo()
        self._refresh_counts()
        # 默认暂停；若配置开启"恢复后自动继续播放"则继续
        self.playing = bool(_current_config().get("autoResume"))

    # ---------- 新章节链：不走种子工坊的空世界库引导 ----------
    def ensure_writing_repo(self) -> None:
        """续写/直写项目不经种子工坊锁定，在此懒初始化一个空世界 DB + LLM，
        使 story_bible / 草稿链可用。已有 repo 时是无操作。必须在写线程调用。"""
        if self.repo is not None:
            return
        PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
        self.repo = Repository(db.connect(self.db_path, check_same_thread=False))
        self.repo.set_project_meta(
            project_type=self.project_type,
            project_status="writing",
            analysis_status=self.analysis_status,
        )
        # 落一份默认写设置（target_words 等），供草稿链读取
        self.repo.set_writing_settings(self.repo.get_writing_settings())
        self._theme = ""
        self._make_llms()
        self.status = "writing"
        self._open_read_repo()
        self._refresh_counts()
        self._touch()

    def _ensure_repo_for_new_chain(self) -> bool:
        """新章节链写方法的统一前置：repo 缺失时，仅对 continuation 自动引导。
        返回 False 表示当前项目尚不该走新链（如原创项目仍在种子期）。"""
        if self.repo is not None:
            return True
        if self.project_type == "continuation":
            self.ensure_writing_repo()
            return self.repo is not None
        return False

    # ---------- 持久化用：可序列化快照 ----------
    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "type": self.project_type,
            "status": self.status,
            "analysisStatus": self.analysis_status,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "chat": self.chat,
            "draft": self.draft,
        }

    @classmethod
    def from_snapshot(cls, snap: dict[str, Any]) -> "Project":
        p = cls(snap.get("title", "") or "未命名小说", snap.get("type", "original"))
        p.id = snap["id"]
        p.status = snap.get("status", "seeding")
        p.analysis_status = snap.get("analysisStatus", "idle")
        p.created_at = snap.get("createdAt", _now())
        p.updated_at = snap.get("updatedAt", _now())
        p.chat = snap.get("chat", p.chat)
        p.draft = snap.get("draft", p.draft)
        p.db_path = str(PROJECTS_DIR / f"{p.id}.db")
        return p

    # ---------- 模拟一拍：返回新事件（已序列化） ----------
    def step_once(self) -> list[dict[str, Any]]:
        if not self.repo:
            return []
        return self._step_chapter_pipeline_once()

    def _step_chapter_pipeline_once(self) -> list[dict[str, Any]]:
        from novel_engine.story_bible import DraftManager

        settings = self.repo.get_writing_settings()
        pending = self.repo.list_chapter_drafts(status="pending_acceptance")
        if settings.require_human_acceptance and pending:
            self.playing = False
            return []

        before = len(self.repo.list_events())
        manager = DraftManager(self.repo, self._gen_llm, project_id=self.id)
        draft = manager.generate(
            guidance="",
            target_words=settings.target_words,
            mode="auto",
        )
        if settings.require_human_acceptance:
            self.playing = False
            self._refresh_counts()
            self._touch()
            return []
        accepted = manager.accept(draft.id)
        self._refresh_counts()
        self._touch()
        events = self.repo.list_events()
        new = events[before:]
        if not new:
            return [{
                "eventId": f"chapter_{accepted.chapter_no}",
                "storyTime": len(events),
                "actors": [],
                "actionType": "chapter_accepted",
                "payload": accepted.summary,
                "locationId": None,
                "perceivers": [],
                "dramaScore": None,
                "beatId": "",
            }]
        return [schemas.event_out(e, self.repo.get_event_drama_score(e.event_id)) for e in new]

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
                       worldsmith=WorldSmith(self.repo, llm=self._narr_llm, theme=self._theme),
                       template_id=str(self.draft.get("templateId", "")).strip())

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

    def replan_chapter(self, chapter_id: str) -> dict[str, Any]:
        if not self.repo:
            return {"ok": False, "error": "no_repo"}
        current = self.repo.get_chapter_plan(chapter_id)
        if current is None:
            return {"ok": False, "error": "not_found"}
        if current.status == "done" or current.audited:
            return {"ok": False, "error": "written"}
        chapter = self._make_planner().replan_chapter(chapter_id)
        if chapter is None:
            return {"ok": False, "error": "unsupported_plan"}
        self._refresh_counts()
        self._touch()
        return {"ok": True}

    def update_disclosure(self, entity_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        if not self.repo:
            return {"ok": False, "error": "no_repo"}
        from novel_engine.disclosure import (
            get_disclosure_schedule,
            set_disclosure_schedule,
        )
        from novel_engine.models import Foreshadow

        ok = set_disclosure_schedule(
            self.repo,
            entity_id,
            foreshadow_from=fields.get("foreshadowFrom"),
            reveal_chapter=fields.get("revealChapter"),
            secret_reveal_chapter=fields.get("secretRevealChapter"),
            foreshadow_hint=fields.get("foreshadowHint"),
            secret_truth=fields.get("secretTruth"),
        )
        if not ok:
            return {"ok": False, "error": "not_found"}
        schedule = get_disclosure_schedule(self.repo, entity_id)
        if (
            schedule.foreshadow_hint
            and schedule.reveal_chapter > schedule.foreshadow_from
        ):
            target = next(
                (
                    chapter
                    for chapter in self.repo.list_chapter_plans()
                    if chapter.sequence_order == schedule.foreshadow_from
                ),
                None,
            )
            if target is not None and entity_id not in target.allowed_entity_ids:
                target.allowed_entity_ids.append(entity_id)
                self.repo.upsert_chapter_plan(target)
            self.repo.upsert_foreshadow(Foreshadow(
                foreshadow_id=f"disclosure:{entity_id}",
                question=schedule.foreshadow_hint,
                linked_fact_id=entity_id,
                planted_discourse_pos=schedule.foreshadow_from,
                target_payoff_beat=f"chapter:{schedule.reveal_chapter}",
                status="open",
            ))
        self._touch()
        return {"ok": True}

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
        """规划层快照：大纲树（Part→Arc→章计划）+ 揭示链进度 + 库存。前端「大纲」页用。

        续写项目没有 Parts，但有 C2 蒸馏出的角色/地点/势力/章计划/伏笔，必须一起返回，
        否则前端「世界配置」「大纲」面板看不到蒸馏成果。
        """
        rt = self._rt()
        if not rt:
            return {"planned": False, "parts": [], "arcs": [], "chapters": [],
                    "revealChain": [], "inventory": []}
        # 续写项目：即使有 Parts 也要补返回蒸馏数据；没有 Parts 也要返回（无规划态）
        parts_list = rt.list_parts()
        if self.project_type == "continuation" and (not parts_list or any(p.part_id.startswith("cont_part_") for p in parts_list)):
            ent_names = {e.entity_id: e.name for e in rt.list_entities()}
            persona_names = {p.agent_id: rt.get_character_display_name(p.agent_id, p.name)
                             for p in rt.list_personas()}
            chapters = rt.list_chapter_plans()
            knowledge = get_knowledge_package(rt).get("package", {})
            locations = [schemas.location_out(l) for l in rt.list_locations()]
            if not locations:
                locations = [
                    {
                        "locId": str(item.get("id", "")),
                        "partId": None,
                        "name": str(item.get("name", "")),
                        "geoFull": str(item.get("detail") or item.get("summary") or ""),
                        "connectsTo": [],
                        "controllingFaction": "",
                        "notableItems": [],
                        "level": str(item.get("level") or "其他"),
                        "parent": str(item.get("parent") or ""),
                        "cultureLocal": str(item.get("culture") or ""),
                        "summary": str(item.get("summary") or item.get("description") or ""),
                        "detail": str(item.get("detail") or item.get("description") or ""),
                    }
                    for item in (knowledge.get("locations") or [])
                    if isinstance(item, dict) and str(item.get("name", "")).strip()
                ]
            factions = [schemas.faction_out(f) for f in rt.list_factions()]
            if not factions:
                factions = [
                    {
                        "factionId": str(item.get("id", "")),
                        "name": str(item.get("name", "")),
                        "ideology": str(item.get("ideology") or ""),
                        "goals": str(item.get("goals") or ""),
                        "methods": str(item.get("methods") or ""),
                        "territory": list(item.get("territory") or []),
                        "structure": str(item.get("structure") or ""),
                        "keyMembers": list(item.get("key_members") or []),
                        "history": str(item.get("history") or ""),
                        "relations": list(item.get("relations") or []),
                        "secret": str(item.get("secret") or ""),
                        "summary": str(item.get("summary") or item.get("description") or ""),
                        "detail": str(item.get("detail") or item.get("description") or ""),
                        "source": "unified_distillation",
                    }
                    for item in (knowledge.get("factions") or [])
                    if isinstance(item, dict) and str(item.get("name", "")).strip()
                ]
            character_cards = [
                schemas.character_card_out(
                    c,
                    rt.get_character_display_name(c.agent_id or "", c.name) if c.agent_id else c.name,
                )
                for c in rt.list_cards()
            ]
            if not character_cards:
                character_cards = [
                    {
                        "cardId": str(item.get("id", "")),
                        "agentId": str(item.get("id", "")),
                        "tier": str(item.get("tier") or item.get("role_tier") or "supporting"),
                        "slotKey": None,
                        "name": str(item.get("name", "")),
                        "displayName": str(item.get("name", "")),
                        "oneLiner": str(item.get("summary") or item.get("one_liner") or ""),
                        "voiceRegister": str(item.get("voice") or ""),
                        "definingTrait": str(item.get("defining_trait") or item.get("stable_trait") or ""),
                        "coreDesire": str(item.get("want") or item.get("core_desire") or ""),
                        "verbalHabits": str(item.get("verbal_habits") or ""),
                        "keyRelation": str(item.get("key_relation") or ""),
                        "backstory": str(item.get("backstory") or ""),
                        "fatalFlaw": str(item.get("fatal_flaw") or ""),
                        "arc": str(item.get("arc") or item.get("growth_axis") or ""),
                        "appearance": str(item.get("appearance") or ""),
                        "socialRole": str(item.get("social_role") or ""),
                        "psychology": str(item.get("psychology") or ""),
                        "finalState": item.get("final_state") or {},
                    }
                    for item in (knowledge.get("characters") or [])
                    if isinstance(item, dict) and str(item.get("name", "")).strip()
                ]
            bible_sections = rt.list_bible_sections()
            if not bible_sections:
                assertions = (knowledge.get("world_setting") or {}).get("assertions") or []
                grouped: dict[str, list[str]] = {}
                for item in assertions:
                    if not isinstance(item, dict) or not str(item.get("claim", "")).strip():
                        continue
                    grouped.setdefault(str(item.get("category") or "settingCore"), []).append(
                        f"第{int(item.get('chapter', 0) or 0)}章：{str(item.get('claim', '')).strip()}"
                    )
                bible_sections = [
                    {
                        "id": -(index + 1),
                        "section": section,
                        "title": section,
                        "body_full": "\n".join(claims),
                        "summary": "；".join(claims[:3]),
                        "source": "unified_distillation",
                        "created_at": 0,
                    }
                    for index, (section, claims) in enumerate(grouped.items())
                ]
            return {
                    "planned": True,
                    "continuation": True,
                    "parts": [
                        {"partId": p.part_id, "sequenceOrder": p.sequence_order,
                         "title": p.title, "goal": p.goal, "region": p.region, "status": p.status}
                        for p in parts_list
                    ],
                    "arcs": [
                        {"arcId": a.arc_id, "partId": a.part_id, "sequenceOrder": a.sequence_order,
                         "title": a.title, "summary": a.summary, "targetChapters": a.target_chapters,
                         "focusAgents": [{"agentId": f["agent_id"], "weight": f["weight"],
                                          "name": persona_names.get(f["agent_id"], "")}
                                         for f in (a.focus_agents or [])],
                         "status": a.status}
                        for a in rt.list_arcs()
                    ],
                    "storyArcsOriginal": [{"arc_id": a["arc_id"], "name": a["name"], "theme": a["theme"],
                                            "journey": a["journey_summary"], "resolution": a["resolution_status"]}
                                           for a in rt.list_story_arcs()],
                    "chapters": [{
                        "chapterId": c.chapter_id, "arcId": c.arc_id,
                        "sequenceOrder": c.sequence_order,
                        "title": c.title, "summary": c.summary,
                        "dramaticQuestion": c.dramatic_question, "exitState": c.exit_state,
                        "beatGoals": c.beat_goals, "role": c.role,
                        "threadDecisions": c.thread_decisions_json,
                        "locationName": ent_names.get(c.location_ids[0], "") if c.location_ids else "",
                        "povName": persona_names.get(c.pov_agent or "", ent_names.get(c.pov_agent or "", "")),
                        "castNames": [persona_names.get(a, ent_names.get(a, a)) for a in c.cast],
                        "status": c.status, "written": c.status == "done" or bool(c.audited),
                        "provisional": False, "targetWords": c.target_words,
                    } for c in chapters],
                    "revealChain": [],
                    "inventory": [],
                    "locations": locations,
                    "factions": factions,
                    "characterCards": character_cards,
                    "bibleSections": bible_sections,
                    "foreshadows": rt.list_foreshadows(),
                    "openThreads": (
                        [{"id": t.thread_id, "question": t.central_question,
                          "status": t.status, "tension": t.current_tension}
                         for t in rt.list_threads()]
                        or [
                            {
                                "id": str(item.get("thread_id", "")),
                                "question": str(item.get("question", "")),
                                "status": str(item.get("status", "open")),
                                "tension": float(item.get("confidence", 0.0) or 0.0),
                            }
                            for item in (knowledge.get("plot_threads") or [])
                            if isinstance(item, dict)
                        ]
                    ),
                    "sourceEvents": rt.list_source_events(),
                    "codex": rt.list_codex(),
                    "storyArcs": rt.list_story_arcs(),
                }
        if not parts_list:
            return {"planned": False, "parts": [], "arcs": [], "chapters": [],
                    "revealChain": [], "inventory": []}
        names = {p.agent_id: rt.get_character_display_name(p.agent_id, p.name) for p in rt.list_personas()}
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
            d["threadDecisions"] = c.thread_decisions_json
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
            "characterCards": [
                schemas.character_card_out(
                    c,
                    rt.get_character_display_name(c.agent_id or "", c.name) if c.agent_id else c.name,
                )
                for c in rt.list_cards()
            ],
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

    # ---- S1 Author Writing Sheet ----
    def distill_author_sheet(self, text: str, name: str = "",
                             genre: str = "") -> dict[str, Any]:
        from novel_engine.style.sheet_distiller import distill_author_sheet
        from novel_engine.style.author_sheet import derive_style_profile

        rt = self.repo
        if rt is None or not (text or "").strip():
            return {"ok": False, "error": "empty"}
        sheet = distill_author_sheet(self._gen_llm, text, name=name, genre=genre)
        sheet_id = rt.save_author_sheet(sheet)
        prof = derive_style_profile(sheet, text[:4000], self._gen_llm)
        rt.set_style_skill(prof)
        return {"ok": True, "sheetId": sheet_id, "styleSkill": schemas.style_skill_out(prof),
                "sheet": schemas.author_sheet_out(sheet)}

    def get_author_sheet(self, sheet_id: int) -> dict[str, Any] | None:
        rt = self.repo
        if rt is None:
            return None
        sheet = rt.get_author_sheet(sheet_id)
        return schemas.author_sheet_out(sheet) if sheet else None

    def list_author_sheets(self) -> list[dict]:
        rt = self.repo
        return rt.list_author_sheets() if rt else []

    def delete_author_sheet(self, sheet_id: int) -> dict[str, Any]:
        if self.repo is not None:
            self.repo.delete_author_sheet(sheet_id)
        return {"ok": True}

    # ---- 续写 ----
    def get_writing_settings(self) -> dict[str, Any]:
        ws = self.repo.get_writing_settings() if self.repo else None
        if ws is None:
            return {}
        return {
            "targetWords": ws.target_words,
            "minWords": ws.min_words,
            "maxWords": ws.max_words,
            "outlineFirst": ws.outline_first,
            "autoChapterCount": ws.auto_chapter_count,
            "requireHumanAcceptance": ws.require_human_acceptance,
            "styleProfileId": ws.style_profile_id,
        }

    def put_writing_settings(self, body: dict[str, Any]) -> dict[str, Any]:
        from novel_engine.models import WritingSettings

        if not self._ensure_repo_for_new_chain():
            return {}
        cur = self.repo.get_writing_settings()
        rec = WritingSettings(
            project_id=self.id,
            target_words=int(body.get("targetWords", cur.target_words) or cur.target_words),
            min_words=int(body.get("minWords", cur.min_words) or cur.min_words),
            max_words=int(body.get("maxWords", cur.max_words) or cur.max_words),
            outline_first=bool(body.get("outlineFirst", cur.outline_first)),
            auto_chapter_count=int(body.get("autoChapterCount", cur.auto_chapter_count) or cur.auto_chapter_count),
            require_human_acceptance=bool(body.get("requireHumanAcceptance", cur.require_human_acceptance)),
            style_profile_id=body.get("styleProfileId", cur.style_profile_id),
        )
        self.repo.set_writing_settings(rec)
        return self.get_writing_settings()

    def get_story_bible(self) -> dict[str, Any] | None:
        rt = self._rt()
        if rt is None:
            return None
        rec = rt.get_story_bible_record()
        if rec is None:
            return None
        return {
            "sourceType": rec.source_type,
            "titleStyle": rec.title_style_json,
            "worldConfig": rec.world_config_json,
            "characters": rec.characters_json,
            "locations": rec.locations_json,
            "factions": rec.factions_json,
            "items": rec.items_json,
            "relationships": rec.relationships_json,
            "timeline": rec.timeline_json,
            "openThreads": rec.open_threads_json,
            "lastState": rec.last_state_json,
            "narrativeConstraints": rec.narrative_constraints_json,
            "styleProfileId": rec.style_profile_id,
            "updatedAt": rec.updated_at,
            # 完全蒸馏：新增产物（前端「世界配置 / 大纲 / 阅读」面板复用）
            "foreshadows": rt.list_foreshadows(),
            "storyArcs": rt.list_story_arcs(),
            "sourceEvents": rt.list_source_events(),
            "codex": rt.list_codex(),
            "characterSnapshots": rt.list_character_snapshots(),
        }

    def import_source_text(self, body: dict[str, Any]) -> dict[str, Any]:
        if not self._ensure_repo_for_new_chain():
            return {"ok": False, "error": "no_repo"}
        text = body.get("text", "") or ""
        file_path = body.get("filePath", "") or ""
        file_paths = list(body.get("filePaths", []) or [])
        if not text.strip() and not file_path and not file_paths:
            return {"ok": False, "error": "empty"}
        filename = body.get("filename", "") or Path(file_path).name or "source.txt"
        result = import_into_repo(
            self.repo,
            project_id=self.id,
            created_at=_now(),
            text=text,
            filename=filename,
            file_path=file_path,
            file_paths=file_paths,
        )
        return self._finish_source_import(result, filename=filename, source_text=text)

    def import_source_uploads(self, files: list[tuple[str, bytes]]) -> dict[str, Any]:
        if not self._ensure_repo_for_new_chain():
            return {"ok": False, "error": "no_repo"}
        if not files:
            return {"ok": False, "error": "empty"}
        result = import_uploaded_into_repo(
            self.repo,
            project_id=self.id,
            created_at=_now(),
            files=files,
        )
        filenames = [Path(name).name for name, _payload in files]
        return self._finish_source_import(
            result,
            filename=", ".join(filenames),
            source_text="",
        )

    def _finish_source_import(
        self,
        result: dict[str, int | str],
        *,
        filename: str,
        source_text: str,
    ) -> dict[str, Any]:
        chapters = int(result.get("chapters", 0) or 0)
        source_hash_basis = source_text if source_text.strip() else "|".join(
            f"{doc.filename}:{doc.raw_text[:5000]}" for doc in self.repo.list_source_documents()
        )
        self.repo.set_project_meta(
            source_text_hash=hashlib.sha256(source_hash_basis.encode("utf-8")).hexdigest(),
            source_book_title=self.title,
            current_book_title=self.title,
            latest_source_chapter_no=chapters,
            chapter_start_no=chapters + 1,
            continuation_phase="source_imported",
            continuation_ready=False,
        )
        self.analysis_status = "ready"
        self.repo.set_project_meta(
            project_type=self.project_type,
            project_status=self.status,
            analysis_status=self.analysis_status,
        )
        return {
            "ok": True,
            "chapters": chapters,
            "filename": filename,
            "documents": int(result.get("documents", 0) or 0),
        }

    def source_chapters(self) -> list[dict[str, Any]]:
        rt = self._rt()
        if rt is None:
            return []
        return [
            {
                "id": ch.id,
                "chapterNo": ch.chapter_no,
                "title": ch.title,
                "text": ch.text,
                "wordCount": ch.word_count,
                "summary": ch.summary,
            }
            for ch in rt.list_source_chapters()
        ]

    def continuation_source(self) -> dict[str, Any]:
        rt = self._rt()
        if rt is None:
            return {"documents": [], "chapters": []}
        return {
            "documents": [
                {
                    "id": doc.id,
                    "filename": doc.filename,
                    "format": doc.format,
                    "createdAt": doc.created_at,
                }
                for doc in rt.list_source_documents()
            ],
            "chapters": self.source_chapters(),
        }

    def update_source_chapter(self, chapter_id: int, body: dict[str, Any]) -> dict[str, Any]:
        """编辑某条原文章节（标题/正文/摘要）。"""
        if not self._ensure_repo_for_new_chain():
            return {"ok": False}
        self.repo.update_source_chapter(
            chapter_id,
            title=body.get("title"),
            text=body.get("text"),
            summary=body.get("summary"),
        )
        self._touch()
        return {"ok": True}

    def resplit_source(self) -> dict[str, Any]:
        """按章节标题重新切分已导入原文（保留 source document 的 raw_text，重建章节）。"""
        if not self._ensure_repo_for_new_chain():
            return {"ok": False}
        from novel_engine.models import SourceChapter
        from novel_engine.story_bible.chapter_splitter import split_text_into_chapters

        docs = self.repo.list_source_documents()
        if not docs:
            return {"ok": False, "error": "no_source"}
        doc = docs[-1]
        # 只重建章节/分块，保留 source document 本身
        self.repo.conn.execute("DELETE FROM source_chunks")
        self.repo.conn.execute("DELETE FROM source_chapters")
        self.repo.conn.commit()
        chapters = split_text_into_chapters(doc.raw_text) or [("正文", (doc.raw_text or "").strip())]
        for idx, (title, content) in enumerate(chapters, 1):
            self.repo.insert_source_chapter(SourceChapter(
                project_id=self.id,
                source_document_id=doc.id,
                chapter_no=idx,
                title=title.strip() or f"第{idx}章",
                text=content.strip(),
                word_count=len(content.strip()),
                summary=(content.strip().replace("\n", " "))[:220],
                created_at=_now(),
            ))
        self.repo.set_project_meta(
            latest_source_chapter_no=len(chapters),
            chapter_start_no=len(chapters) + 1,
            continuation_phase="source_resplit",
            continuation_ready=False,
        )
        self._touch()
        return {"ok": True, "chapters": len(chapters)}

    def build_story_bible(self) -> dict[str, Any]:
        if not self._ensure_repo_for_new_chain():
            return {"ok": False}
        from novel_engine.story_bible import StoryBibleBuilder

        builder = StoryBibleBuilder(self.repo)
        if self.project_type == "continuation":
            builder.build_for_continuation(title=self.title)
        else:
            builder.build_for_original(
                title=self.title,
                theme=self._theme,
                source_text=json.dumps(self.draft, ensure_ascii=False),
            )
        self.analysis_status = "ready"
        self.repo.set_project_meta(
            project_type=self.project_type,
            project_status=self.status,
            analysis_status=self.analysis_status,
            chapter_start_no=resolve_chapter_start_no(self.repo),
            continuation_phase="story_bible_ready" if self.project_type == "continuation" else "",
        )
        return {"ok": True, "status": self.analysis_status}

    def story_bible_status(self) -> dict[str, Any]:
        pending = self.repo.list_chapter_drafts(status="pending_acceptance") if self.repo else []
        latest_pending = pending[0] if pending else None
        cmeta = self.repo.get_continuation_meta() if self.repo else None
        return {
            "status": self.analysis_status,
            "type": self.project_type,
            "pendingDraftId": latest_pending.id if latest_pending else None,
            "pendingChapterNo": latest_pending.chapter_no if latest_pending else None,
            "continuationReady": bool(cmeta.continuation_ready) if cmeta else False,
            "writeMode": cmeta.write_mode if cmeta else "",
            "continuationPhase": cmeta.continuation_phase if cmeta else "",
        }

    def get_continuation_settings(self) -> dict[str, Any]:
        if not self._ensure_repo_for_new_chain():
            return {}
        meta = self.repo.get_continuation_meta()
        return {
            "sourceTextHash": meta.source_text_hash,
            "continuationHint": meta.continuation_hint,
            "seriesId": meta.series_id,
            "sourceBookTitle": meta.source_book_title,
            "currentBookTitle": meta.current_book_title,
            "bookIndex": meta.book_index,
            "writeMode": meta.write_mode or "continue_current_book",
            "chapterStartNo": meta.chapter_start_no,
            "latestSourceChapterNo": meta.latest_source_chapter_no,
            "continuationReady": meta.continuation_ready,
            "continuationPhase": meta.continuation_phase,
            "timePosition": meta.time_position,
            "protagonistStrategy": meta.protagonist_strategy,
            "inheritUnresolvedThreads": meta.inherit_unresolved_threads,
            "experienceLayerEnabled": meta.experience_layer_enabled,
            "experienceLayerMode": meta.experience_layer_mode,
            "experienceSourcePath": meta.experience_source_path,
            "experienceStyleLevel": meta.experience_style_level,
            "activeLifeModelId": meta.active_life_model_id,
        }

    def set_continuation_settings(self, body: dict[str, Any]) -> dict[str, Any]:
        if not self._ensure_repo_for_new_chain():
            return {"ok": False}
        meta = self.repo.get_continuation_meta()
        meta.write_mode = normalize_write_mode(body.get("writeMode", "") or meta.write_mode or "continue_current_book")
        meta.continuation_hint = body.get("continuationHint", meta.continuation_hint) or ""
        meta.source_book_title = body.get("sourceBookTitle", meta.source_book_title) or self.title
        meta.current_book_title = body.get("currentBookTitle", meta.current_book_title) or self.title
        meta.series_id = body.get("seriesId", meta.series_id) or ""
        meta.book_index = int(body.get("bookIndex", meta.book_index) or meta.book_index or 1)
        meta.time_position = body.get("timePosition", meta.time_position) or ""
        meta.protagonist_strategy = body.get("protagonistStrategy", meta.protagonist_strategy) or ""
        if "inheritUnresolvedThreads" in body:
            meta.inherit_unresolved_threads = bool(body.get("inheritUnresolvedThreads"))
        if "experienceLayerEnabled" in body:
            meta.experience_layer_enabled = bool(body.get("experienceLayerEnabled"))
        meta.experience_layer_mode = body.get("experienceLayerMode", meta.experience_layer_mode) or "off"
        meta.experience_source_path = body.get("experienceSourcePath", meta.experience_source_path) or ""
        meta.experience_style_level = body.get("experienceStyleLevel", meta.experience_style_level) or "none"
        meta.chapter_start_no = resolve_chapter_start_no(self.repo)
        meta.continuation_phase = "settings_saved"
        self.repo.set_continuation_meta(meta)
        self._touch()
        return {"ok": True, **self.get_continuation_settings()}

    def start_continuation_distill(self, body: dict[str, Any]) -> dict[str, Any]:
        if not self._ensure_repo_for_new_chain():
            return {"ok": False}
        if not self.repo.list_source_chapters():
            return {"ok": False, "error": "no_source"}
        config = _normalize_distill_config(body)
        total = len(CONTINUATION_PHASE_STEPS)
        job = ContinuationJobRecord(
            id=f"cont_{uuid.uuid4().hex[:10]}",
            project_id=self.id,
            phase="B1",
            progress=1,
            total=total,
            status="running",
            error="",
            config_json={**config, "steps": _build_continuation_steps("B1", done=False)},
            created_at=_now(),
            updated_at=_now(),
        )
        self.repo.upsert_continuation_job(job)
        meta = self.repo.get_continuation_meta()
        meta.continuation_phase = "distilling"
        self.repo.set_continuation_meta(meta)
        shared_llm = self._gen_llm() if callable(self._gen_llm) else self._gen_llm
        shared_llm = shared_llm or MockClient()
        phase_summaries: dict[str, Any] = {}
        try:
            for idx, (code, _label) in enumerate(CONTINUATION_PHASE_STEPS, 1):
                job.phase = code
                job.progress = idx
                job.status = "running"
                summary: dict[str, Any]
                if code == "B1":
                    chapters = self.repo.list_source_chapters()
                    summary = {
                        "documents": len(self.repo.list_source_documents()),
                        "chapters": len(chapters),
                        "characters": sum(len(chapter.text or "") for chapter in chapters),
                    }
                elif code == "B2":
                    summary = extract_unified_blocks(
                        self.repo,
                        shared_llm,
                        target_chars=int(config["targetChunkChars"]),
                        max_chapters=int(config["maxChaptersPerChunk"]),
                        max_workers=int(config["distillWorkers"]),
                    )
                    if int(summary.get("needsReview", 0) or 0) > 0:
                        raise ValueError(
                            f"{summary['needsReview']} 个分块覆盖校验失败；已停止归并，避免生成不可靠知识包"
                        )
                elif code == "B3":
                    summary = reduce_unified_distillation(self.repo)
                else:
                    result = synthesize_knowledge_package(
                        self.repo,
                        shared_llm,
                        max_input_chars=int(config["globalInputMaxChars"]),
                    )
                    summary = dict(result.get("stats") or {})
                    # B4.5 后期审查：对重点但单薄的人物/地点/势力回原文补全（mock 自动跳过）。
                    review = review_and_augment(self.repo, shared_llm)
                    summary["augmentedEntities"] = review.get("augmented", 0)
                phase_summaries[code] = summary
                job.status = "done" if idx == total else "running"
                job.config_json = {
                    **config,
                    "phaseSummaries": phase_summaries,
                    "distillSummary": summary,
                    "steps": _build_continuation_steps(code, done=(idx == total)),
                }
                job.updated_at = _now()
                self.repo.upsert_continuation_job(job)
        except Exception as exc:
            job.status = "failed"
            job.error = str(exc)
            job.config_json = {
                **config,
                "phaseSummaries": phase_summaries,
                "steps": _build_continuation_steps(job.phase, done=False),
            }
            job.updated_at = _now()
            self.repo.upsert_continuation_job(job)
            meta.continuation_phase = "distill_failed"
            self.repo.set_continuation_meta(meta)
            return {"ok": False, "jobId": job.id, "error": str(exc)}
        meta.continuation_phase = "distilled"
        meta.continuation_ready = False
        self.repo.set_continuation_meta(meta)
        self._touch()
        return {"ok": True, "jobId": job.id, "summary": phase_summaries}

    def continuation_knowledge_package(self) -> dict[str, Any]:
        if not self._ensure_repo_for_new_chain():
            return {}
        return get_knowledge_package(self.repo)

    def continuation_job_status(self) -> dict[str, Any]:
        if not self._ensure_repo_for_new_chain():
            return {"status": "idle"}
        job = self.repo.latest_continuation_job()
        if not job:
            return {"status": "idle"}
        return {
            "id": job.id,
            "phase": job.phase,
            "progress": job.progress,
            "total": job.total,
            "status": job.status,
            "error": job.error,
            "config": job.config_json,
            "currentStep": job.phase,
            "steps": list(job.config_json.get("steps", [])),
            "updatedAt": job.updated_at,
        }

    def lock_continuation(self) -> dict[str, Any]:
        if not self._ensure_repo_for_new_chain():
            return {"ok": False}
        meta = self.repo.get_continuation_meta()
        meta.write_mode = normalize_write_mode(meta.write_mode or "continue_current_book")
        meta.chapter_start_no = resolve_chapter_start_no(self.repo)
        meta.continuation_ready = True
        meta.continuation_phase = "locked"
        self.repo.set_continuation_meta(meta)
        self._touch()
        return {"ok": True, **self.get_continuation_settings(), "snapshot": build_continuation_snapshot(self.repo)}

    def style_diagnostics(self) -> dict[str, Any]:
        if not self._ensure_repo_for_new_chain():
            return {"corpus": {}, "latestDraft": None}
        drafts = self.repo.list_chapter_drafts()
        latest = drafts[0] if drafts else None
        latest_payload = None
        if latest is not None:
            latest_payload = {
                "id": latest.id,
                "chapterNo": latest.chapter_no,
                "candidateGroupId": latest.candidate_group_id,
                "scoreBreakdown": latest.score_breakdown_json,
                "retrievedSegmentIds": latest.retrieved_segment_ids_json,
                "stylePacket": latest.style_packet_json,
                "contextSnapshot": latest.context_snapshot_json,
                "revisionHistory": latest.revision_history_json,
            }
        return {
            "corpus": _style_diagnostics_payload(self.repo.style_corpus_summary()),
            "latestDraft": latest_payload,
        }

    def create_chapter_draft(self, body: dict[str, Any]) -> dict[str, Any]:
        from novel_engine.story_bible import DraftManager

        if not self._ensure_repo_for_new_chain():
            return {"ok": False, "error": "no_repo"}
        if self.project_type == "continuation" and not self.repo.get_continuation_meta().continuation_ready:
            return {"ok": False, "error": "continuation_not_locked"}
        manager = DraftManager(self.repo, self._gen_llm, project_id=self.id)
        draft = manager.generate(
            guidance=body.get("guidance", "") or "",
            target_words=int(body.get("targetWords", 0) or 0),
            outline_only=bool(body.get("outlineOnly", False)),
            mode=body.get("mode", "manual") or "manual",
        )
        return {
            "id": draft.id,
            "chapterNo": draft.chapter_no,
            "title": draft.title,
            "outline": draft.outline,
            "prose": draft.prose,
            "guidance": draft.guidance,
            "targetWords": draft.target_words,
            "mode": draft.mode,
            "status": draft.status,
            "contextSnapshot": draft.context_snapshot_json,
            "candidateGroupId": draft.candidate_group_id,
            "stylePacket": draft.style_packet_json,
            "scoreBreakdown": draft.score_breakdown_json,
            "retrievedSegmentIds": draft.retrieved_segment_ids_json,
            "revisionHistory": draft.revision_history_json,
            "createdAt": draft.created_at,
        }

    def list_chapter_drafts(self) -> list[dict[str, Any]]:
        if not self._ensure_repo_for_new_chain():
            return []
        return [
            {
                "id": draft.id,
                "chapterNo": draft.chapter_no,
                "title": draft.title,
                "outline": draft.outline,
                "prose": draft.prose,
                "guidance": draft.guidance,
                "targetWords": draft.target_words,
                "mode": draft.mode,
                "status": draft.status,
                "contextSnapshot": draft.context_snapshot_json,
                "candidateGroupId": draft.candidate_group_id,
                "stylePacket": draft.style_packet_json,
                "scoreBreakdown": draft.score_breakdown_json,
                "retrievedSegmentIds": draft.retrieved_segment_ids_json,
                "revisionHistory": draft.revision_history_json,
                "createdAt": draft.created_at,
            }
            for draft in self.repo.list_visible_chapter_drafts()
        ]

    def accept_chapter_draft(self, draft_id: int) -> dict[str, Any]:
        from novel_engine.story_bible import DraftManager

        if not self._ensure_repo_for_new_chain():
            return {"ok": False}
        accepted = DraftManager(self.repo, self._gen_llm, project_id=self.id).accept(draft_id)
        self._refresh_counts()
        self._touch()
        return {
            "ok": True,
            "acceptedChapterId": accepted.id,
            "chapterNo": accepted.chapter_no,
            "title": accepted.title,
        }

    def force_accept_chapter_draft(self, draft_id: int, reason: str) -> dict[str, Any]:
        from novel_engine.story_bible import DraftManager

        if not self._ensure_repo_for_new_chain():
            return {"ok": False}
        accepted = DraftManager(
            self.repo, self._gen_llm, project_id=self.id
        ).force_accept(draft_id, reason=reason)
        self._refresh_counts()
        self._touch()
        return {
            "ok": True,
            "acceptedChapterId": accepted.id,
            "chapterNo": accepted.chapter_no,
            "title": accepted.title,
            "forced": True,
        }

    def reject_chapter_draft(self, draft_id: int) -> dict[str, Any]:
        from novel_engine.story_bible import DraftManager

        if not self._ensure_repo_for_new_chain():
            return {"ok": False}
        DraftManager(self.repo, self._gen_llm, project_id=self.id).reject(draft_id)
        return {"ok": True}

    def auto_write_chapters(self, body: dict[str, Any]) -> dict[str, Any]:
        from novel_engine.story_bible import DraftManager

        if not self._ensure_repo_for_new_chain():
            return {"ok": False}
        if self.project_type == "continuation" and not self.repo.get_continuation_meta().continuation_ready:
            return {"ok": False, "error": "continuation_not_locked"}
        manager = DraftManager(self.repo, self._gen_llm, project_id=self.id)
        ids = manager.auto_write(
            chapters=int(body.get("chapters", 5) or 5),
            target_words=int(body.get("targetWords", 0) or 0),
            guidance=body.get("guidance", "") or "",
        )
        self._refresh_counts()
        self._touch()
        return {"ok": True, "draftIds": ids}

    def accepted_chapters(self) -> list[dict[str, Any]]:
        rt = self._rt()
        if rt is None:
            return []
        return [
            {
                "id": ch.id,
                "draftId": ch.draft_id,
                "chapterNo": ch.chapter_no,
                "title": ch.title,
                "prose": ch.prose,
                "summary": ch.summary,
                "createdAt": ch.created_at,
            }
            for ch in rt.list_accepted_chapters()
        ]

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
        return [
            schemas.persona_out(p, rt.get_character_display_name(p.agent_id, p.name))
            for p in rt.list_personas()
        ] if rt else []

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

    # ---------- 关键场景档案 ----------
    def scene_anchors(self):
        rt = self._rt()
        return [schemas.scene_anchor_out(a) for a in rt.list_scene_anchors()] if rt else []

    def save_scene_anchor(self, payload: dict) -> dict[str, Any]:
        """新建或编辑一条关键场景档案（人工锁定/修正场景不变事实）。"""
        if not self.repo:
            return {"ok": False, "reason": "no_repo"}
        scene_id = str((payload or {}).get("sceneId") or "").strip() or f"scn_{uuid.uuid4().hex[:8]}"
        existing = self.repo.get_scene_anchor(scene_id)
        kind = str((payload or {}).get("kind", "scene")).strip() or "scene"
        self.repo.upsert_scene_anchor(SceneAnchor(
            scene_id=scene_id,
            name=str((payload or {}).get("name", "")).strip(),
            kind=kind,
            location_id=str((payload or {}).get("locationId", "")).strip(),
            canonical_facts=[str(x).strip() for x in ((payload or {}).get("canonicalFacts") or []) if str(x).strip()],
            aliases=[str(x).strip() for x in ((payload or {}).get("aliases") or []) if str(x).strip()],
            established_chapter=int((payload or {}).get("establishedChapter") or (existing.established_chapter if existing else 0) or 0),
            created_at=(existing.created_at if existing else datetime.now(timezone.utc).isoformat()),
        ))
        self._touch()
        return {"ok": True, "sceneId": scene_id}

    def delete_scene_anchor(self, scene_id: str) -> dict[str, Any]:
        if self.repo:
            self.repo.delete_scene_anchor(scene_id)
            self._touch()
        return {"ok": True}

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

    def create(self, title: str, project_type: str = "original",
               template_id: str = "") -> Project:
        p = Project(title, project_type=project_type)
        if template_id:
            p.draft["templateId"] = template_id
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
