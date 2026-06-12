"""Fix D：世界圣经扩写（治"设定/地理/文化/禁忌内容太少"）。

锁定时把偏薄的世界圣经分节用 LLM **非破坏式**扩写——原文永不覆写，只追加一条
source='llm_expanded' 的「详述」companion；`bible_sections_text` 会把原文+详述一起喂给
规划层与 SceneWriter（二者现都消费世界圣经）。幂等：已有详述则跳过。无 LLM 不做。
"""
from __future__ import annotations

import json
import uuid

from .llm.base import LLMClient
from .models import CharacterCard, Entity, Faction, GraphEdge, Location
from .repository import Repository

_SECTION_CN = {
    "settingCore": "设定内核", "geography": "地理", "culture": "文化/势力/禁忌",
    "rules": "世界规则/铁律", "history": "历史",
}
_EXPAND_SECTIONS = ("settingCore", "geography", "culture", "rules")

# ===== W1 World Skill：分节全量化 + 两级(summary/detail) + 校验回路 + 渐进细化 =====
# 像 skill：基础层(BASE)厚实权威，其余节给出一致的概述，用到深处再 deepen_section。
_W1_SECTIONS: dict[str, str] = {
    "settingCore": "设定内核", "cosmology": "世界结构", "rules": "世界规则/力量体系",
    "geography": "地理", "culture": "文化习俗", "taboos": "禁忌",
    "history": "历史大事年表", "language": "语言/称谓", "economy": "经济/民生",
}
_W1_BASE = ("settingCore", "rules", "geography", "culture")  # detail 须 ≥300–800 字


def _json(llm: LLMClient, system: str, user: str, expect_list: bool = False):
    """解析 LLM 的 JSON 输出（对象或数组），失败返回 None。"""
    try:
        raw = llm.complete(system + " 输出必须是合法 JSON。", user).strip().strip("`")
    except Exception:
        return None
    if raw.lower().startswith("json"):
        raw = raw[4:].strip()
    o, c = (raw.find("["), raw.rfind("]")) if expect_list else (raw.find("{"), raw.rfind("}"))
    try:
        return json.loads(raw)
    except Exception:
        if 0 <= o < c:
            try:
                return json.loads(raw[o:c + 1])
            except Exception:
                return None
        return None


def build_world_skill(repo: Repository, llm: LLMClient | None = None, theme: str = "") -> dict:
    """W1：把世界观分节做厚做权威（两级 summary/detail + 多级生成 + 校验回路）。

    ① 总览：LLM 出各节 1–2 句 summary（严格据草稿原文，不新增冲突设定）。
    ② 逐节深写 detail（喂入全部 summary 保一致 + 本节草稿原文；BASE 层 ≥300–800）。
    ③ 汇聚校验：把全部 summary+detail 丢回 LLM 找矛盾/冲突/漏洞 → issues。
    ④ 修订：按 issue 重写该节（封顶 4），upsert 落 source='w1' 行。
    幂等：已有 w1 行则跳过；无 LLM / 无草稿 → no-op（绝不臆造）。返回统计。"""
    if llm is None:
        return {"skipped": "no_llm"}
    if any(r["source"] == "w1" for r in repo.list_bible_sections()):
        return {"skipped": "exists"}
    source_blob = repo.bible_sections_text(None, max_chars=4000)
    if not source_blob.strip():
        return {"skipped": "no_source"}
    tp = repo.get_tone_profile()
    tone_hint = ""
    if tp and tp.is_set():
        tone_hint = f"（题材「{tp.genre}」，语域「{tp.register}」，理解专有名词时勿望文生义）"

    secs = list(_W1_SECTIONS.items())
    # ① 总览 summary（一次）
    over_sys = (
        "你是小说世界观设定师。【任务：总览】基于给定的世界设定草稿，为下列各节写 1–2 句 summary"
        f"{tone_hint}。**严格据草稿，不得新增与原意冲突的设定、不得跑题**；草稿没提到的节，"
        "按主题与已有设定推一条**与之一致**的概述。只输出 JSON：{section键: \"summary\"}。\n"
        f"节键：{('、'.join(k for k, _ in secs))}"
    )
    over = _json(llm, over_sys, f"主题：{theme or '（未定）'}\n\n[世界设定草稿]\n{source_blob}\n\n只输出 JSON。")
    summaries: dict[str, str] = {}
    if isinstance(over, dict):
        for k, _ in secs:
            v = over.get(k)
            if isinstance(v, str) and v.strip():
                summaries[k] = v.strip()
    for k, _ in secs:
        summaries.setdefault(k, "")

    # ② 逐节深写 detail
    details: dict[str, str] = {}
    sum_blob = "\n".join(f"· {_W1_SECTIONS[k]}：{summaries[k]}" for k, _ in secs if summaries[k])
    for k, cn in secs:
        target = "300–800 字" if k in _W1_BASE else "120–300 字"
        own = repo.bible_sections_text([k], max_chars=1200)
        det_sys = (
            f"你是小说世界观设定师。【任务：深写】section={k}（{cn}）。把这一节写成具体、可被反复调用的设定，"
            f"{target}{tone_hint}。给出具体的地名/势力名号/规矩/禁忌/典型场景的声光气味；"
            "**保留并自然延展草稿原意，不得新增与其它节冲突的设定、不跑题**。只输出这一节的正文，不要标题/解释。"
        )
        det_user = (f"主题：{theme or '（未定）'}\n\n[本节草稿原文]\n{own or '（草稿未提，据下方各节概述与主题补全）'}\n\n"
                    f"[全书各节概述（保持一致）]\n{sum_blob}\n\n请写 {cn} 这一节的正文。")
        try:
            det = (llm.complete(det_sys, det_user) or "").strip()
        except Exception:
            det = ""
        details[k] = det or (own or summaries[k])

    # ③ 汇聚校验
    review_blob = "\n\n".join(f"【{_W1_SECTIONS[k]}】summary：{summaries[k]}\ndetail：{details[k][:600]}"
                              for k, _ in secs)
    rev_sys = (
        "你是世界观一致性审校。【任务：校验】检查下列各节设定：有无**自相矛盾**？有无与主题/草稿**冲突**？"
        "有无明显**逻辑漏洞或空白**？只列真正的硬问题。"
        "只输出 JSON：{\"issues\":[{\"section\":\"节键\",\"problem\":\"问题\",\"fix\":\"修法\"}]}"
    )
    review = _json(llm, rev_sys, f"主题：{theme or '（未定）'}\n\n{review_blob}\n\n只输出 JSON。")
    issues = []
    if isinstance(review, dict) and isinstance(review.get("issues"), list):
        issues = [it for it in review["issues"][:4]
                  if isinstance(it, dict) and it.get("section") in _W1_SECTIONS]

    # ④ 修订（每节至多一次）
    fixed: set[str] = set()
    for it in issues:
        sec = it["section"]
        if sec in fixed:
            continue
        fixed.add(sec)
        fix_sys = (
            f"你是小说世界观设定师。【任务：修订】section={sec}（{_W1_SECTIONS[sec]}）。"
            f"按下述问题与修法重写这一节，消除矛盾/补足漏洞，保持与其它节一致。"
            "只输出 JSON：{\"summary\":\"1–2 句\",\"detail\":\"重写后的正文\"}"
        )
        fix_user = (f"[问题]{it.get('problem','')}\n[修法]{it.get('fix','')}\n\n"
                    f"[原 summary]{summaries[sec]}\n[原 detail]{details[sec]}\n\n只输出 JSON。")
        d = _json(llm, fix_sys, fix_user)
        if isinstance(d, dict):
            if str(d.get("summary", "")).strip():
                summaries[sec] = str(d["summary"]).strip()
            if str(d.get("detail", "")).strip():
                details[sec] = str(d["detail"]).strip()

    # 落库：每节一条权威 w1 行
    for k, cn in secs:
        if details.get(k, "").strip():
            repo.upsert_w1_section(k, cn, summaries.get(k, ""), details[k])
    return {"sections": len(secs), "issues": len(issues), "fixed": len(fixed)}


def deepen_section(repo: Repository, llm: LLMClient | None = None,
                   section: str = "", context: str = "",
                   hint: str = "") -> bool:
    """W1 渐进细化（skill 式按需展开）：给该节**追加**一条
    source='w1_deepened' 子详述（据当前剧情 context 延展具体设定，不与既有冲突）。
    可多次深化；hint 为用户指定的深化方向。无 LLM / 节无内容 → 不做。返回是否新增。"""
    if llm is None or section not in _W1_SECTIONS:
        return False
    rows = repo.list_bible_sections(section)
    if not rows:
        return False
    cur = repo.bible_sections_text([section], max_chars=2000)
    cn = _W1_SECTIONS[section]
    round_n = sum(1 for r in rows if r["source"] == "w1_deepened") + 1
    hint_line = f"用户要求重点深化的方向：{hint}\n" if hint else ""
    sys = (
        f"你是小说世界观设定师。【任务：深化（第{round_n}轮）】section={section}（{cn}）。"
        f"{hint_line}"
        "据当前剧情与已有设定**追加**更具体的设定细节（新地点/规矩/人物群体/物产/禁忌细则等），"
        "**只延展、不得推翻或矛盾**于既有设定，也不要重复已写过的内容。150–400 字，只输出追加的正文。"
    )
    user = f"[本节全部设定（含历次深化）]\n{cur}\n\n[当前剧情]\n{context[:400]}\n\n请追加 {cn} 的新细节。"
    try:
        out = (llm.complete(sys, user) or "").strip()
    except Exception:
        return False
    if not out:
        return False
    title_suffix = f"·深化{round_n}" if round_n > 1 else "·深化"
    repo.add_bible_section(section, f"{cn}{title_suffix}", out, source="w1_deepened")
    return True


def expand_world_bible(repo: Repository, llm: LLMClient | None = None,
                       theme: str = "", min_chars: int = 180) -> int:
    """对偏薄（< min_chars）的核心分节追加「详述」。返回扩写的节数。"""
    if llm is None:
        return 0
    existing = repo.list_bible_sections()
    have_expanded = {s["section"] for s in existing if s["source"] == "llm_expanded"}
    by_section: dict[str, list] = {}
    for s in existing:
        by_section.setdefault(s["section"], []).append(s)
    expanded = 0
    for sec in _EXPAND_SECTIONS:
        rows = by_section.get(sec)
        if not rows or sec in have_expanded:
            continue
        cur = "\n".join(r["body_full"] for r in rows).strip()
        if len(cur) >= min_chars:        # 已足够丰富则不动
            continue
        cn = _SECTION_CN.get(sec, sec)
        system = (
            f"你是小说世界观设定师。基于主题与已有的【{cn}】简述，把它**扩写得更具体、可被反复调用**："
            "给出具体的地名/街区/势力名号/规矩与禁忌/典型场景的声光气味，**保留并自然延展原意**，"
            "**不得**新增与原意冲突的设定、不得跑题。300-600 字，只输出扩写正文，不要标题或解释。"
        )
        user = f"主题：{theme or '（未定）'}\n\n已有【{cn}】简述：\n{cur}\n\n请扩写。"
        try:
            out = llm.complete(system, user).strip()
        except Exception:
            continue
        if out and len(out) > len(cur):
            repo.add_bible_section(sec, "详述", out, source="llm_expanded")
            expanded += 1
    return expanded


def build_geography(repo: Repository, llm: LLMClient | None = None, theme: str = "") -> dict:
    """W2 地理层：把 W0 已固化的 canon 地点**做厚做权威**（两级 summary/detail + 风土人情
    + 层级关系），并对照世界观校验、修订。

    依赖：须在 lock_canonical_geography 之后调；canon 地点已存在但 W2 字段薄。
    ① 总览：把 canon 地点列表丢回 LLM，让它**只用既有地点**给出层级关系与一句话定位
       （level/parent/summary）。**禁止新增 canon 之外的地点**。
    ② 逐个深写：对每个 canon 地点写 detail（方位/地形/气候/建筑/声光气味）+ culture_local
       （风土人情/常驻人群/典型活动）；喂入世界观 settingCore+geography summary 保一致。
    ③ 汇聚校验：所有地点 + 世界观 summary 丢回 LLM，找"与世界观冲突 / 自相矛盾 /
       逻辑漏洞"的硬问题（最多 4 条）。
    ④ 修订：按 issue 重写该地点的 summary/detail/culture_local，原地 update。
    幂等：所有 canon 地点已有非空 summary 则跳过；无 LLM / 无 canon → no-op。返回统计。"""
    if llm is None:
        return {"skipped": "no_llm"}
    canon = [e for e in repo.list_entities()
             if e.type == "location" and (e.attributes or {}).get("canon")]
    if not canon:
        return {"skipped": "no_canon"}
    locs_by_id = {loc.loc_id: loc for loc in repo.list_locations()}
    canon_locs = [locs_by_id[e.entity_id] for e in canon if e.entity_id in locs_by_id]
    if not canon_locs:
        return {"skipped": "no_canon_loc"}
    # 幂等：所有 canon 都已有 W2 summary 则跳过
    if all((l.summary or "").strip() for l in canon_locs):
        return {"skipped": "exists"}

    # 世界观一致性锚：settingCore + geography 的 summary（W1 产出）
    world_blob = repo.bible_summaries_text(["settingCore", "geography", "culture"])
    if not world_blob.strip():
        world_blob = repo.bible_sections_text(["settingCore", "geography"], max_chars=1500)
    tp = repo.get_tone_profile()
    tone_hint = ""
    if tp and tp.is_set():
        tone_hint = f"（题材「{tp.genre}」，语域「{tp.register}」）"

    name_of = {l.loc_id: l.name for l in canon_locs}
    canon_list = "\n".join(f"- {l.name}（loc_id={l.loc_id}）"
                           f"{'：' + l.geo_full if l.geo_full else ''}" for l in canon_locs)

    # ① 总览：层级 + 一句话 summary
    over_sys = (
        "你是小说世界观地理设定师。【任务：总览】下列 canon 地点已固定，**不得新增/删除**。"
        f"对每个地点给：level(大区域/街道/建筑/其他之一)、parent(上级 loc_id；最顶层留空)、"
        f"summary(一句话定位){tone_hint}。**严格符合世界观，不得编造与设定冲突的地名或属性**。"
        "只输出 JSON：{loc_id: {\"level\":\"\",\"parent\":\"\",\"summary\":\"\"}}"
    )
    over = _json(llm, over_sys,
                 f"主题：{theme or '（未定）'}\n\n[世界观锚]\n{world_blob}\n\n"
                 f"[canon 地点]\n{canon_list}\n\n只输出 JSON。")
    overview: dict[str, dict] = {}
    if isinstance(over, dict):
        for lid in name_of:
            v = over.get(lid)
            if isinstance(v, dict):
                overview[lid] = {"level": str(v.get("level", "")).strip(),
                                 "parent": str(v.get("parent", "")).strip() if v.get("parent") in name_of else "",
                                 "summary": str(v.get("summary", "")).strip()}
    for lid in name_of:
        overview.setdefault(lid, {"level": "", "parent": "", "summary": ""})

    # ② 逐个深写 detail + culture_local
    sum_blob = "\n".join(f"· {name_of[lid]}：{overview[lid]['summary']}"
                         for lid in name_of if overview[lid]["summary"])
    details: dict[str, dict] = {}
    for loc in canon_locs:
        det_sys = (
            f"你是小说世界观地理设定师。【任务：深写】地点「{loc.name}」。给两段："
            f"detail(方位/地形/气候/建筑/声光气味，120–300 字) + culture_local(本地风土人情/"
            f"常驻人群/典型活动，60–180 字){tone_hint}。**保留并自然延展既有 geo_full，"
            "严格符合世界观，不得新增与设定冲突的奇幻元素**。"
            "只输出 JSON：{\"detail\":\"\",\"culture_local\":\"\"}"
        )
        det_user = (
            f"主题：{theme or '（未定）'}\n\n[世界观锚]\n{world_blob}\n\n"
            f"[本地点既有 geo_full]\n{loc.geo_full or '（无）'}\n"
            f"[本地点定位]\n{overview[loc.loc_id]['summary']}\n\n"
            f"[全部地点速览（保持一致）]\n{sum_blob}\n\n请写「{loc.name}」。只输出 JSON。"
        )
        d = _json(llm, det_sys, det_user)
        if isinstance(d, dict):
            details[loc.loc_id] = {
                "detail": str(d.get("detail", "")).strip() or loc.geo_full,
                "culture_local": str(d.get("culture_local", "")).strip(),
            }
        else:
            details[loc.loc_id] = {"detail": loc.geo_full, "culture_local": ""}

    # ③ 汇聚校验
    review_blob = "\n\n".join(
        f"【{loc.name}】level：{overview[loc.loc_id]['level']}；"
        f"summary：{overview[loc.loc_id]['summary']}\ndetail：{details[loc.loc_id]['detail'][:300]}\n"
        f"culture：{details[loc.loc_id]['culture_local'][:200]}"
        for loc in canon_locs)
    rev_sys = (
        "你是小说地理一致性审校。【任务：校验】检查下列地点设定：有无**与世界观冲突**？"
        "有无**自相矛盾**？有无**逻辑漏洞/空白**？只列真正的硬问题，最多 4 条。"
        "只输出 JSON：{\"issues\":[{\"loc_id\":\"\",\"problem\":\"\",\"fix\":\"\"}]}"
    )
    review = _json(llm, rev_sys,
                   f"[世界观锚]\n{world_blob}\n\n{review_blob}\n\n只输出 JSON。")
    issues = []
    if isinstance(review, dict) and isinstance(review.get("issues"), list):
        issues = [it for it in review["issues"][:4]
                  if isinstance(it, dict) and it.get("loc_id") in name_of]

    # ④ 修订（每地点至多一次）
    fixed: set[str] = set()
    for it in issues:
        lid = it["loc_id"]
        if lid in fixed:
            continue
        fixed.add(lid)
        fix_sys = (
            f"你是小说地理设定师。【任务：修订】地点「{name_of[lid]}」。按下述问题与修法重写，"
            "消除矛盾/补足漏洞，保持与世界观一致。"
            "只输出 JSON：{\"summary\":\"\",\"detail\":\"\",\"culture_local\":\"\"}"
        )
        fix_user = (f"[问题]{it.get('problem','')}\n[修法]{it.get('fix','')}\n\n"
                    f"[原 summary]{overview[lid]['summary']}\n[原 detail]{details[lid]['detail']}\n"
                    f"[原 culture]{details[lid]['culture_local']}\n\n只输出 JSON。")
        d = _json(llm, fix_sys, fix_user)
        if isinstance(d, dict):
            if str(d.get("summary", "")).strip():
                overview[lid]["summary"] = str(d["summary"]).strip()
            if str(d.get("detail", "")).strip():
                details[lid]["detail"] = str(d["detail"]).strip()
            if str(d.get("culture_local", "")).strip():
                details[lid]["culture_local"] = str(d["culture_local"]).strip()

    # 落库：原地丰富化
    for loc in canon_locs:
        ov = overview[loc.loc_id]
        det = details[loc.loc_id]
        repo.enrich_location(
            loc_id=loc.loc_id,
            summary=ov["summary"],
            detail=det["detail"],
            culture_local=det["culture_local"],
            level=ov["level"],
            parent=ov["parent"],
        )
    return {"locations": len(canon_locs), "issues": len(issues), "fixed": len(fixed)}


def build_factions(repo: Repository, llm: LLMClient | None = None, theme: str = "",
                   max_factions: int = 5) -> dict:
    """W3 势力系统：据世界观+地理（canon 地点）生成 3–5 个独特势力，作 game-like 一等实体。

    依赖：须在 build_world_skill + build_geography 之后调（拿厚地理 + 世界观 summary 做锚）。
    ① 总览：LLM 出 3–5 个势力（name/ideology 一句话/territory 引用 canon loc_id）。
       **据地理因地制宜，势力地盘必须从 canon 地点中选**。
    ② 逐个深写：detail + goals/methods/structure/history/secret + key_members(3–6 个核心)；
       喂入全部势力 summary 保差异、避免雷同。
    ③ 汇聚校验：对世界观+地理+所有势力做硬一致性校验（地盘冲突？信条矛盾？关系不闭合？）。
    ④ 修订：每势力至多一次重写。
    ⑤ 关系生成：再一次 LLM 出全部势力对之间的 relations（allied|hostile|infiltrates|neutral|tributary）。
    ⑥ 核心成员落 character_cards（supporting tier，slot_key=faction:成员名）。

    幂等：已有 source='w3' 势力则跳过；无 LLM / 无 canon 地理 → no-op。返回统计。"""
    if llm is None:
        return {"skipped": "no_llm"}
    if any(f.source == "w3" for f in repo.list_factions()):
        return {"skipped": "exists"}
    canon = [e for e in repo.list_entities()
             if e.type == "location" and (e.attributes or {}).get("canon")]
    if not canon:
        return {"skipped": "no_canon"}
    canon_locs = {e.entity_id: e.name for e in canon}
    world_blob = repo.bible_summaries_text(["settingCore", "culture", "rules", "history"])
    if not world_blob.strip():
        world_blob = repo.bible_sections_text(["settingCore", "culture"], max_chars=1500)
    geo_blob = "\n".join(f"- {l.name}（loc_id={l.loc_id}）: {l.summary or l.geo_full[:80]}"
                         for l in repo.list_locations() if l.loc_id in canon_locs)
    tp = repo.get_tone_profile()
    tone_hint = ""
    if tp and tp.is_set():
        tone_hint = f"（题材「{tp.genre}」，语域「{tp.register}」）"

    # ① 总览
    over_sys = (
        "你是小说世界观势力设定师。【任务：总览】据世界观与地理，给出 3–5 个独特、"
        f"互有张力的势力。{tone_hint}**据地理因地制宜**：territory 必须从 canon loc_id 中选。"
        "**严格符合世界观，不得编造与设定冲突的奇幻势力**。"
        "只输出 JSON：{\"factions\":[{\"name\":\"\",\"ideology\":\"\",\"territory\":[loc_id,…]}]}"
    )
    over = _json(llm, over_sys,
                 f"主题：{theme or '（未定）'}\n\n[世界观锚]\n{world_blob}\n\n"
                 f"[canon 地理]\n{geo_blob}\n\n只输出 JSON，最多 {max_factions} 个。")
    overview: list[dict] = []
    if isinstance(over, dict) and isinstance(over.get("factions"), list):
        for d in over["factions"][:max_factions]:
            if not isinstance(d, dict):
                continue
            name = str(d.get("name", "")).strip()
            if not name:
                continue
            ideology = str(d.get("ideology", "")).strip()
            terr = [t for t in (d.get("territory") or []) if t in canon_locs]
            overview.append({"name": name, "ideology": ideology, "territory": terr})
    if not overview:
        return {"skipped": "no_overview"}

    # ② 逐个深写
    sum_blob = "\n".join(f"· {f['name']}：{f['ideology']}" for f in overview)
    details: list[dict] = []
    for f in overview:
        terr_names = "、".join(canon_locs[t] for t in f["territory"]) or "（无明确地盘）"
        det_sys = (
            f"你是小说势力设定师。【任务：深写】势力「{f['name']}」。给 JSON："
            f"summary(1–2句){tone_hint}、detail(80–200字综述)、goals(短期/长期)、"
            "methods(典型手段)、structure(组织架构)、history(势力自身的简史)、"
            "secret(不可告人的事)、key_members([{name,role,note}] 3–6 个核心成员，"
            "给定 role 如领袖/副官/打手/情报头子等)。"
            "**保持与其它势力差异化，不雷同**；**严格符合世界观与地理**。"
            "只输出 JSON。"
        )
        det_user = (f"[世界观锚]\n{world_blob}\n\n[本势力地盘]\n{terr_names}\n\n"
                    f"[本势力一句话]\n{f['ideology']}\n\n"
                    f"[全部势力概览（差异化参考）]\n{sum_blob}\n\n请写「{f['name']}」。")
        d = _json(llm, det_sys, det_user)
        members = []
        if isinstance(d, dict) and isinstance(d.get("key_members"), list):
            for m in d["key_members"][:6]:
                if isinstance(m, dict) and str(m.get("name", "")).strip():
                    members.append({"name": str(m["name"]).strip(),
                                    "role": str(m.get("role", "")).strip(),
                                    "note": str(m.get("note", "")).strip()})
        details.append({
            "summary": (isinstance(d, dict) and str(d.get("summary", "")).strip()) or f["ideology"],
            "detail": (isinstance(d, dict) and str(d.get("detail", "")).strip()) or "",
            "goals": (isinstance(d, dict) and str(d.get("goals", "")).strip()) or "",
            "methods": (isinstance(d, dict) and str(d.get("methods", "")).strip()) or "",
            "structure": (isinstance(d, dict) and str(d.get("structure", "")).strip()) or "",
            "history": (isinstance(d, dict) and str(d.get("history", "")).strip()) or "",
            "secret": (isinstance(d, dict) and str(d.get("secret", "")).strip()) or "",
            "key_members": members,
        })

    # ③ 汇聚校验
    review_blob = "\n\n".join(
        f"【{f['name']}】{details[i]['summary']}\n地盘：{'、'.join(canon_locs[t] for t in f['territory']) or '无'}"
        f"\n目标：{details[i]['goals'][:120]}\n手段：{details[i]['methods'][:120]}"
        for i, f in enumerate(overview))
    rev_sys = (
        "你是势力一致性审校。【任务：校验】下列势力：有无**与世界观/地理冲突**？"
        "有无**地盘重叠不合理**？信条/手段有无**自相矛盾**？只列硬问题，最多 4 条。"
        "只输出 JSON：{\"issues\":[{\"faction_index\":0,\"problem\":\"\",\"fix\":\"\"}]}"
    )
    review = _json(llm, rev_sys, f"[世界观锚]\n{world_blob}\n\n{review_blob}\n\n只输出 JSON。")
    issues = []
    if isinstance(review, dict) and isinstance(review.get("issues"), list):
        issues = [it for it in review["issues"][:4]
                  if isinstance(it, dict) and isinstance(it.get("faction_index"), int)
                  and 0 <= it["faction_index"] < len(overview)]

    # ④ 修订
    fixed: set[int] = set()
    for it in issues:
        idx = it["faction_index"]
        if idx in fixed:
            continue
        fixed.add(idx)
        f = overview[idx]
        fix_sys = (
            f"你是势力设定师。【任务：修订】势力「{f['name']}」。按下述问题与修法重写关键字段，"
            "保持与世界观/地理一致，与其它势力差异化。"
            "只输出 JSON：{\"summary\":\"\",\"detail\":\"\",\"goals\":\"\",\"methods\":\"\"}"
        )
        fix_user = (f"[问题]{it.get('problem','')}\n[修法]{it.get('fix','')}\n\n"
                    f"[原 summary]{details[idx]['summary']}\n[原 detail]{details[idx]['detail']}\n\n只输出 JSON。")
        d = _json(llm, fix_sys, fix_user)
        if isinstance(d, dict):
            for k in ("summary", "detail", "goals", "methods"):
                if str(d.get(k, "")).strip():
                    details[idx][k] = str(d[k]).strip()

    # 落库（先建 faction，拿 id 给关系用）
    faction_ids: list[str] = []
    for i, f in enumerate(overview):
        fid = f"fac_{uuid.uuid4().hex[:6]}"
        faction_ids.append(fid)
        repo.upsert_faction(Faction(
            faction_id=fid, name=f["name"], ideology=f["ideology"],
            goals=details[i]["goals"], methods=details[i]["methods"],
            territory=f["territory"], structure=details[i]["structure"],
            key_members=details[i]["key_members"], history=details[i]["history"],
            relations=[], secret=details[i]["secret"],
            summary=details[i]["summary"], detail=details[i]["detail"],
            source="w3",
        ))

    # ⑤ 关系生成（一次 LLM 出全部对）
    rel_sys = (
        "你是势力关系设定师。【任务：关系】对下列势力，给出**两两之间**的关系（不必全列，只列有意义的）。"
        "kind ∈ allied|hostile|infiltrates|neutral|tributary；intensity ∈ [1,5]。"
        "只输出 JSON：{\"edges\":[{\"src\":fid,\"dst\":fid,\"kind\":\"\",\"intensity\":3,\"note\":\"\"}]}"
    )
    fac_blob = "\n".join(f"- {f['name']}（faction_id={faction_ids[i]}）：{details[i]['summary']}"
                         for i, f in enumerate(overview))
    rel = _json(llm, rel_sys, f"[势力列表]\n{fac_blob}\n\n只输出 JSON。")
    edges = []
    if isinstance(rel, dict) and isinstance(rel.get("edges"), list):
        valid_kinds = {"allied", "hostile", "infiltrates", "neutral", "tributary"}
        fid_set = set(faction_ids)
        for e in rel["edges"][:20]:
            if not isinstance(e, dict):
                continue
            src, dst = e.get("src"), e.get("dst")
            kind = str(e.get("kind", "")).strip()
            if src in fid_set and dst in fid_set and src != dst and kind in valid_kinds:
                edges.append({"src": src, "dst": dst, "kind": kind,
                              "intensity": max(1, min(5, int(e.get("intensity", 3) or 3))),
                              "note": str(e.get("note", "")).strip()})
    # 回写到各势力的 relations（出边视角）
    for i, fid in enumerate(faction_ids):
        out_edges = [{"target_faction_id": e["dst"], "kind": e["kind"],
                      "intensity": e["intensity"], "note": e["note"]}
                     for e in edges if e["src"] == fid]
        fac = repo.get_faction(fid)
        if fac:
            fac.relations = out_edges
            repo.upsert_faction(fac)

    # ⑥ 核心成员落 character_cards（supporting tier）+ 实体 + 关系回填 agent_id
    members_created = 0
    existing_names = {e.name for e in repo.list_entities() if e.type == "character"}
    for i, fid in enumerate(faction_ids):
        fac = repo.get_faction(fid)
        if not fac:
            continue
        new_members = list(fac.key_members)
        for m_idx, m in enumerate(new_members):
            name = m.get("name", "")
            if not name or name in existing_names:
                continue
            aid = f"named_{uuid.uuid4().hex[:6]}"
            repo.insert_entity(Entity(aid, "character", name, {"faction_id": fid}))
            existing_names.add(name)
            slot = f"faction:{fac.name}:{name}"
            repo.add_card(CharacterCard(
                card_id=f"card_{uuid.uuid4().hex[:6]}",
                agent_id=aid, tier="supporting", slot_key=slot, name=name,
                one_liner=f"{fac.name}·{m.get('role', '')}".strip("·"),
                defining_trait=m.get("role", ""),
                key_relation=f"隶属{fac.name}",
                backstory=m.get("note", ""),
            ))
            new_members[m_idx] = {**m, "agent_id": aid}
            members_created += 1
        fac.key_members = new_members
        repo.upsert_faction(fac)

    return {"factions": len(faction_ids), "issues": len(issues), "fixed": len(fixed),
            "relations": len(edges), "members_created": members_created}


def build_static_graph(repo: Repository) -> dict:
    """W5 知识图谱·静态边：纯本地、无 LLM。从已落库的 W2/W3/W4 抽出关系。

    建：
    - person → faction (member_of)：Entity.attributes.faction_id（W3 落卡时已写）。
    - faction → location (controls)：Faction.territory（W3 已校验全在 canon）。
    - faction → faction (allied/hostile/infiltrates/neutral/tributary)：从 W3 Faction.relations JSON 迁过来。
    - location → location (located_in)：Location.parent（W2 层级关系）。
    - faction → person (has_member)：与 member_of 互为反向，方便检索。

    静态边 intensity 默认 0.7（强且稳定），不衰减。幂等：已有同三元组替换（UNIQUE ON CONFLICT REPLACE）。"""
    edges = 0
    # ① 人→势力
    for e in repo.list_entities():
        if e.type != "character":
            continue
        fid = (e.attributes or {}).get("faction_id")
        if not fid:
            continue
        repo.upsert_edge(GraphEdge(src=e.entity_id, rel="member_of", dst=fid,
                                   meta={"source": "w3"}, intensity=0.7))
        repo.upsert_edge(GraphEdge(src=fid, rel="has_member", dst=e.entity_id,
                                   meta={"source": "w3"}, intensity=0.7))
        edges += 2
    # ② 势力↔势力 + 势力→地点
    for f in repo.list_factions():
        for tid in (f.territory or []):
            repo.upsert_edge(GraphEdge(src=f.faction_id, rel="controls", dst=tid,
                                       meta={"source": "w3"}, intensity=0.7))
            edges += 1
        for rel_edge in (f.relations or []):
            tgt = rel_edge.get("target_faction_id")
            kind = rel_edge.get("kind")
            intensity = float(rel_edge.get("intensity", 3)) / 5.0  # 1-5 → 0.2-1.0
            if not tgt or kind not in ("allied", "hostile", "infiltrates", "neutral", "tributary"):
                continue
            repo.upsert_edge(GraphEdge(src=f.faction_id, rel=kind, dst=tgt,
                                       meta={"source": "w3", "note": rel_edge.get("note", "")},
                                       intensity=intensity))
            edges += 1
    # ③ 地点→上级地点
    for loc in repo.list_locations():
        if loc.parent:
            repo.upsert_edge(GraphEdge(src=loc.loc_id, rel="located_in", dst=loc.parent,
                                       meta={"source": "w2"}, intensity=0.7))
            edges += 1
    return {"edges": edges}


def lock_canonical_geography(repo: Repository, llm: LLMClient | None = None,
                             max_locations: int = 8) -> list[str]:
    """W0 设定保真闸门·canonical 地名固化（治"planner 发明遮面街/镜面塔"式漂移）。

    锁定时把世界圣经地理里**真实点到或明确隐含的具体地点**抽成权威 location 实体
    （`attributes.canon=True`，part_id 暂空），供 planner 只能从中选/细化、子地点须从属。
    幂等：已存在 canon 地点则跳过；无 LLM / 无地理文本 → no-op（绝不臆造）。须在 build_master 前调。
    返回新建的 loc_id 列表。"""
    if llm is None:
        return []
    # 幂等：已固化过 canon 地点则不再重抽
    for e in repo.list_entities():
        if e.type == "location" and (e.attributes or {}).get("canon"):
            return []
    geo = repo.bible_sections_text(["geography", "settingCore", "culture"], max_chars=3000)
    if not geo.strip():
        return []
    system = (
        "你是小说世界观设定师。从给定的世界设定/地理文本中，抽取其中**真实点到或明确隐含的"
        "具体地点**（街区/建筑/机构/地标，如『霞飞路』『七十六号』『百乐门』『闸北废墟』），"
        "供全书统一引用。**只抽设定里确实存在的地名，绝不臆造与设定冲突的新地点**"
        "（尤其不要编造奇幻地名，如设定里并不存在的塔/祠堂/秘境/结界）。每个给："
        "name(地点名)、geo_full(方位/地形/气候/建筑/声光气味，≥1 句)、"
        "controlling_faction(掌控势力，可空)。"
        f"最多 {max_locations} 个。只输出 JSON 数组：[{{\"name\",\"geo_full\",\"controlling_faction\"}}]"
    )
    user = f"[世界设定/地理]\n{geo}\n\n只输出 JSON 数组。"
    try:
        raw = llm.complete(system + " 输出必须是合法 json。", user).strip().strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
        i, j = raw.find("["), raw.rfind("]")
        data = json.loads(raw[i:j + 1] if 0 <= i < j else raw)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    existing_names = {e.name for e in repo.list_entities() if e.type == "location"}
    out: list[str] = []
    for d in data[:max_locations]:
        if not isinstance(d, dict):
            continue
        name = str(d.get("name", "")).strip().strip("「」『』\"' ")
        if not name or name.isascii() or name in existing_names:
            continue
        lid = f"loc_{uuid.uuid4().hex[:6]}"
        repo.insert_entity(Entity(lid, "location", name, {"canon": True}))
        repo.upsert_location(Location(
            loc_id=lid, part_id="", name=name,
            geo_full=str(d.get("geo_full", "")).strip(),
            connects_to=[], controlling_faction=str(d.get("controlling_faction", "")).strip(),
            notable_items=[]))
        existing_names.add(name)
        out.append(lid)
    return out
