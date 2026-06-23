"""B1 SceneWriter：按 beat 直接写正文。"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from ..llm.base import LLMClient
from ..models import ChapterPlan, KnowledgeItem, ReaderKnowledge
from ..prompt_addons import ANTI_AI_FLAVOR_GUIDANCE
from ..repository import Repository
from ._text_utils import _clean_ids, _gram_overlap, _strip_scene_headers
from .retrieval import build_context, scene_seeds

WRITE_TEMPERATURE = 0.9


@dataclass
class SceneSpec:
    """写一场所需的全部输入。"""

    pov: str
    beat: str
    chapter: ChapterPlan
    scene_pos: int = 0
    pov_known: list[KnowledgeItem] = field(default_factory=list)
    reader_known: list[ReaderKnowledge] = field(default_factory=list)
    may_reveal: list[str] = field(default_factory=list)
    thread_decisions: list[dict] = field(default_factory=list)
    prev_tail: str = ""
    avoid_repeat: list[str] = field(default_factory=list)
    chapter_digest: str = ""
    pov_state: str = ""
    current_beat: str = ""
    remaining_beats_locked: list[str] = field(default_factory=list)
    future_chapters_locked: list[dict] = field(default_factory=list)
    no_new_named_character: bool = True
    no_new_location: bool = True
    no_new_investigation_result: bool = True


class SceneWriter:
    def __init__(self, repo: Repository, llm: LLMClient | None = None) -> None:
        self.repo = repo
        self.llm = llm

    def _word_target(self, ch: ChapterPlan) -> int:
        tw = int(getattr(ch, "target_words", 0) or 0)
        ts = max(1, int(getattr(ch, "target_scenes", 3) or 3))
        return tw // ts if tw else 0

    def _names(self) -> dict[str, str]:
        names = {}
        for p in self.repo.list_personas():
            display = getattr(self.repo, "get_character_display_name", lambda aid, name: name)(p.agent_id, p.name)
            names[p.agent_id] = display
        for e in self.repo.list_entities():
            names.setdefault(e.entity_id, e.name)
        return names

    def _rag_context(
        self,
        ch: ChapterPlan,
        pov: str,
        beat_text: str = "",
        allowed_entity_ids: set[str] | None = None,
    ) -> str:
        # 写作严格态：传 chapter_seq + exclude_future + 本章 allowed 白名单，
        # 启用 build_context 的脱敏与可见性闸门——杜绝小传/未来道具进正文 prompt。
        seeds = scene_seeds(ch, pov)
        return build_context(
            self.repo,
            seeds,
            budget=3000,
            beat_text=beat_text,
            chapter_seq=getattr(ch, "sequence_order", None),
            exclude_future=True,
            allowed_entity_ids=allowed_entity_ids if allowed_entity_ids is not None else set(seeds),
        )

    def _world_bible(self) -> str:
        return build_context(self.repo, set(), budget=2000)

    def _geo(self, ch: ChapterPlan) -> str:
        if not getattr(ch, "location_ids", None):
            return ""
        getter = getattr(self.repo, "get_location", None)
        loc = getter(ch.location_ids[0]) if getter else None
        if not loc:
            return ""
        bits: list[str] = []
        if getattr(loc, "summary", ""):
            bits.append(f"〔速览〕{loc.summary}")
        if getattr(loc, "detail", "") or getattr(loc, "geo_full", ""):
            bits.append(getattr(loc, "detail", "") or getattr(loc, "geo_full", ""))
        if getattr(loc, "culture_local", ""):
            bits.append(f"〔风土〕{loc.culture_local}")
        return "\n".join(bit for bit in bits if bit)

    def _arc_context(self, ch: ChapterPlan) -> str:
        bits: list[str] = []
        arc = self.repo.get_arc(ch.arc_id) if getattr(ch, "arc_id", None) else None
        if arc:
            if getattr(arc, "summary", ""):
                bits.append(f"本卷：{arc.summary}")
            part = self.repo.get_part(arc.part_id) if getattr(arc, "part_id", "") else None
            if part and getattr(part, "goal", ""):
                bits.append(f"本部目标：{part.goal}")
        return "；".join(bits)

    def _prior_digest(self, ch: ChapterPlan, limit: int = 4) -> str:
        seq = getattr(ch, "sequence_order", 0) or 0
        prior = [c for c in self.repo.list_chapter_plans() if (c.sequence_order or 0) < seq and c.status == "done"]
        prior.sort(key=lambda c: c.sequence_order or 0)
        lines: list[str] = []
        latest_audit = getattr(self.repo, "latest_batch_audit", lambda **_: None)(before_chapter=seq)
        if latest_audit and getattr(latest_audit, "summary_json", None):
            summary = latest_audit.summary_json
            parts: list[str] = []
            for key, label in (("plot", "剧情"), ("foreshadow", "伏笔"), ("character", "人物")):
                if summary.get(key):
                    parts.append(f"{label}:{str(summary[key])[:120]}")
            if parts:
                lines.append(f"十章压缩摘要（至第 {latest_audit.chapter_seq} 章）：{'；'.join(parts)}")
        for c in prior[-limit:]:
            summary = (c.summary or "；".join(c.beat_goals or [])[:60]).strip()
            if summary:
                lines.append(f"第{c.sequence_order}章：{summary[:80]}")
        return "\n".join(lines)

    def _character_log_block(self, ch: ChapterPlan, cast_ids: list[str], pov: str) -> str:
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
                bits: list[str] = []
                if getattr(log, "actions", ""):
                    bits.append(f"行为：{log.actions}")
                if getattr(log, "psychology", ""):
                    bits.append(f"心理：{log.psychology}")
                if getattr(log, "intention", ""):
                    bits.append(f"下一步意图：{log.intention}")
                if bits:
                    lines.append(f"- 第 {log.chapter_seq} 章：" + " / ".join(bits))
        return "\n".join(lines)

    def _identity_block(self, cast_ids: list[str], pov: str, pov_known: list[KnowledgeItem]) -> str:
        ent_by_id = {e.entity_id: e for e in self.repo.list_entities()}
        pov_fids = {getattr(k, "fact_id", None) for k in (pov_known or [])}
        reader_fids = {getattr(r, "fact_id", None) for r in self.repo.list_reader_knowledge()}
        lines: list[str] = []
        for aid in cast_ids:
            ent = ent_by_id.get(aid)
            if not ent:
                continue
            identity = (ent.attributes or {}).get("identity") or {}
            public = identity.get("public")
            true = identity.get("true")
            fid = identity.get("fact_id")
            known = (fid in pov_fids) or (fid in reader_fids) or (aid == pov)
            if aid == pov and public and true:
                lines.append(
                    f"- POV={true}（真名）对外化名「{public}」：叙述（他的视角）可称真名「{true}」；"
                    f"但其他任何角色当面称呼/提到他时，一律用化名「{public}」。"
                )
            elif known and public and true:
                lines.append(f"- {public}：其真实身份已对 POV 揭示，可称「{true}」。")
            elif public:
                lines.append(f"- {public}：真实身份尚未揭示，只能用「{public}」这一公开称呼。")
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
        item_exists = getattr(self.repo, "item_exists", None)
        items = "、".join(
            n for o in (ch.items_present or [])
            if (n := names.get(o, o)) and (item_exists is None or item_exists(o))
        ) or "（无）"
        known = "\n".join(f"- {k.version_content}" for k in spec.pov_known) or "（空）"
        reader_known = "\n".join(f"- {r.revealed_version}" for r in spec.reader_known) or "（读者尚一无所知）"
        may_reveal = "、".join(spec.may_reveal) or "（无）"

        bible = self._rag_context(ch, spec.pov, beat_text=spec.beat)
        bible_block = (
            f"[世界观·必须据此理解所有设定]\n{bible}\n"
            "其中的专有名词与比喻必须按设定理解、不得望文生义"
            "（例如『孤岛』指被沦陷区包围、孤悬的租界，不是真的海岛）。"
            if bible else ""
        )

        prior = self._prior_digest(ch)
        arc_ctx = self._arc_context(ch)
        geo = self._geo(ch)
        char_logs = self._character_log_block(ch, list(ch.cast or []), spec.pov)
        idn = self._identity_block(list(ch.cast or []), spec.pov, spec.pov_known)

        # === 稳定前缀（system）===
        # 只放 tone/style（项目级）+ 固定叙述规则（常量）。全项目每次调用逐字节一致，
        # 命中 DeepSeek 自动前缀缓存。视角人名与动态世界观检索一律不进 system，
        # 否则前缀漂移、后面的固定规则每次都得重算（旧实现的缓存杀手）。
        system = (
            tone
            + "你是小说叙述者，视角=限制性第三人称。"
            "只写 POV 此刻所知 + 读者已知，不得透露 POV 不知道的事，不得写其他人物的内心。\n"
            "[反升华红线] 禁止在结尾总结寓意/点题/升华主题；结尾停在具体动作或物象上，把意义留给读者。\n"
            "[段落节奏] 每段控制在 3 句左右、不要写成一大坨；即便是没有对白的描写或独角戏，"
            "也要用动作、感官、停顿把它切成短段，一段聚焦一个动作或一个画面。对白各自成段。\n"
            "只输出散文，不要解释、不要加任何标题或第N场/第N章标号。\n"
            + ANTI_AI_FLAVOR_GUIDANCE
        )

        # === 变量后缀（user）===
        # 先放「本章级」块（同章多场之间一致 → 命中章内前缀缓存），再放「本场级」易变块；
        # feedback / 重写意见永远在最后（重写只往后缀追加，复用首稿前缀）。
        user_parts: list[str] = [f"[本场视角]POV={pov_name}"]
        # —— 本章级（同章 3 场之间一致）——
        if arc_ctx:
            user_parts.append(f"[本章在故事中的位置]{arc_ctx}")
        if char_logs:
            user_parts.append(f"[人物近期轨迹（按章累积；本场必须承接行为、心理和下一步意图）]\n{char_logs}")
        if prior:
            user_parts.append(f"[前情梗概（已写过的，本场须承接、不要与之矛盾）]\n{prior}")
        if geo:
            user_parts.append(f"[所在之地（调用其中真实环境细节，不要另造地理）]\n{geo}")
        user_parts.append(f"[在场角色（仅这些，人称与各自性别一致）]{cast_names}")
        if idn:
            user_parts.append(f"[在场角色称谓·必须遵守]\n{idn}")
        user_parts.append(f"[可提及器物（仅这些，不得凭空添置）]{items}")
        user_parts.append(f"[本章戏剧问题]{ch.dramatic_question or '（无）'}　[冲突类型]{ch.conflict_type or '（无）'}")
        if getattr(ch, "time_hint", ""):
            user_parts.append(
                f"[本章时间·故事时钟]{ch.time_hint}。"
                "正文里的钟点/时段必须与之一致：时间只能往后走，不得回到更早的时刻。")
        w = self._word_target(ch)
        if w:
            user_parts.append(f"[篇幅]约 {w} 字为宜，宁短勿注水，不要超过 {int(w * 1.3)} 字。")
        # —— 本场级（每场不同）——
        if bible_block:
            user_parts.append(bible_block)
        user_parts.append(f"[POV={pov_name} 此刻所知]\n{known}")
        user_parts.append(f"[读者已知]\n{reader_known}")
        user_parts.append(f"[本场可揭示的线索/真相]{may_reveal}")
        user_parts.append(
            f"[本场要演的这一拍·必须完整落地]\n{spec.beat}\n"
            "这一拍就是本场的核心剧情，其中点到的动作、地点、物件、对话、转折都要真正写进正文。"
        )
        if spec.pov_state:
            user_parts.append(f"[POV={pov_name} 当前状态]\n{spec.pov_state}")
        if spec.chapter_digest:
            user_parts.append(f"[本章已发生（按时间顺序；本场要紧接最后一条往下写）]\n{spec.chapter_digest}")
        if spec.prev_tail:
            user_parts.append(
                f"[承接上一场·无缝接续]上一场结尾：「…{spec.prev_tail[-180:]}」\n"
                "本场从这里直接往下写：时间、地点、在场人物、情绪都要连上。"
            )
        if spec.avoid_repeat:
            joined = "\n— — —\n".join(s[:90] for s in spec.avoid_repeat[-3:])
            user_parts.append(f"[不得重复以下近场画面/动作]\n{joined}")
        if feedback:
            user_parts.append(f"[上一稿未通过校验，请按以下意见重写]\n{feedback}")

        user = "\n\n".join(user_parts)
        prose = self.llm.complete_at(system, user, WRITE_TEMPERATURE).strip()
        prose = self._post_gate(prose, spec, lambda fb: self.llm.complete_at(system, user + f"\n\n[重写意见]{fb}", WRITE_TEMPERATURE).strip())
        return _clean_ids(_strip_scene_headers(prose))

    def _post_gate(self, prose: str, spec: SceneSpec, render, max_retry: int = 2) -> str:
        used = 0
        if used < max_retry and spec.avoid_repeat:
            for prev in spec.avoid_repeat[-3:]:
                if _gram_overlap(prose, prev, n=6) >= 0.18:
                    prose = render("与近场画面/措辞重复过高，请换意象、比喻、句式与切入点重写。")
                    used += 1
                    break
        if used < max_retry:
            w = self._word_target(spec.chapter)
            if w and len(prose) > int(w * 1.3):
                prose = render(f"过长，请精简到约 {w} 字：删去与推进无关的环境/感官堆叠。")
                used += 1
        if used < max_retry and self._requires_system_broadcasts():
            ok, fb = self._system_broadcast_gate(prose)
            if not ok:
                prose = render(fb)
        return prose

    def _requires_system_broadcasts(self) -> bool:
        profile = self.repo.get_tone_profile()
        hay = " ".join([
            getattr(profile, "genre", "") or "",
            getattr(profile, "primary_effect", "") or "",
            getattr(profile, "register", "") or "",
            getattr(profile, "sentence_rhythm", "") or "",
        ])
        return any(key in hay for key in ("xuanhuan_powerfantasy", "系统", "播报", "黑化值", "进度条"))

    @staticmethod
    def _system_broadcast_gate(prose: str) -> tuple[bool, str]:
        text = prose or ""
        if not text.strip():
            return True, ""
        needed = max(1, math.ceil(len(text) / 500))
        got = len(re.findall(r"(?:叮——|【)", text))
        if got >= needed:
            return True, ""
        return False, (
            f"系统播报频次不足：正文约 {len(text)} 字，需要至少 {needed} 次以“叮——”或“【”开头的系统插话；"
            f"当前只有 {got} 次。请在不新增剧情事件的前提下重写。"
        )

    def _offline(self, spec: SceneSpec) -> str:
        names = self._names()
        pov = names.get(spec.pov, spec.pov)
        loc = ""
        if spec.chapter.location_ids:
            getter = getattr(self.repo, "get_location", None)
            l = getter(spec.chapter.location_ids[0]) if getter else None
            loc = l.name if l else ""
        head = f"{pov}站在{loc}。" if loc else f"{pov}停下脚步。"
        return head + spec.beat.strip()
