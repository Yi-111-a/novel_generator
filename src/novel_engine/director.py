"""导演循环（设计文档 §2，M2 的 decision 节拍部分）。

一个 step：
  1. 跑监控（§3.4）：若某角色弱点长期零成本 → 本拍 prefer_flaw 逼弱点反噬。
  2. 内在冲突生成器选目标 + 构造两难（§3）。
  3. 交权给角色 Agent 自主决定（§0 原则3：不暗示正确答案）。
  4. 确定性校验 + 落库（复用 M1 Engine）。
  5. 传播：在场者直接感知；可选让某角色把旧情报转述给他人（二手扭曲）。
  6. 写回：哪个 value 赢了 → arc_state；付出了什么 → cost_ledger；推进 thread 张力。

导演通过环境行使控制，绝不直接改写角色的决定。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from .agent import CharacterAgent
from .dilemma import DilemmaGenerator
from .engine import Engine, TickResult
from .models import Dilemma, Event, KnowledgeItem, ReaderKnowledge, Scene
from .monitors import Alarm, Monitors
from .propagation import Propagator
from .repository import Repository
from .validator import Validator


@dataclass
class DirectorStep:
    tick: int
    alarms: list[Alarm]
    dilemma: Dilemma | None
    result: TickResult | None
    writeback: dict = field(default_factory=dict)
    chapter_id: str | None = None          # 计划模式：本拍属于哪一章
    chapter_done: bool = False             # 本拍是否写满了当前章
    revealed: list[str] = field(default_factory=list)  # 本拍主角新发现的真相 fact_id


class Director:
    def __init__(
        self,
        repo: Repository,
        generator: DilemmaGenerator | None = None,
        agent: CharacterAgent | None = None,
        validator: Validator | None = None,
        monitors: Monitors | None = None,
        propagator: Propagator | None = None,
        worldsmith=None,
        structural_every: int = 0,
        planner=None,
        consistency=None,
        writer=None,
        extractor=None,
        controller=None,
        mode: str = "sim",
    ) -> None:
        self.repo = repo
        self.generator = generator
        self.monitors = monitors
        self.propagator = propagator or Propagator(repo)
        # B5 清理：scripted 模式不需要模拟核心（dilemma/agent/engine）。仅当 agent+validator
        # 就位（sim 路径）才构造 Engine；scripted 下为 None（_step_scripted 不碰它）。
        self.engine = (
            Engine(repo, agent, validator, max_retries=2,
                   propagator=self.propagator, consistency=consistency)
            if (agent is not None and validator is not None) else None
        )
        # B1 大纲驱动重构：scripted 模式下 director 不再"模拟→渲染"，而是
        # SceneWriter 照 beat 直接写场 + FactExtractor 抽事实回写。默认 sim（旧路径零改动）。
        self.writer = writer
        self.extractor = extractor
        self.controller = controller  # B4 双向控制器（在beat/矛盾/解离反推）
        self.mode = mode
        # 可选：结构性世界生成节拍（§2）。worldsmith 给定且 structural_every>0 时，
        # 每隔 structural_every 拍引入一个新角色/道具（向后兼容：默认关闭）。
        self.worldsmith = worldsmith
        self.structural_every = structural_every
        # 可选：规划器（P2/P3）。给定即进入"计划模式"——按当前章计划约束生成，
        # 而非纯涌现 + 随机注入。未给定则行为完全同旧版（旧项目/旧测试不受影响）。
        self.planner = planner
        self._tick = 0
        self._stall: dict[str, int] = {}  # B3 章内"无推进"连拍计数（按章）

    def step(self) -> DirectorStep:
        self._tick += 1
        tick = self._tick
        # B1 scripted：照 beat 直接写场 + 抽事实回写（需 planner + writer + extractor）
        if (self.mode == "scripted" and self.planner is not None
                and self.writer is not None and self.extractor is not None):
            return self._step_scripted(tick)
        if self.planner is not None:
            return self._step_planned(tick)

        # 0. 结构性节拍：到点则引入新角色/道具，本拍交给世界生成（§2 structural）
        if self.worldsmith is not None and self.structural_every > 0 and tick % self.structural_every == 0:
            intro = self.worldsmith.introduce(tick)
            if intro is not None:
                return DirectorStep(tick, [], None, None, {"structural": intro.__dict__})

        # 1. 监控（基于到目前为止的状态）
        alarms = self.monitors.check(tick - 1)
        prefer_flaw = False
        forced_target: str | None = None
        for a in alarms:
            if a.kind == "flaw_free_too_long":
                forced_target, prefer_flaw = a.agent_id, True
                break

        # 2. 生成两难
        dilemma = self.generator.generate(tick, target=forced_target, prefer_flaw=prefer_flaw)
        if dilemma is None:
            return DirectorStep(tick, alarms, None, None)

        # 3+4. 交权 + 校验 + 落库（在场者 = 该线涉及角色）
        perceivers = self._present_agents(dilemma)
        allowed = [e.entity_id for e in self.repo.list_entities()]
        result = self.engine.run_tick(
            dilemma.target_agent, dilemma.situation, allowed,
            perceivers=perceivers, tick=tick,
        )

        # 6. 写回
        wb: dict = {}
        if result.committed:
            wb = self._writeback(dilemma, result, tick)
        return DirectorStep(tick, alarms, dilemma, result, wb)

    # ================= 计划模式（P3：大纲驱动） =================
    def _step_planned(self, tick: int) -> DirectorStep:
        """按当前章计划约束推进，P1 多角色对手戏：
          · 1 个 beat = K 个 actor 轮流行动（K = min(len(cast), 4)），组成一场交锋
          · 每个 actor 的处境 = 规定情境（dilemma）+ 本场已发生 transcript
          · 事件打上章号；写满 target_scenes 个 beat 即收束本章
        """
        # §4.2 情绪随拍衰减（自然回落，避免一个情绪挂太久）
        self.repo.decay_emotions()
        # ⑤ 软节拍：有 active/planned 章则用之，否则临场懒生成下一章（据前情，含跨 Arc）
        ch = self.planner.ensure_chapter()
        if ch is None:  # 全书铺满 → 待收尾
            return DirectorStep(tick, [], None, None)
        if ch.status != "active":
            ch.status = "active"
            self.repo.upsert_chapter_plan(ch)
            self._activate_part_of(ch)  # §11 演到该 Part → 标 active（其章由 provisional 转 locked）

        # 监控仍照跑（弱点反噬等），但目标必须落在本章 cast 内
        alarms = self.monitors.check(tick - 1)
        prefer_flaw = any(a.kind == "flaw_free_too_long" and a.agent_id in ch.cast for a in alarms)

        if not ch.cast:
            return DirectorStep(tick, alarms, None, None, chapter_id=ch.chapter_id)

        # P1 多角色：K 个 actor 轮番行动构成一场（beat）
        K = min(len(ch.cast), 4)  # 每场最多 4 个 actor
        n_events = self.repo.count_events_for_beat(ch.chapter_id)
        beat_idx = n_events // K      # 当前是第几个 beat（场）
        actor_idx = n_events % K      # 当前 beat 里轮到第几个 actor

        # B2 逐场消费 beat：按 beat_idx 取本场节拍提示，作为"规定情境"方向
        beats = ch.beat_goals or []
        beat_hint = beats[min(beat_idx, len(beats) - 1)] if beats else ""

        # B3 推进闸门：本章连续 ≥2 拍无实质推进 → 升级为弱点驱动两难
        if self._stall.get(ch.chapter_id, 0) >= 2:
            prefer_flaw = True

        # 本 beat 已发生事件的对白摘要（给当前 actor 看，让他据此反应）
        all_ch_events = self.repo.events_for_beat(ch.chapter_id)
        scene_start = beat_idx * K
        scene_events_prior = all_ch_events[scene_start: scene_start + actor_idx]
        scene_transcript = self._format_scene_transcript(scene_events_prior)
        # 问题4 修复：本章前面 beat（已收的场）的最近若干事件，让 actor 知道自己之前说过/做过什么，
        # 避免跨场重复同一句台词/同一个动作（如陈阿公每场都说"雾天里石头会走路"）。
        chapter_prior_events = all_ch_events[:scene_start][-6:]
        chapter_transcript = self._format_scene_transcript(chapter_prior_events)

        # 当前 actor：按顺序从 cast 中轮取
        target = ch.cast[actor_idx % len(ch.cast)]

        facts_before = len(self.repo.list_facts())
        dilemma = self.generator.generate(tick, target=target, prefer_flaw=prefer_flaw,
                                          beat_hint=beat_hint, beat_idx=beat_idx)
        if dilemma is None:
            return DirectorStep(tick, alarms, None, None, chapter_id=ch.chapter_id)

        # 节拍去重消费（治本）：当前 actor 在本章已说过的对白 + 是否已登场，单独醒目地告知，
        # 避免他在相邻 beat 重复同一句登场台词/重复同样的动作（如苏静两场都说"我正盯着呢"）。
        my_prior = [e for e in all_ch_events[:scene_start + actor_idx]
                    if target in (e.actors or [])]
        my_lines = [(e.payload or {}).get("dialogue", "").strip()
                    for e in my_prior if (e.payload or {}).get("dialogue", "").strip()]

        # 把本章前情 + 本场已发生 transcript 追加到处境里
        situation = dilemma.situation
        if chapter_transcript:
            situation = (f"{situation}\n\n【本章前面已发生（不要重复你已说过的台词或已做过的动作，"
                         f"要推进剧情而非复述）】\n{chapter_transcript}")
        if my_prior:
            warn = "【重要】你本章已经登场过了，不要再写「重新登场/初次出现/刚走进来」，要接着此前的处境继续。"
            if my_lines:
                warn += "你已经说过这些话，**不得重复或换汤不换药地再说一遍**：\n" + \
                        "\n".join(f"· 「{l}」" for l in my_lines[-4:])
            situation = f"{situation}\n\n{warn}"
        if scene_transcript:
            situation = f"{situation}\n\n【本场刚刚发生（据此做出你的反应）】\n{scene_transcript}"
        # 问题4：本章须达成的推进方向——引导 actor 朝"抉择/交换关键物/揭线索"行动，别原地周旋。
        if getattr(ch, "exit_state", "") and not self._chapter_advanced(ch):
            situation = (f"{situation}\n\n【本章需要推进到】{ch.exit_state}。"
                         f"如果时机合适，请用一个**实质行动**(做出抉择、交出/夺取关键物件、"
                         f"说破一条线索)把局面往这个方向推，而不是反复试探、绕圈子。")

        # 约束：在场者=本章 cast；可引用实体=cast+地点+可用物品
        perceivers = list(ch.cast)
        allowed = list(dict.fromkeys(ch.cast + ch.location_ids + ch.available_items))
        location = ch.location_ids[0] if ch.location_ids else None
        result = self.engine.run_tick(
            target, situation, allowed,
            location_id=location, perceivers=perceivers, tick=tick,
        )

        wb: dict = {}
        revealed: list[str] = []
        chapter_done = False
        if result.committed:
            wb = self._writeback(dilemma, result, tick)
            if result.event_id:
                self.repo.set_event_beat(result.event_id, ch.chapter_id)
            # P5 道具物化：若本拍是真实的"给/递/交"动作 → 落 inventory 转移事件
            self._handle_item_transfer(result.action, ch, target)
            # B3 判"是否推进"：本拍产出新 fact 或带关键抉择 → 重置停滞计数；否则累加。
            ev = self.repo.get_event(result.event_id) if result.event_id else None
            advanced = (len(self.repo.list_facts()) > facts_before) or bool(
                (ev.payload or {}).get("chosen_value") if ev else False)
            self._stall[ch.chapter_id] = 0 if advanced else self._stall.get(ch.chapter_id, 0) + 1
            n = self.repo.count_events_for_beat(ch.chapter_id)
            beats_done = n // K
            # §2 探索驱动揭示：演完 min_scenes 个 beat 后主角撞到真相
            if ch.reveal_gate and beats_done >= ch.min_scenes:
                revealed = self._reveal_for_chapter(ch, tick)
            # §2 戏剧问题驱动收束：beat 演完即为主路径；谓词仅辅助确认
            if self._should_close(ch, n, K):
                self._fulfil_cast(ch)  # §13.5 兑现校验
                ch.status = "done"
                self.repo.upsert_chapter_plan(ch)
                chapter_done = True
        return DirectorStep(
            tick, alarms, dilemma, result, wb,
            chapter_id=ch.chapter_id, chapter_done=chapter_done, revealed=revealed,
        )

    # ================= B1 scripted 模式（大纲驱动：照 beat 写场） =================
    def _scenes_done_for_chapter(self, ch) -> int:
        """本章已落成稿场数（scripted 下 1 场 = 1 个 beat）。靠场的 source_events 的 beat_id 归属。"""
        cnt = 0
        for s in self.repo.list_scenes():
            for eid in s.source_events:
                ev = self.repo.get_event(eid)
                if ev and ev.beat_id == ch.chapter_id:
                    cnt += 1
                    break
        return cnt

    def _step_scripted(self, tick: int) -> DirectorStep:
        from .narration.scene_writer import SceneSpec

        self.repo.decay_emotions()
        ch = self.planner.ensure_chapter()
        if ch is None:
            return DirectorStep(tick, [], None, None)
        if ch.status != "active":
            ch.status = "active"
            self.repo.upsert_chapter_plan(ch)
            self._activate_part_of(ch)
        if not ch.cast:
            return DirectorStep(tick, [], None, None, chapter_id=ch.chapter_id)

        scenes_done = self._scenes_done_for_chapter(ch)
        beats = ch.beat_goals or [ch.exit_state or "推进本章"]
        beat_idx = min(scenes_done, len(beats) - 1)
        beat = beats[beat_idx]
        # POV 跟着节拍走：本场 POV = 这一拍的视角人物（planner 给每个 beat 标了 beat_povs）；
        # 缺省回退章 pov_agent / focus / cast[0]。
        beat_povs = getattr(ch, "beat_povs", None) or []
        pov = (beat_povs[beat_idx] if beat_idx < len(beat_povs) and beat_povs[beat_idx] else "") \
            or getattr(ch, "pov_agent", "") or self._focus_target(ch) or (ch.cast[0] if ch.cast else "")
        if ch.cast and pov not in ch.cast:
            pov = ch.cast[0]
        # 守卫：POV 不能是藏着未揭反派身份的人（防读者从其视角提前泄底）→ 落到首个合格者
        try:
            from .casting import pov_eligible
            hero_id = self.repo.list_personas()[0].agent_id if self.repo.list_personas() else None
            if ch.cast and not pov_eligible(self.repo, pov, hero_id):
                pov = next((a for a in ch.cast if pov_eligible(self.repo, a, hero_id)), pov)
        except Exception:
            pass

        # 组装写作上下文（信息隔离：只喂 POV 账本）
        all_scenes = self.repo.list_scenes()
        prev_tail = (all_scenes[-1].prose_text or "")[-200:] if all_scenes else ""
        avoid = [s.prose_text for s in all_scenes if s.prose_text][-3:]
        # 衔接：本章到此为止已发生（FactExtractor 抽出的事实，按序）+ POV 当前状态
        ch_events = self.repo.events_for_beat(ch.chapter_id)
        chapter_digest = "\n".join(
            f"· {c}" for e in ch_events[-6:]
            if (c := str((e.payload or {}).get("content", "")).strip()))
        pov_state = self._pov_state(pov, ch, ch_events)
        # 本场可揭示：本章 reveal_gate ∩「揭示链 prereq 已满、自身未发现」，且演过 ≥min_scenes 场（公平谜题）
        may_reveal = (self._revealable_now(ch) if scenes_done + 1 >= ch.min_scenes else [])
        spec = SceneSpec(
            pov=pov, beat=beat, chapter=ch, scene_pos=len(all_scenes) + 1,
            pov_known=self.repo.get_agent_ledger(pov),
            reader_known=self.repo.list_reader_knowledge(),
            may_reveal=may_reveal, prev_tail=prev_tail, avoid_repeat=avoid,
            chapter_digest=chapter_digest, pov_state=pov_state,
        )
        prose = self.writer.write(spec)
        # B4 双向控制器：写完先把关（在beat/矛盾/关键场解离反推），不过则带反馈重写一次，再落库。
        if self.controller is not None:
            try:
                ok, fb = self.controller.check(prose, spec, list(ch.cast))
                if not ok and fb:
                    prose = self.writer.write(spec, feedback=fb)
            except Exception:
                pass
        delta = self.extractor.extract(prose, pov, list(ch.cast), spec)
        eids = self.extractor.commit(delta, pov, list(ch.cast), tick, chapter=ch)
        if not eids:
            # 即使没抽出事实，也补 1 个 marker 事件，保证本场被计数、beat 能递进
            eid = f"ev_{uuid.uuid4().hex[:8]}"
            loc = ch.location_ids[0] if ch.location_ids else None
            self.repo.append_event(Event(
                event_id=eid, story_time=tick, actors=[pov], action_type="narrated",
                payload={"content": (prose or "")[:80]}, location_id=loc,
                perceivers=list(ch.cast), beat_id=ch.chapter_id))
            eids = [eid]

        order = len(all_scenes) + 1
        self.repo.insert_scene(Scene(
            scene_id=f"sc_{uuid.uuid4().hex[:8]}", discourse_order=order, source_events=eids,
            pov=pov, target_tension=float(getattr(ch, "target_tension", 0.5) or 0.5),
            prose_text=prose, newly_revealed=list(delta.reveals)))

        scenes_now = scenes_done + 1
        chapter_done = self._should_close_scripted(ch, scenes_now)
        revealed = list(delta.reveals)
        if chapter_done:
            # 收束前补揭示（若 reveal_gate 仍未兑现）
            if ch.reveal_gate and not revealed:
                revealed = self._reveal_for_chapter(ch, tick)
            self._sync_consumed_items(ch)
            self._fulfil_cast(ch)
            ch.status = "done"
            self.repo.upsert_chapter_plan(ch)
            # B3 章末动态细化下一章：用刚写出的真实事实复核下一 planned 章（吸收涌现）
            revise = getattr(self.planner, "revise_next_chapter", None)
            if revise is not None:
                try:
                    revise()
                except Exception:
                    pass
            # W1 渐进细化：演到与某节相关的剧情深处 → 深化该节一次（保守，每节至多一次）
            self._maybe_deepen_world(ch)
            # W5 人物注意力衰减：章末把 related_to/knows 边按章距半衰减（剧情焦点自然浮沉）
            decay = getattr(self.repo, "decay_edges", None)
            if decay:
                try:
                    decay(getattr(ch, "sequence_order", 0))
                except Exception:
                    pass
            if getattr(ch, "sequence_order", 0) and ch.sequence_order % 10 == 0:
                try:
                    from .narration.batch_audit import BatchAuditor
                    BatchAuditor(self.repo, getattr(self.planner, "llm", None)).run(
                        ch.sequence_order, tick=tick)
                except Exception:
                    pass
        return DirectorStep(tick, [], None, None, chapter_id=ch.chapter_id,
                            chapter_done=chapter_done, revealed=revealed)

    # W1：本章冲突类型 → 世界观节的小映射（保守自动触发渐进细化）
    _CONFLICT_SECTION = {
        "潜入任务": "geography", "正面对峙": "geography", "三方搅局": "culture",
        "心理博弈": "culture", "身份危机": "culture", "立场抉择": "culture",
        "情感羁绊": "culture",
    }

    def _maybe_deepen_world(self, ch) -> None:
        """W1 渐进细化（skill 式按需展开）：据本章冲突类型/揭示/角色映射到一个世界观节，
        用本章剧情深化它一次。deepen_section 内部保证每节至多一次、无 LLM 不做。"""
        llm = getattr(self.planner, "llm", None) if self.planner else None
        if llm is None:
            return
        try:
            from .worldbible import deepen_section
        except Exception:
            return
        role = getattr(ch, "role", "") or ""
        if role in ("twist", "climax"):
            sec = "history" if role == "twist" else "culture"
        else:
            sec = self._CONFLICT_SECTION.get(getattr(ch, "conflict_type", "") or "")
        if getattr(ch, "reveal_gate", None):
            sec = "history"
        if not sec:
            sec = {"setup": "geography", "rising": "culture", "twist": "history",
                   "climax": "culture", "resolution": "culture"}.get(role, "")
        if not sec:
            return
        ctx = "；".join(getattr(ch, "beat_goals", []) or [])[:300] or getattr(ch, "dramatic_question", "")
        try:
            deepen_section(self.repo, llm=llm, section=sec, context=ctx)
        except Exception:
            pass

    def _sync_consumed_items(self, ch) -> None:
        gone = {
            it.object_id for it in self.repo.list_inventory()
            if it.acquired_chapter == getattr(ch, "sequence_order", 0)
            and it.status in ("consumed", "destroyed", "sacrificed")
        }
        if not gone:
            return
        ch.items_consumed = list(dict.fromkeys(list(ch.items_consumed or []) + list(gone)))
        ch.items_present = [o for o in (ch.items_present or []) if o not in gone]
        self.repo.upsert_chapter_plan(ch)

    def _pov_state(self, pov: str, ch, ch_events) -> str:
        """POV 当前状态：地点 + 情绪余温 + 最近一次抉择 → 让新场接住情绪/位置/刚做的事。"""
        bits = []
        if getattr(ch, "location_ids", None):
            getter = getattr(self.repo, "get_location", None)
            loc = getter(ch.location_ids[0]) if getter else None
            if loc and loc.name:
                bits.append(f"此刻在「{loc.name}」")
        res = getattr(self.repo, "emotional_residue_text", None)
        emo = res(pov) if res else ""
        if emo and "平静" not in emo:
            bits.append(emo.strip())
        for e in reversed(ch_events):
            cv = str((e.payload or {}).get("chosen_value", "")).strip()
            if cv:
                bits.append(f"刚做出的选择：{cv}")
                break
        return "；".join(bits)

    def _revealable_now(self, ch) -> list[str]:
        """reveal_gate 中此刻**真能揭**的 fact_id：对应揭示链节点 prereq 全已发现、自身尚未发现。
        让 SceneWriter 只写"该揭的"，与 FactExtractor 的 prereq 门控一致（公平谜题不越级）。"""
        if not ch.reveal_gate:
            return []
        nodes = self.repo.list_reveal_nodes()
        discovered = {n.node_id for n in nodes if n.discovered}
        out: list[str] = []
        for fid in ch.reveal_gate:
            cand = [n for n in nodes if n.fact_id == fid and not n.discovered]
            if not cand:
                out.append(fid)  # 无对应节点（自由真相）→ 允许
            elif any(all(p in discovered for p in (n.prereq_node_ids or [])) for n in cand):
                out.append(fid)
        return out

    def _should_close_scripted(self, ch, scenes_done: int) -> bool:
        """scripted 收束（B3）：演满下界(min_scenes) 后——
        · 到上界(target_scenes)→硬收（防失控）；
        · beats 全演完 **且本章有实质推进**(exit_state 朝达成)→收；只演完没推进→继续逼到上界。"""
        min_beats = max(1, ch.min_scenes)
        if scenes_done < min_beats:
            return False
        if scenes_done >= max(min_beats, ch.target_scenes):
            return True
        if ch.beat_goals and scenes_done >= len(ch.beat_goals):
            return self._chapter_advanced(ch)   # B3：beats 演完 + 真推进才收
        return False

    # ---------- §2 收束判定：P1 多角色版本（以 beat 为单位，而非 event） ----------
    def _should_close(self, ch, n_events: int, K: int = 1) -> bool:
        """K = 本章每场 actor 数（P1 多角色），beats_done = n_events // K。"""
        K = max(1, K)
        beats_done = n_events // K
        min_beats = max(1, ch.min_scenes)
        if beats_done < min_beats:              # 下界：防太短
            return False
        # 上界保护（防失控）：到上界无条件收
        if beats_done >= max(min_beats, ch.target_scenes):
            return True
        # P2：predicate 命中即收——撞到真相/做出抉择本身就是"实质推进"，不受推进闸门约束
        if ch.resolution_predicate:
            if self._eval_predicate(ch.resolution_predicate, ch, beats_done, len(ch.beat_goals or [])):
                return True
        # B4 主路径：所有 beat 演完即收。但（问题4 推进闸门）若本章自始至终只是氛围对话、
        # 毫无实质推进，则不在此收束，继续逼到上界（治"重复盘问演满即收却毫无推进"）。
        # repo 不可用（纯逻辑单测）时退化为旧行为：beat 演完即收。
        if ch.beat_goals and beats_done >= len(ch.beat_goals):
            if self.repo is None or self._chapter_advanced(ch):
                return True
            return False
        return False

    def _chapter_advanced(self, ch) -> bool:
        """问题4：本章是否发生过"实质推进"——局面真的改变了，而非只是氛围对话。
        判据（任一）：①带关键抉择(chosen_value)的事件；②本章有道具易手(inventory 转移)；
        ③本章揭示链节点被解锁。普通对话/观察事件不算（每事件都会产 fact，故不以 fact 为据）。"""
        evs = self.repo.events_for_beat(ch.chapter_id)
        # ① 关键抉择
        for e in evs:
            if (e.payload or {}).get("chosen_value"):
                return True
        # ② 道具在本章易手
        try:
            for it in self.repo.list_inventory():
                if it.acquired_chapter == ch.sequence_order and it.status in ("held", "transferred"):
                    return True
        except Exception:
            pass
        # ③ 本章揭示解锁
        if ch.reveal_gate:
            for n in self.repo.list_reveal_nodes():
                if n.discovered and n.fact_id in ch.reveal_gate \
                        and n.discovered_chapter == ch.sequence_order:
                    return True
        return False

    def _eval_predicate(self, pred: str, ch, beats_done: int = 0, total_beats: int = 0) -> bool:
        """收束 DSL：reveal_discovered_fact(fid) | decision_made(agent_id)。
        P2 fix：decision_made 仅在最后一个 beat 开始后才允许触发（防首拍即收）。"""
        if not pred:
            return False
        try:
            fn, _, rest = pred.partition("(")
            arg = rest.rstrip(")").strip()
            fn = fn.strip()
            if fn == "reveal_discovered_fact":
                return any(n.fact_id == arg and n.discovered for n in self.repo.list_reveal_nodes())
            if fn == "decision_made":
                # P2：至少演到最后一个 beat 才允许 decision_made 提前收束
                if total_beats > 0 and beats_done < total_beats - 1:
                    return False
                for e in self.repo.events_for_beat(ch.chapter_id):
                    if arg in (e.actors or []) and (e.payload or {}).get("chosen_value"):
                        return True
                return False
        except Exception:
            return False
        return False

    def _activate_part_of(self, ch) -> None:
        """§11：把本章所属 Part 标为 active（首次演到时），使其章纲从 provisional 转 locked。"""
        arc = self.repo.get_arc(ch.arc_id)
        if arc is None:
            return
        part = self.repo.get_part(arc.part_id)
        if part is not None and part.status == "planned":
            self.repo.set_part_status(part.part_id, "active")
            if self.planner is not None:
                try:
                    # 惰性大纲：本部章纲若尚未生成（非首部），演到时即时生成（用已发生事实）；
                    # 若已生成（首部/已 materialize），则复核 provisional 章（吸收涌现）。
                    if self.planner.ensure_part_chapters(part.part_id) == 0:
                        self.planner.revise_provisional_chapters(part.part_id)
                except Exception:
                    pass

    def _fulfil_cast(self, ch) -> None:
        """§13.5 cast 兑现校验：把声明 cast 收敛为本章事件里**实际行动过**的人，
        未出场者剔除（治根因 C：本章人物写了 X 却整章不出现）。至少保留焦点角色，避免清空。"""
        acted: set[str] = set()
        for e in self.repo.events_for_beat(ch.chapter_id):
            for a in (e.actors or []):
                acted.add(a)
        fulfilled = [a for a in ch.cast if a in acted]
        if not fulfilled:  # 兜底：极端情况下不清空（保留焦点角色或原 cast）
            focus = self._focus_target(ch)
            fulfilled = [focus] if focus else ch.cast
        ch.cast = fulfilled

    def _focus_target(self, ch) -> str | None:
        """在本章 cast 内，按 Arc.focus_agents 权重选焦点角色（主讲谁由 Arc 定）。"""
        if not ch.cast:
            return None
        arc = self.repo.get_arc(ch.arc_id)
        weights = {f["agent_id"]: float(f.get("weight", 0)) for f in (arc.focus_agents if arc else [])}
        # 优先在 cast 中选权重最高者；都无权重则取 cast 第一人
        best = max(ch.cast, key=lambda aid: weights.get(aid, 0.0))
        return best

    def _reveal_for_chapter(self, ch, tick: int) -> list[str]:
        """探索驱动揭示：本章收束时，主角"撞到"reveal_gate 指向的真相。
        把对应揭示链节点标记为已发现，并把该真相写入主角账本（读者侧揭示交叙述层闸门）。"""
        revealed: list[str] = []
        if not ch.reveal_gate:
            return revealed
        hero = self._focus_target(ch)
        for fid in ch.reveal_gate:
            fact = self.repo.get_fact(fid)
            if fact is None:
                continue
            # 解锁揭示链中指向此 fact 的节点
            for node in self.repo.list_reveal_nodes():
                if node.fact_id == fid and not node.discovered:
                    self.repo.mark_node_discovered(node.node_id, ch.sequence_order)
            # 主角获知（探索所得）
            if hero and not self.repo.agent_knows_fact(hero, fid):
                self.repo.insert_knowledge(
                    KnowledgeItem(hero, fid, fact.canonical_content, 0.9, tick, fact.source_event_id)
                )
            # §4.1 知识连续性：主角撞到真相的当下，主动把它推给读者账本（修 payoff 滞后）
            if not self.repo.reader_knows(fid):
                pos = len(self.repo.list_scenes()) + 1
                self.repo.reveal_to_reader(
                    ReaderKnowledge(fid, fact.canonical_content, pos, via_pov=hero)
                )
            revealed.append(fid)
        return revealed

    # ---------- P5 道具/转交物化 ----------
    _GIVE_WORDS = ("给", "递", "交", "转交", "送", "塞", "奉上", "呈", "赠")

    def _handle_item_transfer(self, action, ch, actor: str) -> None:
        """把 agent 真实的"给 B"动作落成 inventory 转移：
          · 收方 = action.target 且属于本章 cast、且非 actor 自己（即角色）
          · 物品 = action.referenced_entities 中 actor 当前真实持有的对象
        只转移 actor 真正持有的物，杜绝"凭空转交白名单外道具"。"""
        intent = action.intent or ""
        if not any(w in intent for w in self._GIVE_WORDS):
            return
        recipient = action.target
        if not recipient or recipient == actor or recipient not in ch.cast:
            return
        # 收方必须是角色（cast 内一律角色），物品来自 referenced_entities
        for oid in (action.referenced_entities or []):
            item = self.repo.get_inventory_item(oid)
            if item is not None and self.repo.agent_holds(actor, oid):
                self.repo.transfer_item(oid, recipient, ch.sequence_order,
                                        note=f"{actor}→{recipient}（{intent}）")
                # 转交后物品在收方手中，并入本章在场台账（叙述白名单）
                if oid not in ch.items_present:
                    ch.items_present.append(oid)
                    self.repo.upsert_chapter_plan(ch)

    # ---------- P1 本场已发生 transcript ----------
    def _format_scene_transcript(self, events) -> str:
        """把本 beat 前几轮的事件转成人类可读摘要，让后续 actor 据此反应。"""
        if not events:
            return ""
        names = {e.entity_id: e.name for e in self.repo.list_entities()}
        lines = []
        for ev in events:
            actor_name = names.get(ev.actors[0], ev.actors[0]) if ev.actors else "某人"
            p = ev.payload or {}
            line = f"· {actor_name}：{ev.action_type}"
            if p.get("dialogue"):
                line += f"，说「{p['dialogue']}」"
            lines.append(line)
        return "\n".join(lines)

    # ---------- 传播：谁在场 ----------
    def _present_agents(self, dilemma: Dilemma) -> list[str]:
        for t in self.repo.list_threads():
            if t.thread_id == dilemma.thread_id:
                return t.involved_agents or [dilemma.target_agent]
        return [dilemma.target_agent]

    # ---------- 二手转述（暴露给 demo/测试主动触发扭曲） ----------
    def gossip(self, speaker: str, listener: str, fact_id: str) -> None:
        self.propagator.tell(speaker, listener, fact_id, self._tick)

    # ---------- 写回 arc_state / cost_ledger / thread ----------
    def _writeback(self, dilemma: Dilemma, result: TickResult, tick: int) -> dict:
        persona = self.repo.get_persona(dilemma.target_agent)
        assert persona is not None
        a, b = dilemma.colliding_pair
        chosen = result.action.chosen_value or ""

        # 判定谁赢谁被牺牲（best-effort：chosen_value 命中哪一边）
        if a and a in chosen:
            won, lost = a, b
        elif b and b in chosen:
            won, lost = b, a
        else:
            won, lost = chosen or a, b  # 无法判定时默认 A 赢

        arc = dict(persona.arc_state)
        arc["last_change_tick"] = tick
        arc["last_chosen_value"] = won
        arc["changed"] = True
        # 反完美：本两难若把弱点放上台面，记一次弱点付出代价
        flaw_pressed = persona.fatal_flaw in (a, b)
        if flaw_pressed:
            arc["last_flaw_cost_tick"] = tick
        self.repo.update_arc_state(persona.agent_id, arc)

        cost_text = f"[tick {tick}] 为守住「{won}」，牺牲了「{lost}」"
        self.repo.append_cost(persona.agent_id, cost_text)

        # §4.2 情绪余温：依抉择性质给角色留一股可跨场传递的情绪
        if flaw_pressed:
            emo, inten = "动摇", 0.7
        elif lost and lost != won:
            emo, inten = "沉重", 0.6
        else:
            emo, inten = "坚定", 0.45
        self.repo.bump_emotion(persona.agent_id, emo, inten,
                               cause=f"牺牲了「{lost}」" if lost and lost != won else f"守住「{won}」",
                               tick=tick)

        # 推进 thread 张力
        if dilemma.thread_id:
            threads = {t.thread_id: t for t in self.repo.list_threads()}
            t = threads.get(dilemma.thread_id)
            if t:
                new_tension = min(1.0, round(t.current_tension + 0.2 * dilemma.score, 3))
                self.repo.update_thread_tension(t.thread_id, new_tension, tick)

        return {"won": won, "lost": lost, "flaw_pressed": flaw_pressed, "cost": cost_text}
