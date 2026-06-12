"""章级审计闸门（用户要求·严格审计，不合格重渲）。

每写完一章，审三件事：
  ① 章际衔接：本章开头是否承接上一章结尾/钩子；
  ② 场次转场：章内场与场之间时间/地点是否连贯（无跳脱、时间倒流、无故折返）；
  ③ 道具/人物合规：是否出现不在【在场人物】【可用道具】清单、或与剧情/大纲不符的人/物。

确定性层（零 token，低误报）先抓"已登记但本章不该出现的道具"；LLM 层做严格综合判定。
两层任一不过 → ok=False + feedback，交剪辑层重渲本章。无 LLM 时不拦（离线放行）。
"""
from __future__ import annotations

import re

from ..llm.base import LLMClient
from ..models import ChapterPlan, Scene
from ..repository import Repository

# ⑤ 反升华（StoryScope 硬伤一）：归纳式连接词 + 抽象大词 同现于章尾 → 疑似强行升华/点题。
_SERMON_CONNECTIVES = [
    "这就是", "这便是", "或许", "也许", "才是真正", "才是最", "原来", "不过是", "终究",
    "到头来", "说到底", "归根结底", "其实", "无非是", "都是为了",
]
_SERMON_ABSTRACT = [
    "命运", "人生", "意义", "真相", "救赎", "宿命", "人性", "生命", "世界", "时间",
    "选择", "代价", "答案", "信念", "希望", "孤独", "自由",
]


def _sermon_tail(prose: str) -> bool:
    """确定性：章尾是否在强行升华/点题。取末段（末 ~120 字），命中"归纳式连接词 + 抽象大词"
    组合即判疑似（低误报，仅作 LLM 终判的预警，不单独硬性触发重渲）。"""
    tail = (prose or "").strip()
    if not tail:
        return False
    parts = [p for p in re.split(r"\n\s*\n|\n", tail) if p.strip()]
    last = (parts[-1] if parts else tail)[-120:]
    return any(c in last for c in _SERMON_CONNECTIVES) and any(w in last for w in _SERMON_ABSTRACT)


def _advanced(repo: Repository, ch: ChapterPlan) -> bool:
    """S2 向前检查（事实层）：本章在模拟里是否真发生过"实质推进"——关键抉择 / 道具易手 / 揭示解锁。
    与 director._chapter_advanced 同判据。仅作 LLM 审计的上下文（不作硬重渲触发，因重渲只改正文、
    造不出本未发生的抉择）。"""
    try:
        for e in repo.events_for_beat(ch.chapter_id):
            if (e.payload or {}).get("chosen_value"):
                return True
        for it in repo.list_inventory():
            if it.acquired_chapter == ch.sequence_order and it.status in ("held", "transferred"):
                return True
        if ch.reveal_gate:
            for n in repo.list_reveal_nodes():
                if n.discovered and n.fact_id in ch.reveal_gate \
                        and n.discovered_chapter == ch.sequence_order:
                    return True
    except Exception:
        return True  # 取数失败不误伤
    return False


def _phantom_items(repo: Repository, ch: ChapterPlan, prose: str) -> list[str]:
    """确定性：正文里出现了"已登记为实体、但不在本章 items_present"的具体道具 → 视为越界道具。
    只认长度≥2 的非英文器物名（避免"信"这种单字误伤），低误报。"""
    allowed = set(ch.items_present or [])
    out: list[str] = []
    for e in repo.list_entities():
        if e.type != "object":
            continue
        nm = e.name
        if e.entity_id in allowed or len(nm) < 2 or nm.isascii():
            continue
        if nm in prose:
            out.append(nm)
    return out


def audit_chapter(repo: Repository, ch: ChapterPlan, scenes: list[Scene],
                  prev_ch: ChapterPlan | None, llm: LLMClient | None) -> tuple[bool, str]:
    """审一章。返回 (是否通过, 反馈)。无 LLM → 仅跑确定性层。"""
    prose = "\n\n".join(s.prose_text for s in scenes if s.prose_text)
    if not prose.strip():
        return True, ""
    names = {e.entity_id: e.name for e in repo.list_entities()}

    # ① 确定性：越界道具
    phantom = _phantom_items(repo, ch, prose)
    det_issue = (f"出现了不属于本章的道具：{('、'.join(phantom))}（不在本章可用道具清单里，"
                 f"要么删掉、要么交代它合理的来源）。" if phantom else "")

    if llm is None:
        return (not phantom), det_issue

    # ② LLM 严格综合判定
    cast_names = "、".join(names.get(a, a) for a in (ch.cast or [])) or "（未指定）"
    item_names = "、".join(n for o in (ch.items_present or [])
                          if (n := names.get(o, o)) and not n.isascii()) or "（无）"
    prev_tail = ""
    if prev_ch is not None:
        prev_tail = (prev_ch.ending_hook or "").strip()
    beats = "；".join((ch.beat_goals or [])[:4])
    advanced = _advanced(repo, ch)
    system = (
        "你是严格的小说连续性审校。只判断硬伤，不评文笔。对下面这一章做四项检查，"
        "**从严**——只要有一项明显出问题就判不合格：\n"
        "① 衔接：本章开头是否承接了上一章结尾留下的悬念/钩子？（若这是第一章则此项免检）\n"
        "② 转场：章内各场之间，时间与地点是否连贯？有没有**时间倒流、人物无故回到上一场已离开的"
        "地点、场景跳脱无交代**？\n"
        "③ 合规：正文里出现的**人物**是否都在【在场人物】内（被回忆/提及的缺席者除外）？"
        "出现的**具体道具**是否都在【可用道具】内、且来源交代得清楚（**不得有凭空出现在手里的关键物件**）？\n"
        "④ 推进（向前检查）：本章是否**真的让局面发生了实质变化**、兑现了它的【推进目标】与【戏剧问题】？"
        "还是只是又一场原地周旋、试探、观察而没有任何推进（空转章）？空转即判不合格。\n"
        "⑤ 反升华：本章结尾是否在**强行升华、点题、总结寓意**（如「这就是…」「原来…不过是…」"
        "「命运/人生/意义…」这类格言式、归纳式收束句）？好的结尾应停在具体动作或物象上、把意义留给读者，"
        "强行升华即判不合格。\n"
        '只输出 JSON：{"ok": true或false, "feedback": "若不合格，逐条指出问题并给出修改方向；合格则空串"}'
    )
    user = (
        f"【上一章结尾钩子】{prev_tail or '（无／本章为第一章）'}\n"
        f"【本章戏剧问题】{ch.dramatic_question or '（无）'}\n"
        f"【本章冲突类型】{ch.conflict_type or '（未指定）'}\n"
        f"【本章推进目标(exit_state)】{ch.exit_state or '（无）'}\n"
        f"【本章节拍】{beats or '（无）'}\n"
        f"【在场人物】{cast_names}\n"
        f"【可用道具】{item_names}\n"
        f"【事实层是否有推进】{'有（已发生抉择/易手/揭示）' if advanced else '未检出实质推进——本章可能在空转，请重点核查 ④'}\n"
        f"{('【确定性预警】' + det_issue) if det_issue else ''}\n"
        f"{'【升华预警】章尾疑似强行升华/点题（出现归纳式收束 + 抽象大词），请重点核查 ⑤。' if _sermon_tail(prose) else ''}\n\n"
        f"【本章正文】\n{prose[:6000]}\n\n只输出 JSON。"
    )
    try:
        import json
        raw = llm.complete(system, user).strip().strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
        i, j = raw.find("{"), raw.rfind("}")
        data = json.loads(raw[i:j + 1] if 0 <= i < j else raw)
        llm_ok = bool(data.get("ok"))
        fb = str(data.get("feedback", "")).strip()
    except Exception:
        # 解析失败 → 不阻塞（放行），但带上确定性问题
        return (not phantom), det_issue
    ok = llm_ok and not phantom
    feedback = "；".join(x for x in [det_issue, fb] if x)
    return ok, feedback
