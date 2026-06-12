"""叙述/渲染 Agent（设计文档 §6.3 + §4.5）。

叙述者被刻意"变笨"：上下文 = POV账本 + 读者账本 + style_bible + 前情摘要 +
本场events + 可用 motif/mannerisms。**不得透露 POV 不知道的事**。

两种渲染：
  - 离线模板（llm=None）：确定性、具体、不点名情绪 → 让流水线可离线跑通并过反抽象闸门。
  - LLM（§6.3）：把同样的材料交给模型写散文。
"""
from __future__ import annotations

import re

from ..llm.base import LLMClient
from ..models import Event
from ..repository import Repository
from .style import STYLE_BIBLE


_LEAD_PUNCT = "。！？，；、—…\n"

# P4d：清除引擎内部 id 标识符，防止泄漏进正文
_ID_RE = re.compile(r'\b(?:obj|p|cast|loc|ev)_[A-Za-z0-9_]+')


def _clean_ids(text: str) -> str:
    return _ID_RE.sub('', text)


def _gram_overlap(a: str, b: str, n: int = 6) -> float:
    """两段文本 n-gram（字符）的 Jaccard 重合度，用于句级去重。"""
    a = "".join(a.split())
    b = "".join(b.split())
    if len(a) < n or len(b) < n:
        return 0.0
    ga = {a[i:i + n] for i in range(len(a) - n + 1)}
    gb = {b[i:i + n] for i in range(len(b) - n + 1)}
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)


def _lead_clause(text: str, limit: int = 16) -> str:
    """取开场首个分句（到第一个标点为止），用于检测"每场都以同一意象/句式开头"。"""
    s = (text or "").lstrip()
    out = []
    for ch in s[:limit]:
        if ch in _LEAD_PUNCT:
            break
        out.append(ch)
    return "".join(out).strip()


def _lead_overlap(a: str, b: str) -> float:
    """两个开场首分句的字符集 Jaccard；前缀相同也算高重合（治"雾在涨…"反复开头）。"""
    la, lb = _lead_clause(a), _lead_clause(b)
    if not la or not lb:
        return 0.0
    # 一方是另一方前缀（≥3 字）→ 视作高度重合
    if len(la) >= 3 and len(lb) >= 3 and (la.startswith(lb) or lb.startswith(la)):
        return 1.0
    sa, sb = set(la), set(lb)
    return len(sa & sb) / len(sa | sb)


_DIALOGUE_RE = re.compile(r"[「『\"“]([^」』\"”]{4,})[」』\"”]")


def _extract_dialogue(text: str) -> list[str]:
    """抽取正文里「」『』""内的对白（≥4 字，滤掉短叹词）。"""
    return [m.strip() for m in _DIALOGUE_RE.findall(text or "") if len(m.strip()) >= 4]


def _dialogue_overlap(prose: str, recent: list[str], thresh: float = 0.5) -> tuple[bool, str]:
    """对白级去重：本场任一句对白与近场任一句对白 6-gram Jaccard ≥ thresh → 判重复台词。
    治"同一句登场/钩子台词在相邻场各说一遍"（agent 在相似处境重复同一句）。"""
    cur = _extract_dialogue(prose)
    prev_lines = [d for prev in recent for d in _extract_dialogue(prev)]
    for c in cur:
        for p in prev_lines:
            # 完全相同或高度重合都算
            if c == p or _gram_overlap(c, p, n=6) >= thresh:
                return False, (f"有一句对白与前文几乎一样（「{c[:16]}…」），"
                               f"同一个人不要在相邻场景重复说同一句话；若该意思必须再现，请换一种说法或改为动作/沉默。")
    return True, ""


def _split_paragraphs(text: str, min_len: int = 20) -> list[str]:
    """按空行/换行切段，过滤过短段（短句偶然重合不算搬运）。"""
    parts = re.split(r"\n\s*\n|\n", text or "")
    return [p.strip() for p in parts if len(p.strip()) >= min_len]


def _para_overlap(prose: str, recent: list[str], thresh: float = 0.35) -> tuple[bool, str]:
    """段落级去重：本场任一段与近场任一段 6-gram Jaccard ≥ thresh → 判为整段搬运。
    补整场/开场两道闸门的盲区（本场仅一个段落复述前场、其余原创时会被漏掉）。"""
    cur = _split_paragraphs(prose)
    for prev in recent:
        for pp in _split_paragraphs(prev):
            for cp in cur:
                if _gram_overlap(cp, pp, n=6) >= thresh:
                    return False, (f"有一整段与前文几乎重复（开头是「{cp[:18]}…」），"
                                   f"请删掉这段复述，改写成本场独有的画面与推进，不要照搬上一场写过的段落。")
    return True, ""


# 问题2：中文小说常被刷屏的氛围意象候选（无需分词，按出现场次统计跨场重复）
_IMAGERY_CANDIDATES = [
    "钟", "秒针", "钟摆", "机芯", "梧桐", "落叶", "枯叶", "叶", "雪茄", "香烟", "烟",
    "咖啡", "留声机", "唱针", "爵士", "月光", "阴影", "百叶窗", "霓虹", "雨", "雾",
    "钢笔", "笔帽", "骰子", "蜡烛", "炉火", "灯泡", "藤蔓", "爬山虎", "鱼骨", "尘埃",
    "烟雾", "烟灰", "镜子", "玻璃", "窗", "风", "盐",
]


_SCENE_LABEL = re.compile(
    r'^\s*#{0,6}\s*第\s*[一二三四五六七八九十百千两零0-9]+\s*[场場章回节節幕](?:\s*[:：])?(?=\s|$)')


def _strip_scene_headers(prose: str) -> str:
    """去掉 LLM 偶发在正文开头加的"场/章"元标题（如「# 第七场」「第八场」「## 第三章」）。
    读者正文里不应出现"第N场/第N章"这类标号——分章分场由排版层负责。只剥开头的元标记，
    保留紧随其后的正文（"# 第七场  亭子间…" → "亭子间…"）。"""
    if not prose:
        return prose
    s = prose.lstrip()
    for _ in range(4):  # 反复剥离叠加的标记（如 "## 第三章 章名\n第一场\n"）
        m = _SCENE_LABEL.match(s)
        if m:
            rest = s[m.end():]
            nl = rest.find("\n")
            if nl < 0:                       # 标号后全在一行 → 余下都是正文，保留
                s = rest.lstrip()
            else:
                same_line = rest[:nl].strip()
                after = rest[nl + 1:].lstrip()
                # 标号后同一行是短标题（章/场名，无句末标点）且下方另有正文 → 连标题一并剥掉
                title_like = (len(same_line) <= 8 and not re.search(r"[。！？!?，,；;]", same_line))
                if same_line == "" or (title_like and after):
                    s = after
                else:                        # 同一行已是正文 → 保留
                    s = rest.lstrip()
            continue
        if s.startswith("#"):  # 纯 markdown 短标题行（楔子/序章等，无场章字样）也剥掉
            nl = s.find("\n")
            head = (s[:nl] if nl >= 0 else s).lstrip("# ").strip()
            if len(head) <= 16 and not re.search(r"[。！？!?，,；;]", head):
                s = (s[nl + 1:] if nl >= 0 else "").lstrip()
                continue
        break
    return s


def _recurring_imagery(recent: list[str], min_scenes: int = 3) -> list[str]:
    """近 N 场里出现 ≥ min_scenes 场的氛围意象 → 视为刷屏，列入本场禁令。"""
    if len(recent) < min_scenes:
        return []
    ban = []
    for w in _IMAGERY_CANDIDATES:
        if sum(1 for s in recent if w in s) >= min_scenes:
            ban.append(w)
    return ban


# C 动作/桥段去重：谍战常见的、易被 LLM 每场复读的"展示谨慎/紧张"招式。
# （治"蹲下藏×5/查尾巴×5/摸钢笔×6/掸袖口×4"——冷却机制只管我们提供的习惯动作，治不住这些自发桥段。）
_ACTION_CANDIDATES = [
    "蹲下", "系鞋带", "鞋带", "掸", "袖口", "整了整", "衣领", "理了理",
    "回头", "确认", "身后", "尾巴", "盯梢", "余光",
    "攥", "捏", "指腹", "摩挲", "顿了顿",
    "透口气", "深吸", "压低", "帽檐", "掂量",
]


def _recurring_actions(recent: list[str], min_scenes: int = 3) -> list[str]:
    """近 N 场里出现 ≥ min_scenes 场的同一动作/桥段 → 列入本场"换种方式"禁令。"""
    if len(recent) < min_scenes:
        return []
    return [w for w in _ACTION_CANDIDATES if sum(1 for s in recent if w in s) >= min_scenes]


class Narrator:
    def __init__(self, repo: Repository, llm: LLMClient | None = None) -> None:
        self.repo = repo
        self.llm = llm

    def render(
        self,
        pov: str,
        events: list[Event],
        rolling_summary: str,
        reveal: list[str],
        conceal: list[str],
        feedback: str | None = None,
        hook: bool = False,
        scene_pos: int = 0,
    ) -> str:
        # hook=True 用于开篇第 1 场：开头要"钩"住读者（钩子理论）。
        # ③ 习惯动作冷却：本场可用的 mannerism（隔 N 场才复用；可能为空 → 本场不写习惯动作，更真实）。
        persona = self.repo.get_persona(pov)
        mann = self._pick_mannerisms(persona, scene_pos)
        motif = self._pick_motif(persona, scene_pos)  # A1 核心意象跨场冷却（每场只取一次）
        if self.llm is None:
            return self._render_offline(pov, events, hook, mann, motif)
        all_prose = [s.prose_text for s in self.repo.list_scenes() if s.prose_text]
        recent = all_prose[-2:]
        # 问题2：近 5 场反复出现的氛围意象 → 本场禁令（治"钟/梧桐叶/雪茄每场刷"）
        imagery_ban = _recurring_imagery(all_prose[-5:])

        def _render(fb):
            return self._render_llm(pov, events, rolling_summary, reveal, conceal, fb, hook,
                                    mann, motif, recent, imagery_ban)

        prose = _render(feedback)
        # 渲染后校验链（文风→篇幅[步骤4]→去重），整场至多重渲 2 次，按优先级用尽即止。
        prose = self._post_gate(prose, _render, recent, events)
        return _clean_ids(_strip_scene_headers(prose))  # 去场/章元标题 + 清内部 id

    def _post_gate(self, prose, render, recent, events, max_retry: int = 2) -> str:
        """统一渲染后校验：①§16.4 文风义务 ②A3 句级去重。各重渲至多一次，整场上限 max_retry。"""
        from ..tone import tone_gate

        from ..tone import emotion_ratio_gate

        used = 0
        profile = self.repo.get_tone_profile()
        if profile.is_set() and used < max_retry:
            ok, fb = tone_gate(prose, profile, self.llm)
            if not ok:
                prose = render(f"[文风义务未达标]{fb}")
                used += 1
        # ⑥ 情感共鸣 E 比率（仅高张力场）：感官/抽象情感比 < 3.5 → 改写为以生理与环境承载情感。
        if used < max_retry:
            ok, fb = emotion_ratio_gate(prose, self._scene_tension(events))
            if not ok:
                prose = render(f"[情感太抽象]{fb}")
                used += 1
        # B0 文风指纹软闸门：启用 style_skill 时，本场句法节奏偏离目标文风过大 → 重写靠拢。
        if used < max_retry:
            sp_getter = getattr(self.repo, "get_style_skill", None)
            sp = sp_getter() if sp_getter else None
            if sp is not None and sp.is_set():
                from ..style_skill import style_metric_gate
                ok, fb = style_metric_gate(prose, sp)
                if not ok:
                    prose = render(f"[文风指纹偏离]{fb}")
                    used += 1
        # A 篇幅：改为「过长压缩」（治描写过度饱和）。超过目标 1.3× → 精简；过短不再强行扩写（留白）。
        if used < max_retry:
            w = self._scene_word_target(events)
            if w and len(prose) > int(w * 1.3):
                prose = render(
                    f"[本场过长]请精简到约 {w} 字以内：删去与推进无关的环境/感官堆叠、重复的动作细节和连用的比喻，"
                    f"保留信息、对白与处境推进，不要灌水。")
                used += 1
        if used < max_retry:
            ok, fb = self._dedup_check(prose, recent)
            if not ok:
                prose = render(f"[与近场重复过高]{fb}")
                used += 1
        return prose

    # ---------- C1 单场目标字数 ----------
    def _scene_word_target(self, events) -> int:
        """从本场所属章的 target_words/target_scenes 折算单场字数；取不到则 0（不约束）。"""
        ch_id = next((e.beat_id for e in events if e.beat_id), None)
        if not ch_id:
            return 0
        getter = getattr(self.repo, "get_chapter_plan", None)
        ch = getter(ch_id) if getter else None
        if ch is None:
            return 0
        tw = int(getattr(ch, "target_words", 0) or 0)
        ts = max(1, int(getattr(ch, "target_scenes", 3) or 3))
        return tw // ts if tw else 0

    def _scene_tension(self, events) -> float:
        """本场所属章的 target_tension（0..1）；取不到则 0.5。E 比率/情感滞后据此区分重点场。"""
        ch_id = next((e.beat_id for e in events if e.beat_id), None)
        if not ch_id:
            return 0.5
        getter = getattr(self.repo, "get_chapter_plan", None)
        ch = getter(ch_id) if getter else None
        if ch is None:
            return 0.5
        return float(getattr(ch, "target_tension", 0.5) or 0.5)

    # ---------- A3 句级去重闸门（含开场去重） ----------
    def _dedup_check(self, prose: str, recent: list[str], thresh: float = 0.18,
                     lead_thresh: float = 0.6, para_thresh: float = 0.35) -> tuple[bool, str]:
        """本场与近 2 场重复过高 → 判重复重写。三道：
        ①整场 6-gram Jaccard ≥ thresh（防大面积复述）；
        ②**段落级** 6-gram Jaccard ≥ para_thresh（防"本场整段搬运前场某段"，补整场阈值盲区）；
        ③**开场首分句**与近场重合 ≥ lead_thresh（防"每场都以同一意象/句式开头"，如反复'雾在涨…'）。"""
        if not prose or not recent:
            return True, ""
        for prev in recent:
            if _gram_overlap(prose, prev, n=6) >= thresh:
                return False, "请改用不同的意象、比喻与句式，不要复述上一场已写过的画面与措辞。"
        ok, fb = _para_overlap(prose, recent, para_thresh)  # 段落级搬运检测
        if not ok:
            return False, fb
        ok, fb = _dialogue_overlap(prose, recent)  # 对白级重复台词检测
        if not ok:
            return False, fb
        for prev in recent:
            if _lead_overlap(prose, prev) >= lead_thresh:
                lead = _lead_clause(prev)
                return False, f"不要以与上一场相同的意象或句式开头（上一场开头是「{lead}…」），换一个完全不同的切入点重写开篇。"
        return True, ""

    # ---------- ③ 习惯动作冷却 ----------
    def _pick_mannerisms(self, persona, scene_pos: int, cooldown: int = 3, k: int = 1) -> list[str]:
        if not persona or not persona.mannerisms:
            return []
        arc = dict(persona.arc_state or {})
        cd = dict(arc.get("_mann_cd", {}))
        FAR = -10**9
        avail = [(cd.get(m, FAR), m) for m in persona.mannerisms if scene_pos - cd.get(m, FAR) >= cooldown]
        if not avail:
            return []  # 全在冷却中 → 本场不用习惯动作
        # 先 last_pos 最旧者，平局按在 persona 中的原始顺序（稳定、可预测）
        order = {m: i for i, m in enumerate(persona.mannerisms)}
        avail.sort(key=lambda t: (t[0], order.get(t[1], 0)))
        picked = [m for _, m in avail[:k]]
        for m in picked:
            cd[m] = scene_pos
        arc["_mann_cd"] = cd
        self.repo.update_arc_state(persona.agent_id, arc)
        return picked

    # ---------- A1 核心意象（motif）跨场冷却 ----------
    def _pick_motif(self, persona, scene_pos: int, cooldown: int = 2) -> str:
        """同一 motif 道具在 cooldown 场内不再当核心意象；全在冷却 → 返回 ''（本场不靠 motif）。
        治"铜钥每场都摸"——核心意象与习惯动作同理需要跨场冷却。返回道具名（非 id）。"""
        if not persona or not persona.motif_objects:
            return ""
        arc = dict(persona.arc_state or {})
        cd = dict(arc.get("_motif_cd", {}))
        FAR = -10**9
        avail = [m for m in persona.motif_objects if scene_pos - cd.get(m, FAR) >= cooldown]
        if not avail:
            return ""
        order = {m: i for i, m in enumerate(persona.motif_objects)}
        avail.sort(key=lambda m: (cd.get(m, FAR), order.get(m, 0)))
        pick = avail[0]
        cd[pick] = scene_pos
        arc["_motif_cd"] = cd
        self.repo.update_arc_state(persona.agent_id, arc)
        names = {e.entity_id: e.name for e in self.repo.list_entities()}
        nm = names.get(pick, "")
        # 英文/ASCII 名（如 "backup chip" 这类 id 残留）不作意象词，避免泄漏进正文
        return nm if (nm and not nm.isascii()) else ""

    # ---------- 离线模板：具体取代抽象，不点名情绪 ----------
    def _render_offline(self, pov: str, events: list[Event], hook: bool = False,
                        mann: list[str] | None = None, motif: str | None = None) -> str:
        persona = self.repo.get_persona(pov)
        name = persona.name if persona else pov
        mannerism = (mann or [None])[0]  # 冷却后可能无可用习惯动作
        motif_name = motif or self._motif_name(persona)
        # 跳过英文/ASCII 的 motif 名（如 "backup chip" 这类 id 残留），不让它进正文
        if motif_name and motif_name.isascii():
            motif_name = ""

        ents = {e.entity_id: e for e in self.repo.list_entities()}
        pov_pron = self._pron_for(pov)  # 按 POV 性别取代词（修离线模板硬编码"她"）
        # 钩子开篇：先抛一句反常/未答之问；非钩子用题材中性、不带古风意象的起句
        opener = (
            f"后来{name}常想，若那一夜没有回头，一切会不会不同。"
            if hook
            else f"{name}没有立刻开口。四下里很静，静得能听见自己的呼吸。"
        )
        lines: list[str] = [opener]
        for ev in events:
            payload = ev.payload or {}
            actor_id = ev.actors[0] if ev.actors else pov
            actor = ents[actor_id].name if actor_id in ents else name
            apron = self._pron_for(actor_id)
            tgt = payload.get("target")
            tgt_ent = ents.get(tgt) if tgt else None
            is_char = tgt_ent is not None and tgt_ent.type == "character"

            dialogue = payload.get("dialogue")
            if dialogue:
                lines.append(f"「{dialogue}」{actor}的声音压得很低。")
            elif is_char:
                lines.append(f"{actor}停在{tgt_ent.name}三尺之外，没有再近一步。")
            elif tgt_ent is not None:
                lines.append(f"{actor}的指尖一寸寸抚过{tgt_ent.name}。")
            else:
                lines.append(f"{actor}没有动，{apron}在等一个答案。")
        # 意象只在结尾点一次（避免每个事件重复同一句）
        if mannerism or motif_name:
            tail = f"{name}{mannerism}" if mannerism else f"{name}停住手"
            if motif_name:
                tail += f"，{motif_name}在掌心硌出一道印子"
            lines.append(tail + "。")
        return "\n".join(lines)

    def _pron_for(self, agent_id: str) -> str:
        """按角色性别返回人称代词；未知则默认"他"（中性兜底）。"""
        return "她" if self._infer_gender(agent_id) == "女" else "他"

    def _motif_name(self, persona) -> str:
        if persona and persona.motif_objects:
            names = {e.entity_id: e.name for e in self.repo.list_entities()}
            nm = names.get(persona.motif_objects[0], "")
            if nm and not nm.isascii():  # 英文/ASCII（id 残留）不作意象
                return nm
        return ""

    # ---------- LLM（§6.3） ----------
    def _render_llm(self, pov, events, rolling_summary, reveal, conceal, feedback, hook=False,
                    mann: list[str] | None = None, motif: str | None = None,
                    recent: list[str] | None = None, imagery_ban: list[str] | None = None) -> str:
        persona = self.repo.get_persona(pov)
        name = persona.name if persona else pov
        ledger = self.repo.get_agent_ledger(pov)
        reader = self.repo.list_reader_knowledge()
        names = {e.entity_id: e.name for e in self.repo.list_entities()}

        known = "\n".join(f"- {k.version_content}" for k in ledger) or "（空）"
        reader_known = "\n".join(f"- {r.revealed_version}" for r in reader) or "（读者尚一无所知）"
        ev_desc = "\n".join(self._event_desc(e, names) for e in events)
        # §12.3 所在之地：取本场事件地点的完整描写，供叙述调用真实环境细节（非空壳地点）
        geo = ""
        loc_id = next((e.location_id for e in events if e.location_id), None)
        if loc_id:
            getter = getattr(self.repo, "get_location", None)
            loc = getter(loc_id) if getter else None
            if loc and loc.geo_full:
                geo = loc.geo_full
        # ③ 只提供本场"未冷却"的习惯动作；为空则明确告知不要使用习惯动作（避免复读机）
        mann = mann or []
        motif_name = (motif if motif is not None else (self._motif_name(persona) if persona else ""))
        if mann:
            motifs = "、".join(mann + ([motif_name] if motif_name else []))
            mann_rule = ""
        else:
            motifs = motif_name
            mann_rule = "\n[习惯动作]本场**不要**使用任何人物习惯动作（近期已频繁出现，避免重复）。"
        reveal_txt = "、".join(self._ver(reveal)) or "（无）"
        conceal_txt = "、".join(self._ver(conceal)) or "（无）"
        w = self._scene_word_target(events)  # C1 单场目标字数（章 target_words/target_scenes）
        # §13.1 道具白名单：本章 items_present 之外不得凭空添置具体器物（叙述层硬约束）
        allowed_items = ""
        intro_items = ""   # 道具来源闸门：本章新登场道具，必须交代来源、不得凭空在手中
        scene_tension = 0.5  # B：本场张力（取本章 target_tension）→ 区分重点场/过渡场
        ch_id = next((e.beat_id for e in events if e.beat_id), None)
        if ch_id:
            getter = getattr(self.repo, "get_chapter_plan", None)
            ch = getter(ch_id) if getter else None
            if ch:
                scene_tension = float(getattr(ch, "target_tension", 0.5) or 0.5)
            if ch and getattr(ch, "items_present", None):
                # 跳过英文/ASCII 显示名的器物（id 残留如 "backup chip"），不进白名单提示
                allowed_items = "、".join(
                    nm for o in ch.items_present
                    if (nm := names.get(o, o)) and not nm.isascii())
            if ch and getattr(ch, "items_introduced", None):
                intro_items = "、".join(
                    nm for o in ch.items_introduced
                    if (nm := names.get(o, o)) and not nm.isascii())

        # P3：在场角色白名单（只有事件里真正行动的人）+ 性别标注（修性别一致性 bug）
        scene_actor_ids = list(dict.fromkeys(aid for e in events for aid in (e.actors or [])))
        wl_parts = []
        for aid in scene_actor_ids:
            nm = names.get(aid, aid)
            g = self._infer_gender(aid)
            wl_parts.append(f"{nm}（{g}）" if g else nm)
        actor_whitelist = "、".join(wl_parts) if wl_parts else "（无）"

        # POV 性别强约束（叙述主体出现最频繁，必须明确，否则 LLM 默认用"他"）
        pov_gender = self._infer_gender(pov)
        pov_pron = "她" if pov_gender == "女" else ("他" if pov_gender == "男" else "")
        pov_decl = f"POV={name}"
        if pov_gender:
            pov_decl += f"（{pov_gender}性；全文凡指代 {name} 一律用「{pov_pron}」，绝不可写错性别）"

        tone = self.repo.tone_profile_prompt()  # §16.5 文风契约前置块（约束渲染层基调）
        # B0 文风模拟：启用 style_skill 时叠加并优先（安全注入：只学腔调、严禁照搬样例内容）。
        style = ""
        sk_getter = getattr(self.repo, "style_skill_prompt", None)
        if sk_getter is not None:
            try:
                style = sk_getter()
            except Exception:
                style = ""
        system = (
            (tone + "\n\n" if tone else "")
            + (style + "\n\n" if style else "")
            + f"你是小说叙述者，视角=限制性第三人称，{pov_decl}。"
            f"你只知道 POV 此刻所知 + 读者已知，不得透露 POV 不知道的事。\n规则：{STYLE_BIBLE}\n"
            f"[红线] 本场在场角色仅限：{actor_whitelist}。括号内为该角色性别，"
            f"行文中每个人称代词（他/她）都必须与对应角色的性别严格一致，不得弄错。"
            f"不得凭空写出此名单之外的人物，不得发明未在事件里出现的台词，不得安排未经事件记录的物品转交。\n"
            f"[身份红线] 一个人物的隐藏身份/职务头衔（如队长、局长、特工、卧底、某党的人），"
            f"只有当它已经出现在上面【POV 此刻所知】或【读者已知】里时，才可以用该头衔称呼他；"
            f"否则一律用中性称呼（先生/那位女子/柜台后的人/对方），**不得擅自给人物安上尚未揭示的身份**。\n"
            # F 视角硬化：限制性第三人称不得越界写他人内心
            f"[视角红线] 只写 {name} 能看到、听到、感觉到、推断到的；**绝不**直接写其他人物的内心活动、"
            f"真实动机或 {name} 视线之外发生的事（不要写「赵九心里清楚…」「苏静的目光在…」这类全知句）。\n"
            # D 时间模糊化：禁精确钟点，用粗时段，且不自相矛盾
            f"[时间] 不要写精确钟点（如「三点十七分」「四点十分」）；用「午后/黄昏/入夜/后半夜」这类粗略时段。"
            f"同一场内时间只能向前走，不得自相矛盾、不得忽早忽晚。\n"
            # 反升华红线（StoryScope 硬伤一：77% AI 在结尾强行升华/说教）
            f"[反升华红线] **禁止**在场尾/段尾总结寓意、点题、升华主题，禁止写"
            f"「这就是…」「或许…才是真正的…」「原来…不过是…」这类格言式、归纳式收束句。"
            f"把意义留给读者：结尾停在一个**具体的动作、物象或未尽的对白**上，不要替读者把道理说破。"
        )
        # 问题3：在场角色随身道具的固定设定（材质/刻字已定，不得每场另编）
        ent_by_id = {e.entity_id: e for e in self.repo.list_entities()}
        canon_lines, seen_obj = [], set()
        for aid in list(dict.fromkeys(scene_actor_ids + [pov])):
            pp = self.repo.get_persona(aid)
            for oid in (pp.motif_objects if pp else []):
                if oid in seen_obj:
                    continue
                seen_obj.add(oid)
                ent = ent_by_id.get(oid)
                canon = (ent.attributes or {}).get("canon") if ent else None
                if canon:
                    canon_lines.append(f"- {ent.name}：{canon}")
        canon_block = (("[器物固定设定（材质/外观/刻字已经定死，必须严格遵守，"
                        "不得改成别的材质或别的刻字）]\n" + "\n".join(canon_lines) + "\n\n")
                       if canon_lines else "")

        # 问题5：隐藏身份称谓闸门——按揭示状态给每个在场角色"当前可用称谓"。
        # 治"身份揭示突兀（苏静突然成队长）"：真实身份未被主角/读者解锁前，只能用中性称呼。
        pov_fids = {k.fact_id for k in ledger}
        reader_fids = {r.fact_id for r in reader}
        idn_lines = self._identity_lines(scene_actor_ids + [pov], pov, pov_fids, reader_fids)
        idn_block = (("[在场角色称谓（按当前揭示状态，必须遵守，不得擅自升格头衔）]\n"
                      + "\n".join(idn_lines) + "\n\n") if idn_lines else "")

        # B 描写克制：聚焦主导感官 + 留白 + 比喻密度；低张力场（过渡场）整体简笔。
        if scene_tension < 0.5:
            desc_block = ("[描写克制] 本场是过渡场，**整体简笔推进**：少铺陈环境，把笔墨让给推进与对白；"
                          "全场聚焦 1 种主导感官即可，其余点到为止。比喻至多 1-2 处，优先直说。\n\n")
        else:
            desc_block = ("[描写克制] 本场聚焦 **1-2 种主导感官**，其余克制；**同一段不要视觉/听觉/嗅觉/触觉全堆上**。"
                          "重要的细节才铺陈，次要动作（洗脸、系鞋带、掸灰这类）一笔带过、不要逐帧描写。"
                          "比喻每场至多 2-3 处，优先直说，不要连用。\n\n")
        # ⑤ 情感滞后（affective lag）：高张力场不当场宣泄，延后到微小细节才崩塌（治"AI 情感太顺太饱和"）。
        if scene_tension >= 0.7:
            desc_block += ("[情感滞后] 遭遇重大冲击时，第一反应可以是反常的冷静、麻木，或去做一件无关小事"
                           "（系鞋带、擦杯子、把东西摆正）；强烈情感**延后**到稍后某个微小细节才全面崩塌。"
                           "不要当场嚎啕、不要直接宣告情绪（绝望/崩溃/痛不欲生），用生理与动作去承载。\n\n")
        # E 对话留白 + 语域差异：不必每句都带信息；按各人语域拉开说话方式。
        voice_bits = []
        for aid in scene_actor_ids:
            pp = self.repo.get_persona(aid)
            v = (pp.voice if pp else "").strip()
            if v:
                voice_bits.append(f"{names.get(aid, aid)}：{v[:30]}")
        voice_block = ("[对白] 不必每句话都带信息或试探，可有无目的的家常闲笔；"
                       "让不同人物的说话方式按各自语域拉开差距"
                       + ("（" + "；".join(voice_bits) + "）" if voice_bits else "")
                       + "。不要让所有人都用一样克制书面的腔调打哑谜。\n\n")
        # C 动作/桥段去重：近 5 场反复出现的同一动作桥段（蹲下藏/查尾巴/掸袖口/摸道具）→ 本场避免重复。
        recent5 = [s.prose_text for s in self.repo.list_scenes() if s.prose_text][-5:]
        action_ban = _recurring_actions(recent5)
        action_block = (("[动作去重] 以下动作/桥段近几场已反复出现，本场**换一种方式**表现谨慎/紧张，"
                         "不要再重复同一招：" + "、".join(action_ban) + "。\n\n") if action_ban else "")
        # ③ 章内连续性：若上一场与本场同属一章、紧接其后，则时空必须衔接
        # （治"第1章走出邮局又折返/时间倒流"——rolling_summary 太软，这里给硬约束）。
        cont_block = ""
        all_sc = self.repo.list_scenes()
        if all_sc and ch_id:
            last = all_sc[-1]
            last_beat = next((ev.beat_id for eid in last.source_events
                              if (ev := self.repo.get_event(eid)) and ev.beat_id), None)
            if last_beat == ch_id and (last.prose_text or "").strip():
                tail = last.prose_text.strip()[-70:]
                cont_block = (
                    f"[承接上一场·时空必须连续]\n本场与上一场同属一章、紧接其后。上一场结尾："
                    f"「…{tail}」\n本场要**接着这个时间和地点往下写**：人物**不得无故回到上一场已经离开的"
                    f"地点**，时间只能向前、不得倒流；若确实换了地点，必须交代是怎么过去的。\n\n")

        geo_block = f"[所在之地（可调用其中环境细节）]\n{geo}\n\n" if geo else ""
        user = (
            f"[前情摘要]\n{rolling_summary or '（开篇）'}\n\n"
            f"{cont_block}"
            f"{geo_block}"
            f"[POV 此刻所知]\n{known}\n\n[读者已知]\n{reader_known}\n\n"
            f"[本场事件]\n{ev_desc}\n\n[可用意象/习惯动作]\n{motifs}{mann_rule}\n\n"
            f"[本场需揭示]\n{reveal_txt}\n[本场需藏住]\n{conceal_txt}\n\n"
            + canon_block
            + idn_block
            + desc_block
            + voice_block
            + action_block
            + (f"[可提及的器物（仅这些；不得凭空添置其它具体道具）]\n{allowed_items}\n"
               "——反复出现的随身道具（钢笔/烟盒/骰子等）每次尽量赋予**不同的功能或意义**，"
               "不要每章都把同一件东西用作同一种暗号/动作。\n\n" if allowed_items else "")
            + (f"[本章新登场道具·必须交代来源]\n{intro_items}\n"
               "——这些器物若在本场首次出现，**必须写清它从何而来**（从某处取得/某人递来/某处发现），"
               "**绝不可**让它凭空出现在人物手里或怀中；若本场用不到，可不写。\n\n" if intro_items else "")
            # A 篇幅瘦身：软上限 + 鼓励留白；过短无妨（短即节奏），**不要注水**填字数。
            + (f"[篇幅]本场约 {w} 字为宜，**宁可短、不要注水**；不足也无妨（留白即节奏），但不要超过 {int(w * 1.3)} 字。\n\n" if w else "")
            + "只输出散文，不要解释，不要直接点名情绪。"
            + "**不要**加任何标题、小标题或「第N场/第N章」之类的标号，直接从正文写起。"
        )
        anchor = self.repo.style_anchor_prompt()  # §4.3 跨批语气对齐
        if anchor:
            user += "\n\n" + anchor
        # A2 近场反重复：把上 1-2 场的首尾句喂回去，明令换意象/句式/视角/开头，不得复述。
        if recent:
            tails = []
            for t in [s.strip() for s in recent[-2:] if s and s.strip()]:
                tails.append((t[:40] + " … " + t[-40:]) if len(t) > 95 else t)
            if tails:
                user += ("\n\n[避免与近场雷同（以下是刚写过的，换意象/比喻/句式/切入点，"
                         "不要复述同样的画面与措辞）]\n" + "\n— — —\n".join(tails))
            prev_lead = _lead_clause(recent[-1]) if recent[-1] else ""
            if prev_lead:
                user += (f"\n[开头硬约束]上一场以「{prev_lead}…」开头；本场**不得**以相同的意象词或句式开头，"
                         f"换一个完全不同的切入点（人物动作/对白/一个具体物件皆可）。")
        if hook:
            user += (
                "\n\n[这是全书开篇·钩子]\n第一句/第一段最重要的任务是"
                "「钩」住读者：抛出一个反常、悬而未决或令人不安的瞬间，"
                "让读者立刻想问『然后呢？』。在场景中途切入(in medias res)，"
                "先制造悬念，把来龙去脉押后，不要从平铺直叙的背景写起。"
            )
        if imagery_ban:
            user += (f"\n\n[意象禁令]以下意象在最近几场已反复出现，本场**不要再用**作为画面或氛围"
                     f"（换全新的感官细节）：{('、'.join(imagery_ban))}。")
        if feedback:
            user += f"\n\n[上一稿未通过校验，请按以下意见重写]\n{feedback}"
        # ③ 尾部铁律加固（对抗 Lost-in-Middle / Attention Sink）：把最硬约束在 prompt **最尾端**再钉一遍，
        # 紧挨"开始写作"——激活模型末端注意力，避免核心设定滑入"中间黑洞"被稀释。
        iron_bits = [f"在场仅限 {actor_whitelist}（人称代词须与各自性别一致）"]
        if pov_pron:
            iron_bits.append(f"指代 {name} 一律用「{pov_pron}」")
        if allowed_items:
            iron_bits.append(f"可提及器物仅限 {allowed_items}，不得凭空添置别的具体道具")
        if w:
            iron_bits.append(f"约 {w} 字、宁短勿注水")
        iron_bits.append("结尾停在具体动作或物象上、不得升华点题")
        user += "\n\n[★本场铁律·最后再确认（最高优先）]\n" + "；".join(iron_bits) + "。"
        # 强制注意力锚定：让关键道具被本场生成"消费"一次，对抗 Attention Drift 对设定的遗忘。
        key_item = allowed_items.split("、")[0] if allowed_items else ""
        if key_item:
            user += (f"\n本场至少让 {name} 在一个动作或一句内心里**触及一次**「{key_item}」此刻的状态/位置，"
                     f"以确保你的生成始终受该器物设定约束。")
        return self.llm.complete(system, user).strip()  # type: ignore[union-attr]

    def _identity_lines(self, actor_ids: list[str], pov: str,
                        pov_fids: set, reader_fids: set) -> list[str]:
        """问题5：为在场角色生成"当前可用称谓"约束行（据隐藏身份的揭示状态）。
        未解锁（主角/读者都还不知其真实身份）→ 只能用中性称呼；解锁后才可用真实头衔。
        身份信息存在 entity.attributes['identity']={public,true,fact_id}（lock_hidden_identities 落库）。"""
        ent_by_id = {e.entity_id: e for e in self.repo.list_entities()}
        lines: list[str] = []
        for aid in dict.fromkeys(actor_ids):
            ent = ent_by_id.get(aid)
            idn = (ent.attributes or {}).get("identity") if ent else None
            if not isinstance(idn, dict) or not idn.get("true"):
                continue
            nm = ent.name
            pub = (idn.get("public") or "中性称呼").strip()
            tru = idn.get("true").strip()
            fid = idn.get("fact_id")
            # POV 是本人 → 当然知道自己身份；否则须主角或读者已解锁该身份 fact
            unlocked = (aid == pov) or bool(fid and (fid in pov_fids or fid in reader_fids))
            if unlocked:
                lines.append(f"- {nm}：真实身份已揭示，可称「{tru}」。")
            else:
                lines.append(
                    f"- {nm}：真实身份**尚未揭示**，本场只能用中性称呼「{pub}」；"
                    f"**绝不可**称其「{tru}」、点破其真实职务/立场，或让任何人暗示该身份。")
        return lines

    def _infer_gender(self, agent_id: str) -> str:
        """取角色本人性别（修性别一致性 bug）。
        优先用建卡时 LLM 显式判断并存入 arc_state['gender'] 的结果（可靠）；
        仅当缺失时才回退到文本统计（不可靠，因为简介常提到他人，仅作兜底）。"""
        persona = self.repo.get_persona(agent_id)
        stored = (persona.arc_state or {}).get("gender") if persona else None
        if stored in ("男", "女"):
            return stored
        # 无显式存储时不臆测：从自由文本统计代词会被简介里提到的他人带偏（反向误判），
        # 宁可不标注（让叙述者按名字与上下文自行处理），也不给出可能错误的性别。
        return ""

    def _event_desc(self, ev: Event, names: dict) -> str:
        p = ev.payload or {}
        tgt = names.get(p.get("target"), p.get("target") or "")
        bits = [f"{names.get(ev.actors[0], ev.actors[0]) if ev.actors else '某人'} {ev.action_type}"]
        if tgt:
            bits.append(f"对象：{tgt}")
        if p.get("dialogue"):
            bits.append(f"说：{p['dialogue']}")
        return "；".join(bits)

    def _ver(self, fact_ids: list[str]) -> list[str]:
        out = []
        for fid in fact_ids:
            f = self.repo.get_fact(fid)
            if f:
                out.append(f.canonical_content)
        return out
