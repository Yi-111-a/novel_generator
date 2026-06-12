"""剪辑层编排（设计文档 §4）。

串联：选材+排序（§4.1/§4.2）→ 逐场揭示决策（§4.6）→ 渲染（§6.3）→
反抽象校验+重写（§4.5）→ 落 scenes + 更新 reader_knowledge。

输出 scenes：discourse_order ≠ story_time，证明"能把事件变成不流水账的散文"。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from ..llm.base import LLMClient
from ..models import Scene
from ..repository import Repository
from .exposition import Exposition
from .foreshadow import ForeshadowLedger
from .narrator import Narrator
from .reveal import RevealPlan, commit_reveals, plan_reveals
from .selection import SelectionResult, compute_drama_score, select_and_order
from .style import AntiAbstractValidator
from .tension import TensionScheduler


@dataclass
class SceneRender:
    scene: Scene
    reveal_plan: RevealPlan
    style_attempts: int
    style_ok: bool
    role: str = "scene"                       # peak | rising | breather | scene
    planted: list[str] = field(default_factory=list)   # 本场新埋伏笔 id
    paid_off: list[str] = field(default_factory=list)   # 本场回收伏笔 id


class Editor:
    def __init__(
        self,
        repo: Repository,
        llm: LLMClient | None = None,
        theme: str = "",
        threshold: float = 0.5,
        reveal_budget: int = 1,
        max_rewrites: int = 1,
        # M4 可选组件：传入即启用张弛调度 / 伏笔台账 / 背景渗透
        tension: bool = False,
        foreshadows: bool = False,
        exposition: bool = False,
        max_consecutive_high: int = 2,
    ) -> None:
        self.repo = repo
        self.theme = theme
        self.threshold = threshold
        self.reveal_budget = reveal_budget
        self.max_rewrites = max_rewrites
        self.narrator = Narrator(repo, llm)
        self.offline_narrator = Narrator(repo, None)  # 反抽象兜底：LLM 屡次不过则用离线稿
        self.style = AntiAbstractValidator()
        self.scheduler = (
            TensionScheduler(repo, max_consecutive_high=max_consecutive_high) if tension else None
        )
        self.foreshadows = ForeshadowLedger(repo) if foreshadows else None
        self.exposition = Exposition(repo) if exposition else None
        self.tension_alarms: list[str] = []

    def select(self) -> SelectionResult:
        return select_and_order(self.repo, self.theme, self.threshold)

    def render_incremental(self, rendered_event_ids: set[str], max_new: int = 0) -> list[str]:
        """增量渲染：只把"尚未成稿的高戏剧事件"按故事顺序追加成新场，绝不重渲已有场。

        相比 run() 每次清空+全量重渲（O(全部场)次 LLM 调用），这里是 O(新增场)。
        为了让直播式增长稳定，按故事时间顺序追加（discourse_order 递增），
        第一场（全书开篇）用钩子开篇。返回本次新渲染的 event_id 列表。
        """
        threshold = self.threshold
        existing = self.repo.list_scenes()
        pos = max((s.discourse_order for s in existing), default=0)
        first = len(existing) == 0
        events = sorted(self.repo.list_events(), key=lambda e: e.story_time)

        # P1：把事件按「场」分组（一个 beat = K 个 actor 的对手戏 = 1 场），
        # 而非一个事件一场。只有"本场齐了"（K 个事件到齐，或该章已收束）才成稿。
        groups = self._group_into_scenes(events, rendered_event_ids, threshold, first)

        # 限批：每次最多渲染 max_new 场
        if max_new > 0:
            groups = groups[:max_new]

        # 问题4 修复：从已成稿场重建"前情摘要"，让叙述者知道前文写过什么剧情，
        # 不只靠 n-gram 去重 → 杜绝相邻场重复同一段对白/走位。
        summary = self._rebuild_rolling_summary()

        new_ids: list[str] = []
        for group in groups:
            head = group[0]
            # 问题6：优先用本章指定的 POV（focus_agents 轮换，让配角有主讲场）；
            # 回退到本场首个行动者。POV 须在本场在场（actors∪perceivers），否则回退保证隔离正确。
            fallback = head.actors[0] if head.actors else (head.perceivers[0] if head.perceivers else None)
            pov = fallback
            ch_id = next((e.beat_id for e in group if e.beat_id), None)
            ch = self.repo.get_chapter_plan(ch_id) if ch_id else None
            chap_pov = getattr(ch, "pov_agent", "") if ch else ""
            if chap_pov:
                present = {a for e in group for a in (e.actors or [])} | \
                          {p for e in group for p in (e.perceivers or [])}
                if chap_pov in present:
                    pov = chap_pov
            if pov is None:
                continue
            plan = plan_reveals(self.repo, pov, group, self.reveal_budget)
            prose, attempts, ok = self._render_with_gate(pov, group, summary, plan, hook=first, scene_pos=pos + 1)
            summary = self._extend_summary(summary, pos + 1, group)
            self._anchor_after_render(prose, pov)
            revealed = commit_reveals(self.repo, plan, pov, pos + 1)
            self._handle_foreshadows(pov, plan, revealed, pos + 1)
            pos += 1
            tension = max((self.repo.get_event_drama_score(e.event_id) or 0.5) for e in group)
            self.repo.insert_scene(
                Scene(
                    scene_id=f"sc_{uuid.uuid4().hex[:8]}",
                    discourse_order=pos,
                    source_events=[e.event_id for e in group],
                    pov=pov,
                    target_tension=round(tension, 3),
                    prose_text=prose,
                    newly_revealed=revealed,
                )
            )
            new_ids.extend(e.event_id for e in group)
            first = False
        # 审计闸门：本批渲染后，对"已收束且渲染完整"的章逐章严格审计，不合格就地重渲。
        self._audit_done_chapters()
        return new_ids

    # ---------- 章级审计闸门（衔接 / 转场 / 道具人物合规；不合格重渲） ----------
    def _audit_done_chapters(self, max_retry: int = 1) -> list[str]:
        """对每个 done 且尚未 audited、且**已不在渲染最前沿**（说明已渲完）的章做严格审计；
        不合格 → 就地重渲该章各场（保留 scene_id/顺序/读者账本），至多 max_retry 次，然后标记 audited。
        返回被判不合格（触发重渲）的章 id。无 LLM 时跳过。"""
        if self.narrator.llm is None:
            return []
        from .audit import audit_chapter

        scenes_all = self.repo.list_scenes()
        if not scenes_all:
            return []
        # 当前渲染最前沿那一章（最后一场所属）仍可能在写 → 不审
        last = max(scenes_all, key=lambda s: s.discourse_order)
        front_ch = self._chapter_of_scene(last)
        chs = sorted(self.repo.list_chapter_plans(), key=lambda c: c.sequence_order)
        failed: list[str] = []
        for ch in chs:
            if ch.status != "done" or int(getattr(ch, "audited", 0)) or ch.chapter_id == front_ch:
                continue
            ev_ids = {e.event_id for e in self.repo.events_for_beat(ch.chapter_id)}
            ch_scenes = sorted((s for s in scenes_all
                                if any(eid in ev_ids for eid in s.source_events)),
                               key=lambda s: s.discourse_order)
            if not ch_scenes:
                continue
            prev_ch = next((c for c in reversed(chs) if c.sequence_order < ch.sequence_order), None)
            ok, fb = audit_chapter(self.repo, ch, ch_scenes, prev_ch, self.narrator.llm)
            tries = 0
            while not ok and tries < max_retry:
                self._rerender_chapter(ch_scenes, fb)
                ch_scenes = sorted((s for s in self.repo.list_scenes()
                                    if any(eid in ev_ids for eid in s.source_events)),
                                   key=lambda s: s.discourse_order)
                ok, fb = audit_chapter(self.repo, ch, ch_scenes, prev_ch, self.narrator.llm)
                tries += 1
            if tries:
                failed.append(ch.chapter_id)
            ch.audited = 1
            self.repo.upsert_chapter_plan(ch)
        return failed

    def _chapter_of_scene(self, scene) -> str | None:
        for eid in scene.source_events:
            ev = self.repo.get_event(eid)
            if ev and ev.beat_id:
                return ev.beat_id
        return None

    def _rerender_chapter(self, ch_scenes, feedback: str) -> None:
        """就地重渲一章的各场（只更新正文，保留顺序与读者账本），带审计反馈。"""
        summary = self._rebuild_rolling_summary()
        for s in ch_scenes:
            events = [e for eid in s.source_events if (e := self.repo.get_event(eid))]
            if not events:
                continue
            prose = self.narrator.render(s.pov, events, summary, s.newly_revealed, [],
                                         feedback=f"[本章审计未通过，请按意见修正并重写本场]{feedback}")
            self.repo.update_scene_prose(s.scene_id, prose)

    def _summarize_group(self, pos: int, group) -> str:
        """把一场（多角色交锋）压成一句前情摘要：谁做了什么、说了关键的一句。"""
        names = {e.entity_id: e.name for e in self.repo.list_entities()}
        bits = []
        for e in group:
            if not e.actors:
                continue
            who = names.get(e.actors[0], e.actors[0])
            p = e.payload or {}
            seg = f"{who}{e.action_type}"
            dlg = (p.get("dialogue") or "").strip()
            if dlg:
                seg += f"（“{dlg[:18]}”）"
            bits.append(seg)
        return f"第{pos}场：" + "；".join(bits) if bits else ""

    def _rebuild_rolling_summary(self, keep_last: int = 5) -> str:
        """从已成稿场重建前情摘要（仅保留最近 keep_last 场，控制 token）。"""
        scenes = sorted(self.repo.list_scenes(), key=lambda s: s.discourse_order)[-keep_last:]
        lines = []
        for s in scenes:
            evs = [self.repo.get_event(eid) for eid in s.source_events]
            evs = [e for e in evs if e is not None]
            line = self._summarize_group(s.discourse_order, evs)
            if line:
                lines.append(line)
        return "  ".join(lines)

    def _extend_summary(self, summary: str, pos: int, group, keep_last: int = 5) -> str:
        """追加本场摘要，并裁剪到最近 keep_last 场。"""
        new = self._summarize_group(pos, group)
        parts = [p for p in summary.split("  ") if p] + ([new] if new else [])
        return "  ".join(parts[-keep_last:])

    def _group_into_scenes(self, events, rendered_event_ids, threshold, first):
        """P1 把未成稿事件按「场」（beat = K 个 actor 的交锋）分组。
        同章事件按 story_time 切成 K 个一组；只返回"完整且未成稿、且至少含一个达标事件"的组。
        无 beat_id（如 worldsmith 登场事件）→ 单独成场（K=1）。"""
        def _score(e):
            s = self.repo.get_event_drama_score(e.event_id)
            if s is None:
                s = compute_drama_score(self.repo, e, self.theme)
                self.repo.set_event_drama_score(e.event_id, s)
            return s

        # 先按章（beat_id）归集，保持原始故事时间顺序
        by_ch: dict[str | None, list] = {}
        for e in events:
            by_ch.setdefault(e.beat_id, []).append(e)

        groups: list[list] = []
        for ch_id, evs in by_ch.items():
            evs = sorted(evs, key=lambda e: e.story_time)
            ch = self.repo.get_chapter_plan(ch_id) if ch_id else None
            K = min(len(ch.cast), 4) if (ch and ch.cast) else 1
            ch_done = (ch.status == "done") if ch else True
            for i in range(0, len(evs), K):
                chunk = evs[i:i + K]
                # 已成稿（任一事件已渲染）→ 跳过
                if any(e.event_id in rendered_event_ids for e in chunk):
                    continue
                # 完整性：K 个到齐，或该章已收束（尾组允许不足 K）
                if len(chunk) < K and not ch_done:
                    continue
                # 至少含一个达标事件（多角色交锋通常含对白/抉择，分数高）
                if any(_score(e) >= threshold for e in chunk):
                    groups.append(chunk)

        # 开篇兜底：还没有任何场、也没有任何达标组 → 至少把最高分事件单独成稿
        if first and not groups and events:
            top = max(events, key=lambda e: self.repo.get_event_drama_score(e.event_id) or 0)
            groups = [[top]]
        return groups

    def run(self) -> list[SceneRender]:
        sel = self.select()
        # 张弛调度：在连续高张力间插喘息（用被弃的低 drama 事件）；否则沿用 M3 顺序
        if self.scheduler is not None:
            schedule = self.scheduler.schedule(sel.selected, sel.skipped)
            self.tension_alarms = schedule.alarms
            items = [(s.event, s.target_tension, s.role) for s in schedule.scenes]
        else:
            items = [
                (ev, self.repo.get_event_drama_score(ev.event_id) or 0.5, "scene")
                for ev in sel.selected
            ]

        renders: list[SceneRender] = []
        summary = ""
        for pos, (ev, tension, role) in enumerate(items, start=1):
            pov = ev.actors[0] if ev.actors else (ev.perceivers[0] if ev.perceivers else None)
            if pov is None:
                continue

            plan = plan_reveals(self.repo, pov, [ev], self.reveal_budget)
            prose, attempts, ok = self._render_with_gate(pov, [ev], summary, plan, hook=(pos == 1), scene_pos=pos)
            self._anchor_after_render(prose, pov)  # §4.3 定调 + 累积意象
            revealed = commit_reveals(self.repo, plan, pov, pos)

            # 喘息场景：渗透背景（§4.3），把背景也算作本场新揭
            if role == "breather" and self.exposition is not None:
                revealed = revealed + self.exposition.drip(pos, pov)

            planted, paid = self._handle_foreshadows(pov, plan, revealed, pos)

            scene = Scene(
                scene_id=f"sc_{uuid.uuid4().hex[:8]}",
                discourse_order=pos,
                source_events=[ev.event_id],
                pov=pov,
                target_tension=round(tension, 3),
                prose_text=prose,
                newly_revealed=revealed,
            )
            self.repo.insert_scene(scene)
            renders.append(SceneRender(scene, plan, attempts, ok, role, planted, paid))
            summary = (summary + f" 第{pos}场：{ev.action_type}。").strip()

        return renders

    def _handle_foreshadows(self, pov, plan, revealed, pos) -> tuple[list[str], list[str]]:
        """回收被揭真相命中的伏笔；为本场藏起的真相埋新伏笔（§1.5）。"""
        if self.foreshadows is None:
            return [], []
        paid: list[str] = []
        for fid in revealed:
            paid += [f.foreshadow_id for f in self.foreshadows.pay_off_for_fact(fid, pos)]
        planted: list[str] = []
        for fid in plan.conceal[:1]:  # 每场至多埋一个，避免悬念过载
            fact = self.repo.get_fact(fid)
            q = f"{fact.canonical_content[:12]}……究竟如何？" if fact else "悬而未决之事？"
            fs = self.foreshadows.plant(q, fid, pos, must_resolve=True)
            if fs and fs.planted_discourse_pos == pos:
                planted.append(fs.foreshadow_id)
        return planted, paid

    def _anchor_after_render(self, prose: str, pov: str) -> None:
        """§4.3：首场成稿即定调；累积本场 POV 的关联意象词到 motif_lexicon（去重）。"""
        try:
            self.repo.set_tone_sample(prose)
            persona = self.repo.get_persona(pov)
            if persona and persona.motif_objects:
                names = {e.entity_id: e.name for e in self.repo.list_entities()}
                self.repo.add_motifs([names.get(o, o) for o in persona.motif_objects])
        except Exception:
            pass

    def _render_with_gate(self, pov, events, summary, plan, hook: bool = False,
                          scene_pos: int = 0) -> tuple[str, int, bool]:
        """渲染并跑反抽象闸门；不过则带反馈重写；屡次不过则退回离线稿。"""
        feedback = None
        prose = ""
        for attempt in range(self.max_rewrites + 1):
            prose = self.narrator.render(
                pov, events, summary, plan.reveal, plan.conceal, feedback, hook=hook, scene_pos=scene_pos
            )
            res = self.style.check(prose)
            if res.ok:
                return prose, attempt + 1, True
            feedback = res.summary()
        # 兜底：保证产物干净
        offline = self.offline_narrator.render(
            pov, events, summary, plan.reveal, plan.conceal, hook=hook, scene_pos=scene_pos
        )
        return offline, self.max_rewrites + 1, self.style.check(offline).ok
