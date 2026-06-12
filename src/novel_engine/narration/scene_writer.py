"""B1 SceneWriter（大纲驱动重构脚手架）：照 beat **直接写正文**，而非渲染已模拟事件。

输入 = 一个 beat + POV 可见信息 + 约束（在场/道具/可揭示/篇幅/上一场尾/不得重复），
输出 = 本场散文。受信息隔离（只喂 POV 账本）、文风/时代隔离墙、反升华/视角红线约束。

B1-a 阶段：独立组件，单测可跑通；尚未接入 director（接线在 B1-b）。
复用：narrator 的 `_strip_scene_headers`/`_clean_ids`/`_gram_overlap`、tone/style 闸门、B0.5 反升华。
双层解码：创作层用高温（complete_at temperature≈0.9）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..llm.base import LLMClient
from ..models import ChapterPlan, KnowledgeItem, ReaderKnowledge
from ..repository import Repository
from .narrator import _clean_ids, _gram_overlap, _lead_clause, _strip_scene_headers
from .retrieval import build_context, scene_seeds

WRITE_TEMPERATURE = 0.9  # 创作层高温，抓灵气


@dataclass
class SceneSpec:
    """写一场所需的全部输入（B1-b 由 director 组装）。"""

    pov: str
    beat: str
    chapter: ChapterPlan
    scene_pos: int = 0
    pov_known: list[KnowledgeItem] = field(default_factory=list)
    reader_known: list[ReaderKnowledge] = field(default_factory=list)
    may_reveal: list[str] = field(default_factory=list)
    prev_tail: str = ""
    avoid_repeat: list[str] = field(default_factory=list)
    chapter_digest: str = ""   # 本章到此为止已发生（滚动梗概，本场须紧接其后）
    pov_state: str = ""        # POV 当前状态：地点/情绪余温/最近抉择（接续连续性）


class SceneWriter:
    def __init__(self, repo: Repository, llm: LLMClient | None = None) -> None:
        self.repo = repo
        self.llm = llm

    # ---------- 篇幅 ----------
    def _word_target(self, ch: ChapterPlan) -> int:
        tw = int(getattr(ch, "target_words", 0) or 0)
        ts = max(1, int(getattr(ch, "target_scenes", 3) or 3))
        return tw // ts if tw else 0

    def _names(self) -> dict[str, str]:
        m = {p.agent_id: p.name for p in self.repo.list_personas()}
        for e in self.repo.list_entities():
            m.setdefault(e.entity_id, e.name)
        return m

    # ---------- Fix A：世界观 / 地点 / 前后文 / 身份称谓 上下文 ----------
    def _rag_context(self, ch, pov: str, beat_text: str = "") -> str:
        """W6 RAG 注入：基于知识图谱子图检索，取代零散的 _world_bible + _geo。"""
        seeds = scene_seeds(ch, pov)
        # 从 cast 的 faction_id 也加入种子（势力上下文）
        for aid in list(seeds):
            ent = None
            for e in self.repo.list_entities():
                if e.entity_id == aid:
                    ent = e
                    break
            if ent and ent.type == "character":
                fid = (ent.attributes or {}).get("faction_id")
                if fid:
                    seeds.add(fid)
        return build_context(self.repo, seeds, budget=3000, beat_text=beat_text)

    def _world_bible(self) -> str:
        """兼容旧调用路径（W6 前）；新路径走 _rag_context。"""
        return build_context(self.repo, set(), budget=2000)

    def _geo(self, ch) -> str:
        if not getattr(ch, "location_ids", None):
            return ""
        getter = getattr(self.repo, "get_location", None)
        loc = getter(ch.location_ids[0]) if getter else None
        if not loc:
            return ""
        bits = []
        if getattr(loc, "summary", ""):
            bits.append(f"〔速览〕{loc.summary}")
        if getattr(loc, "detail", "") or loc.geo_full:
            bits.append(getattr(loc, "detail", "") or loc.geo_full)
        if getattr(loc, "culture_local", ""):
            bits.append(f"〔风土〕{loc.culture_local}")
        return "\n".join(bits)

    def _arc_context(self, ch) -> str:
        bits = []
        arc = self.repo.get_arc(ch.arc_id) if getattr(ch, "arc_id", None) else None
        if arc:
            if arc.summary:
                bits.append(f"本卷：{arc.summary}")
            part = self.repo.get_part(arc.part_id) if arc.part_id else None
            if part and part.goal:
                bits.append(f"本部目标：{part.goal}")
        return "；".join(bits)

    def _prior_digest(self, ch, limit: int = 4) -> str:
        """前情梗概：本章之前已写章的摘要/目标，给跨章连续性（治"没前后文"）。"""
        seq = getattr(ch, "sequence_order", 0) or 0
        prior = [c for c in self.repo.list_chapter_plans()
                 if (c.sequence_order or 0) < seq and c.status == "done"]
        prior.sort(key=lambda c: c.sequence_order or 0)
        lines = []
        latest_audit = getattr(self.repo, "latest_batch_audit", lambda **_: None)(
            before_chapter=seq)
        if latest_audit and latest_audit.summary_json:
            summary = latest_audit.summary_json
            parts = []
            for key, label in (("plot", "剧情"), ("foreshadow", "伏笔"), ("character", "人物")):
                if summary.get(key):
                    parts.append(f"{label}:{str(summary[key])[:120]}")
            if parts:
                lines.append(f"十章压缩摘要（至第 {latest_audit.chapter_seq} 章）：{'；'.join(parts)}")
        for c in prior[-limit:]:
            s = (c.summary or "；".join(c.beat_goals or [])[:60]).strip()
            if s:
                lines.append(f"第{c.sequence_order}章：{s[:80]}")
        return "\n".join(lines)

    def _character_log_block(self, ch, cast_ids: list[str], pov: str) -> str:
        get_logs = getattr(self.repo, "get_character_logs", None)
        if get_logs is None:
            return ""
        seq = int(getattr(ch, "sequence_order", 0) or 0)
        names = self._names()
        lines: list[str] = []
        for aid in dict.fromkeys([pov] + list(cast_ids or [])):
            logs = get_logs(aid, last_n=5, before_chapter=seq)
            if not logs:
                continue
            lines.append(f"{names.get(aid, aid)}：")
            for log in logs:
                bits = []
                if log.actions:
                    bits.append(f"行为：{log.actions}")
                if log.psychology:
                    bits.append(f"心理：{log.psychology}")
                if log.intention:
                    bits.append(f"下一步意图：{log.intention}")
                if bits:
                    lines.append(f"- 第 {log.chapter_seq} 章：" + " / ".join(bits))
        return "\n".join(lines)

    def _identity_block(self, cast_ids: list[str], pov: str, pov_known) -> str:
        """称谓约束：隐藏身份/化名未被 POV 知晓时，只能用 public 称呼（治"该用化名却叫真名"）。
        身份存 entity.attributes['identity']={public,true,fact_id}。POV 知道 fact_id 才可用真名。"""
        ent_by_id = {e.entity_id: e for e in self.repo.list_entities()}
        pov_fids = {getattr(k, "fact_id", None) for k in (pov_known or [])}
        reader_fids = {getattr(r, "fact_id", None) for r in self.repo.list_reader_knowledge()}
        lines = []
        for aid in dict.fromkeys(list(cast_ids) + [pov]):
            ent = ent_by_id.get(aid)
            idn = (ent.attributes or {}).get("identity") if ent else None
            if not idn:
                continue
            public, true, fid = idn.get("public"), idn.get("true"), idn.get("fact_id")
            known = (fid in pov_fids) or (fid in reader_fids) or (aid == pov)
            if aid == pov:
                # 主角自己是 POV：限制性叙述（他的视角）可用真名；但**别的角色当面称呼他时必须用化名**
                if public and true:
                    lines.append(
                        f"- POV={true}（真名）对外化名「{public}」：叙述（他的视角）可称真名「{true}」；"
                        f"但**其他任何角色当面称呼/提到他时，一律用化名「{public}」**——他们不知道他的真名，"
                        f"绝不可让别人叫出「{true}」。他的内心独白用真名「{true}」。")
            elif known and true:
                lines.append(f"- {public}：其真实身份已对 POV 揭示，可称「{true}」。")
            elif public:
                lines.append(f"- {public}：真实身份尚未揭示，只能用「{public}」这一公开称呼，不得擅自叫破其真名/真实头衔。")
        return "\n".join(lines)

    def write(self, spec: SceneSpec, feedback: str = "") -> str:
        if self.llm is None:
            return self._offline(spec)
        names = self._names()
        pov_name = names.get(spec.pov, spec.pov)
        ch = spec.chapter

        tone = ""
        for getter in ("tone_profile_prompt", "style_skill_prompt"):
            fn = getattr(self.repo, getter, None)
            if fn:
                try:
                    block = fn()
                except Exception:
                    block = ""
                if block:
                    tone += block + "\n\n"

        cast_names = "、".join(names.get(a, a) for a in (ch.cast or [])) or "（无）"
        _item_exists = getattr(self.repo, "item_exists", None)
        items = "、".join(n for o in (ch.items_present or [])
                          if (n := names.get(o, o)) and not n.isascii()
                          and (_item_exists is None or _item_exists(o))) or "（无）"
        known = "\n".join(f"- {k.version_content}" for k in spec.pov_known) or "（空）"
        reader_known = "\n".join(f"- {r.revealed_version}" for r in spec.reader_known) or "（读者尚一无所知）"
        may_reveal = "、".join(spec.may_reveal) or "（无）"

        # W6 RAG：按本场 cast/地点/势力 + beat 关键词检索相关子图注入
        bible = self._rag_context(ch, spec.pov, beat_text=spec.beat)
        bible_block = (
            f"[世界观·必须据此理解所有设定]\n{bible}\n"
            "其中的专有名词与比喻必须**按设定理解、不得望文生义**"
            "（例如『孤岛』指被沦陷区包围、孤悬的租界，**不是真的海岛**）。\n\n" if bible else "")
        system = (
            tone
            + bible_block
            + f"你是小说叙述者，视角=限制性第三人称，POV={pov_name}。"
            "你只知道 POV 此刻所知 + 读者已知，**不得透露 POV 不知道的事**，不得写其他人物的内心。\n"
            "[反升华红线] 禁止在结尾总结寓意/点题/升华主题；结尾停在具体动作或物象上，把意义留给读者。\n"
            "只输出散文，不要解释、不要加任何标题或「第N场/第N章」标号。"
        )
        w = self._word_target(ch)
        geo, arc_ctx = self._geo(ch), self._arc_context(ch)
        prior = self._prior_digest(ch)
        char_logs = self._character_log_block(ch, list(ch.cast or []), spec.pov)
        idn = self._identity_block(list(ch.cast or []), spec.pov, spec.pov_known)
        user_parts = []
        if arc_ctx:
            user_parts.append(f"[本章在故事中的位置]{arc_ctx}")
        if prior:
            user_parts.append(f"[前情梗概（已写过的，本场须承接、不要与之矛盾）]\n{prior}")
        if char_logs:
            user_parts.append(f"[人物近期轨迹（按章累积；本场必须承接行为、心理和下一步意图）]\n{char_logs}")
        if spec.chapter_digest:
            user_parts.append(f"[本章已发生（按时间顺序；本场要**紧接最后一条**往下写，不是另起炉灶）]\n{spec.chapter_digest}")
        if spec.pov_state:
            user_parts.append(f"[POV={pov_name} 当前状态（位置/情绪/刚做的事，本场须连上）]\n{spec.pov_state}")
        user_parts += [
            f"[★本场要演的这一拍·必须完整落地]\n{spec.beat}\n"
            "——这一拍**就是本场的核心剧情**：其中点到的每一个动作、地点、物件、对话、转折都要"
            "写进正文，不能只取氛围而丢掉实质；要把这一拍演完整、演到位，结尾留下它该有的转折/麻烦。",
            f"[本章戏剧问题]{ch.dramatic_question or '（无）'}　[冲突类型]{ch.conflict_type or '（无）'}",
        ]
        if geo:
            user_parts.append(f"[所在之地（调用其中真实环境细节，不要另造地理）]\n{geo}")
        user_parts += [
            f"[POV={pov_name} 此刻所知]\n{known}",
            f"[读者已知]\n{reader_known}",
            f"[在场角色（仅这些，人称与各自性别一致）]{cast_names}",
        ]
        if idn:
            user_parts.append(f"[在场角色称谓·必须遵守（化名/隐藏身份未揭示前不得叫破真名）]\n{idn}")
        user_parts += [
            f"[可提及器物（仅这些，不得凭空添置）]{items}",
            f"[本场可揭示的线索/真相]{may_reveal}",
        ]
        if spec.prev_tail:
            user_parts.append(
                f"[承接上一场·**无缝接续**]上一场结尾：「…{spec.prev_tail[-180:]}」\n"
                "本场从这里**直接往下写**：时间、地点、在场人物、情绪都要连上；"
                "**不得**重新交代背景、不得让人物'重新登场/初次出现'、不得无故回到已离开的地点、时间不得倒流。")
        if spec.avoid_repeat:
            joined = "\n— — —\n".join(s[:90] for s in spec.avoid_repeat[-3:])
            user_parts.append(f"[不得重复以下近场画面/动作（换意象/比喻/句式/切入点）]\n{joined}")
        if w:
            user_parts.append(f"[篇幅]约 {w} 字为宜，宁短勿注水，不要超过 {int(w * 1.3)} 字。")
        # 尾部铁律加固（对抗 Lost-in-Middle）
        user_parts.append(f"[★本场铁律·最后再确认]在场仅限 {cast_names}；可提及器物仅限 {items}；"
                          f"只写 {pov_name} 所知；结尾不得升华点题。")
        if feedback:
            user_parts.append(f"[上一稿未通过校验，请按以下意见重写]\n{feedback}")
        user = "\n\n".join(user_parts)

        prose = self.llm.complete_at(system, user, WRITE_TEMPERATURE).strip()
        prose = self._post_gate(prose, spec, lambda fb: self.llm.complete_at(system, user + f"\n\n[重写意见]{fb}", WRITE_TEMPERATURE).strip())
        return _clean_ids(_strip_scene_headers(prose))

    # ---------- 渲染后闸门（复用 B0.5/B0 的 gate 函数）----------
    def _post_gate(self, prose: str, spec: SceneSpec, render, max_retry: int = 2) -> str:
        from ..style_skill import style_metric_gate
        from ..tone import emotion_ratio_gate, tone_gate

        used = 0
        tension = float(getattr(spec.chapter, "target_tension", 0.5) or 0.5)
        profile = self.repo.get_tone_profile()
        if profile.is_set() and used < max_retry:
            ok, fb = tone_gate(prose, profile, self.llm)
            if not ok:
                prose = render(fb); used += 1
        if used < max_retry:
            ok, fb = emotion_ratio_gate(prose, tension)
            if not ok:
                prose = render(fb); used += 1
        if used < max_retry:
            sp = self.repo.get_style_skill()
            if sp.is_set():
                ok, fb = style_metric_gate(prose, sp)
                if not ok:
                    prose = render(fb); used += 1
        # 去重：与 avoid_repeat 整场 6-gram 重合过高 → 重写
        if used < max_retry and spec.avoid_repeat:
            for prev in spec.avoid_repeat[-3:]:
                if _gram_overlap(prose, prev, n=6) >= 0.18:
                    prose = render("与近场画面/措辞重复过高，请换意象、比喻、句式与切入点重写。")
                    used += 1
                    break
        # 篇幅过长压缩
        if used < max_retry:
            w = self._word_target(spec.chapter)
            if w and len(prose) > int(w * 1.3):
                prose = render(f"过长，请精简到约 {w} 字：删去与推进无关的环境/感官堆叠与连用比喻。")
                used += 1
        return prose

    def _offline(self, spec: SceneSpec) -> str:
        """无 LLM 的确定性兜底：把 beat 落成一句最小可读正文（供离线测试/流水线跑通）。"""
        names = self._names()
        pov = names.get(spec.pov, spec.pov)
        loc = ""
        if spec.chapter.location_ids:
            getter = getattr(self.repo, "get_location", None)
            l = getter(spec.chapter.location_ids[0]) if getter else None
            loc = (l.name if l else "")
        head = f"{pov}站在{loc}。" if loc else f"{pov}停下脚步。"
        return head + spec.beat.strip()
