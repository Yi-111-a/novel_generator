"""选角层（§1）：出生即建卡。功能位 → 角色卡 唯一映射，同一 slot 永不重抽（治"乱写名字"）。

纯引擎层：只依赖 repository / models / llm。.md 人类可读镜像由 server.dossier 负责。
"""
from __future__ import annotations

import json
import re
import uuid

from .llm.base import LLMClient
from .models import CharacterCard, Entity, InventoryItem, Persona
from .repository import Repository

# 显式化名/代号：从种子文本确定性抽取（治"种子写了化名秦书白却没人用"）。
# 匹配「沈砚（化名「秦书白」）」「化名秦书白」「代号夜莺」「假名李四」等。
_ALIAS_RE = re.compile(r'(?:化名|代号|假名|别名|对外称)[为叫是:：]?\s*[「『“"]?([^」』”"，。、；）)\s]{1,8})')


def pov_eligible(repo: Repository, agent_id: str, hero_id: str | None = None) -> bool:
    """该角色能否当叙事 POV——防"用反派视角写场 → 读者提前全知泄底"。
    规则：①主角永远可以；②无隐藏身份的人可以；③化名(主角/卧底自己的对外身份，is_alias)可以；
    ④藏着**反派真实身份**的人——身份未对读者揭示前**不可**当 POV（其视角会漏底），揭示后才可。"""
    if hero_id and agent_id == hero_id:
        return True
    ent = repo.get_entity(agent_id)
    idn = (ent.attributes or {}).get("identity") if ent else None
    if not idn:
        return True
    if idn.get("is_alias"):
        return True
    fid = idn.get("fact_id")
    return bool(fid and repo.reader_knows(fid))   # 反派隐藏身份：已揭示给读者才可当 POV


def lock_aliases(repo: Repository, *texts: str) -> dict[str, dict]:
    """B-Fix：从种子文本（protagonistWant / 世界圣经 / persona 描述）确定性抽取**显式化名**，
    把"主角对外用化名、真名只对自己与知情者"固化进 entity.identity。

    与 lock_hidden_identities 的区别：那是给**反派**藏真实头衔（public=中性称呼/true=真头衔）；
    这是给**主角/卧底**的对外化名（public=化名/true=真名）。须在 lock_hidden_identities 之前调，
    已设 identity 的角色跳过（幂等）。返回 {agent_id: identity}。"""
    from .models import Fact, KnowledgeItem
    blob = "\n".join(t for t in texts if t)
    result: dict[str, dict] = {}
    for p in repo.list_personas():
        ent = repo.get_entity(p.agent_id)
        if ent is None or ent.type != "character" or (ent.attributes or {}).get("identity"):
            continue
        # 在文本里找「{真名}…化名…X」：真名出现处后方就近的一个化名
        idx = blob.find(p.name)
        if idx < 0:
            continue
        window = blob[idx: idx + 60]   # 真名后 60 字内找化名
        m = _ALIAS_RE.search(window)
        if not m:
            continue
        alias = m.group(1).strip()
        if not alias or alias == p.name or alias.isascii():
            continue
        # 身份 fact：本人知道、外人不知 → 进揭示链（与 hidden_identities 同机制）
        fid = f"f_alias_{uuid.uuid4().hex[:6]}"
        truth = f"{p.name} 对外化名「{alias}」，其真实身份不可对外暴露。"
        repo.append_fact(Fact(fid, "state", truth, involved_entities=[p.agent_id]))
        repo.insert_knowledge(KnowledgeItem(p.agent_id, fid, truth, 1.0, 0))
        ident = {"public": alias, "true": p.name, "fact_id": fid, "is_alias": True}
        repo.update_entity_attributes(p.agent_id, {"identity": ident})
        result[p.agent_id] = ident
    return result

# 各 tier 需生成/填充的字段（戏份越高越全）
_TIER_FIELDS = {
    "extra": ["name", "one_liner", "voice_register", "defining_trait"],
    "supporting": ["name", "one_liner", "voice_register", "defining_trait",
                   "core_desire", "verbal_habits", "key_relation"],
    "lead": ["name", "one_liner", "voice_register", "defining_trait", "core_desire",
             "verbal_habits", "key_relation", "backstory", "fatal_flaw", "arc"],
}


def cast_or_get(repo: Repository, slot_key: str, tier: str = "supporting",
                context: str = "", llm: LLMClient | None = None) -> CharacterCard:
    """同一 slot 永远返回同一张卡；新建时同时落 entity + persona（可被扮演）+ inventory(motif)。"""
    existing = repo.get_card_by_slot(slot_key)
    if existing:
        return existing
    existing_names = [e.name for e in repo.list_entities() if e.type == "character"]
    spec = _llm_card_spec(slot_key, tier, context, llm, existing_names) or _fallback_card_spec(slot_key, tier, repo)
    # P4b：即便 LLM 也可能撞名 → 撞名则确定性改写为不冲突的新名
    if spec.get("name") in existing_names:
        spec["name"] = _distinct_name(spec.get("name", ""), existing_names)
    agent_id = f"cast_{uuid.uuid4().hex[:6]}"
    motif = spec.get("motif_objects", []) or []
    if not repo.entity_exists(agent_id):
        repo.insert_entity(Entity(agent_id, "character", spec["name"], {"slot": slot_key, "tier": tier}))
    repo.insert_persona(Persona(
        agent_id=agent_id, name=spec["name"],
        want=spec.get("core_desire", ""), values=[],
        fatal_flaw=spec.get("fatal_flaw", ""),
        voice=spec.get("voice_register", ""),
        mannerisms=[h for h in (spec.get("verbal_habits", "") or "").split("、") if h],
        motif_objects=motif,
        arc_state={"last_change_tick": 0, "last_flaw_cost_tick": 0, "changed": False},
        cost_ledger=[],
    ))
    card = CharacterCard(
        card_id=f"card_{uuid.uuid4().hex[:8]}", agent_id=agent_id, tier=tier, slot_key=slot_key,
        name=spec["name"], one_liner=spec.get("one_liner", ""),
        voice_register=spec.get("voice_register", ""), defining_trait=spec.get("defining_trait", ""),
        core_desire=spec.get("core_desire", ""), verbal_habits=spec.get("verbal_habits", ""),
        key_relation=spec.get("key_relation", ""), backstory=spec.get("backstory", ""),
        fatal_flaw=spec.get("fatal_flaw", ""), motif_objects=motif,
        relationship_map=spec.get("relationship_map", {}) or {}, arc=spec.get("arc", ""),
    )
    repo.add_card(card)
    for obj in motif:
        if not repo.entity_exists(obj):
            repo.insert_entity(Entity(obj, "object", obj, {}))
        if repo.get_inventory_item(obj) is None:
            repo.set_inventory(InventoryItem(obj, holder_agent_id=agent_id, status="held"))
    return card


def ensure_cards_for_personas(repo: Repository) -> list[str]:
    """锁定时为种子已知角色批量建卡：personas[0]=lead，其余=supporting。幂等。"""
    out: list[str] = []
    for i, p in enumerate(repo.list_personas()):
        if repo.get_card_for_agent(p.agent_id):
            continue
        tier = "lead" if i == 0 else "supporting"
        trait = (p.values[0]["name"] if p.values else "") or p.fatal_flaw or "（待定）"
        repo.add_card(CharacterCard(
            card_id=f"card_{uuid.uuid4().hex[:8]}", agent_id=p.agent_id, tier=tier,
            slot_key=f"seed_{p.agent_id}", name=p.name,
            one_liner=(f"怀着「{p.want}」之人" if p.want else ""),
            voice_register=p.voice, defining_trait=trait,
            core_desire=p.want, verbal_habits="、".join(p.mannerisms),
            fatal_flaw=p.fatal_flaw, motif_objects=p.motif_objects, arc="",
        ))
        out.append(p.agent_id)
    return out


def _json_card(llm: LLMClient, system: str, user: str):
    """解析 LLM 的 JSON 输出（对象），失败返回 None。"""
    try:
        raw = llm.complete(system + " 输出必须是合法 JSON。", user).strip().strip("`")
    except Exception:
        return None
    if raw.lower().startswith("json"):
        raw = raw[4:].strip()
    o, c = raw.find("{"), raw.rfind("}")
    try:
        return json.loads(raw)
    except Exception:
        if 0 <= o < c:
            try:
                return json.loads(raw[o:c + 1])
            except Exception:
                return None
        return None


def enrich_character_cards(repo: Repository, llm: LLMClient | None = None,
                           theme: str = "") -> dict:
    """W4 分层人物卡：把已有 lead/supporting 卡加厚到三维度（生理/社会/心理）+ 小传 + 弧线。

    依赖：须在 ensure_cards_for_personas + build_factions 之后；卡已存在但 W4 字段薄。
    主角(tier='lead')：极详（appearance≥80字 + social_role≥80字 + psychology≥80字 + backstory≥150字 + arc）。
    主配(tier='supporting')：精简（三段各 30–80字 + backstory 60–120字 + arc）；喂入隶属势力（W3 落库时
        Entity.attributes.faction_id 已挂）+ 主角做差异化。
    龙套(tier='extra')：跳过（cast_or_get JIT 处理）。
    校验回路：汇聚主角+主配的三维度，与世界观/势力做硬一致性校验，按 issue 修订（每卡 ≤1）。
    幂等：卡的 appearance 已非空则跳过；无 LLM / 无 lead 卡 → no-op。返回统计。"""
    if llm is None:
        return {"skipped": "no_llm"}
    cards = [c for c in repo.list_cards() if c.tier in ("lead", "supporting")]
    if not cards:
        return {"skipped": "no_cards"}
    todo = [c for c in cards if not (c.appearance or "").strip()]
    if not todo:
        return {"skipped": "exists"}
    leads = [c for c in todo if c.tier == "lead"]
    supports = [c for c in todo if c.tier == "supporting"]
    if not leads and not [c for c in cards if c.tier == "lead"]:
        return {"skipped": "no_lead"}

    # 世界观+势力锚
    world_blob = repo.bible_summaries_text(["settingCore", "culture", "rules", "history"])
    if not world_blob.strip():
        world_blob = repo.bible_sections_text(["settingCore", "culture"], max_chars=1200)
    fac_blob = repo.faction_summaries_text()
    tp = repo.get_tone_profile()
    tone_hint = f"（题材「{tp.genre}」，语域「{tp.register}」）" if (tp and tp.is_set()) else ""

    # persona 元数据查找
    persona_of = {p.agent_id: p for p in repo.list_personas()}
    faction_name_of = {f.faction_id: f.name for f in repo.list_factions()}

    def _ent_faction(aid: str) -> str:
        if not aid:
            return ""
        ent = repo.get_entity(aid)
        fid = (ent.attributes or {}).get("faction_id") if ent else ""
        return faction_name_of.get(fid, "")

    # ① 主角极详
    fixed = 0
    issues_count = 0
    for c in leads:
        p = persona_of.get(c.agent_id)
        persona_brief = ""
        if p:
            persona_brief = (f"欲望：{p.want}\n弱点：{p.fatal_flaw}\n说话方式：{p.voice}\n"
                             f"珍视：{'、'.join(v.get('name','') for v in (p.values or []))[:80]}")
        sys = (
            f"你是小说人物设定师。【任务：主角极详】角色「{c.name}」(lead)。给 JSON 五段："
            f"appearance(生理：外貌/身材/年龄/标志物，≥80字){tone_hint}、"
            "social_role(社会：出身/家庭/阶层/隶属势力/社会关系网，≥80字)、"
            "psychology(心理：性格/三观/恐惧/欲望细化，≥80字)、"
            "backstory(小传：成长关键事件，≥150字)、"
            "arc(角色弧线：起点特质→事件推动→转变终点，1-2句)。"
            "**严格符合世界观与势力，不得编造与设定冲突的细节**。只输出 JSON。"
        )
        user = (f"[世界观]\n{world_blob}\n\n[势力速览]\n{fac_blob}\n\n"
                f"[人物种子]\n{persona_brief}\n[既有卡片]\n"
                f"one_liner: {c.one_liner}\nbackstory: {c.backstory}\nkey_relation: {c.key_relation}\n\n"
                f"请写「{c.name}」的极详卡。只输出 JSON。")
        d = _json_card(llm, sys, user)
        if isinstance(d, dict):
            c.appearance = str(d.get("appearance", "")).strip() or c.appearance
            c.social_role = str(d.get("social_role", "")).strip() or c.social_role
            c.psychology = str(d.get("psychology", "")).strip() or c.psychology
            c.backstory = str(d.get("backstory", "")).strip() or c.backstory
            c.arc = str(d.get("arc", "")).strip() or c.arc
            repo.add_card(c)

    # ② 主配加厚
    lead_blob = "\n".join(f"· {c.name}：{c.one_liner or c.defining_trait}" for c in cards if c.tier == "lead")
    for c in supports:
        fac_name = _ent_faction(c.agent_id) or c.key_relation
        sys = (
            f"你是小说人物设定师。【任务：主配加厚】角色「{c.name}」(supporting)。给 JSON 五段："
            f"appearance(30-80字){tone_hint}、social_role(30-80字)、psychology(30-80字)、"
            "backstory(60-120字)、arc(1句)。"
            "**严格符合世界观与势力，不得编造与设定冲突的细节；与主角差异化**。只输出 JSON。"
        )
        user = (f"[世界观]\n{world_blob}\n\n[势力]\n{fac_blob}\n\n"
                f"[主角参照]\n{lead_blob}\n\n[本角色既有]\n"
                f"one_liner: {c.one_liner}\nrole: {c.defining_trait}\n隶属：{fac_name}\n"
                f"backstory: {c.backstory}\n\n请写「{c.name}」的加厚卡。只输出 JSON。")
        d = _json_card(llm, sys, user)
        if isinstance(d, dict):
            c.appearance = str(d.get("appearance", "")).strip() or c.appearance
            c.social_role = str(d.get("social_role", "")).strip() or c.social_role
            c.psychology = str(d.get("psychology", "")).strip() or c.psychology
            c.backstory = str(d.get("backstory", "")).strip() or c.backstory
            c.arc = str(d.get("arc", "")).strip() or c.arc
            repo.add_card(c)

    # ③ 汇聚校验
    review_blob = "\n\n".join(
        f"【{c.name}】tier={c.tier}\nappearance: {c.appearance[:120]}\n"
        f"social: {c.social_role[:120]}\npsy: {c.psychology[:120]}"
        for c in todo)
    rev_sys = (
        "你是小说人物一致性审校。【任务：人物校验】下列角色卡：有无**与世界观/势力冲突**？"
        "有无**自相矛盾**？主角与主配间有无明显**关系冲突或重复**？只列硬问题，最多 4 条。"
        "只输出 JSON：{\"issues\":[{\"name\":\"\",\"problem\":\"\",\"fix\":\"\"}]}"
    )
    name_to_card = {c.name: c for c in todo}
    review = _json_card(llm, rev_sys,
                        f"[世界观]\n{world_blob}\n\n[势力]\n{fac_blob}\n\n{review_blob}\n\n只输出 JSON。")
    if isinstance(review, dict) and isinstance(review.get("issues"), list):
        for it in review["issues"][:4]:
            if not isinstance(it, dict):
                continue
            nm = it.get("name", "")
            if nm not in name_to_card:
                continue
            issues_count += 1
            c = name_to_card[nm]
            fix_sys = (
                f"你是人物设定师。【任务：人物修订】角色「{nm}」。按下述问题与修法重写关键字段，"
                "保持与世界观/势力一致。只输出 JSON：{\"appearance\":\"\",\"social_role\":\"\","
                "\"psychology\":\"\",\"backstory\":\"\",\"arc\":\"\"}"
            )
            fix_user = (f"[问题]{it.get('problem','')}\n[修法]{it.get('fix','')}\n\n"
                        f"[原 appearance]{c.appearance}\n[原 social]{c.social_role}\n"
                        f"[原 psy]{c.psychology}\n[原 backstory]{c.backstory}\n\n只输出 JSON。")
            d = _json_card(llm, fix_sys, fix_user)
            if isinstance(d, dict):
                for k in ("appearance", "social_role", "psychology", "backstory", "arc"):
                    if str(d.get(k, "")).strip():
                        setattr(c, k, str(d[k]).strip())
                repo.add_card(c)
                fixed += 1
            del name_to_card[nm]

    return {"leads": len(leads), "supports": len(supports),
            "issues": issues_count, "fixed": fixed}


def _persona_brief(repo: Repository, p) -> str:
    """汇集角色本人的可判性别信息（含卡片字段），供逐个补判使用。"""
    ct = p.cost_threshold or {}
    note = ct.get("note", "") if isinstance(ct, dict) else str(ct)
    bits = [f"姓名：{p.name}", f"欲望：{p.want[:60]}", f"弱点：{(p.fatal_flaw or '')[:60]}",
            f"说话方式：{(p.voice or '')[:40]}", f"代价底线：{note[:50]}"]
    card = None
    getter = getattr(repo, "get_card_for_agent", None)
    if getter is not None:
        try:
            card = getter(p.agent_id)
        except Exception:
            card = None
    if card:
        bits += [f"一句话：{getattr(card, 'one_liner', '')[:50]}",
                 f"背景：{getattr(card, 'backstory', '')[:80]}",
                 f"关键关系：{getattr(card, 'key_relation', '')[:50]}"]
    return "；".join(b for b in bits if b.split("：", 1)[-1])


def _gender_by_relation(text: str) -> str:
    """关系词兜底：只认**明确指向本人**的关系词，不被简介里提到的他人带偏。
    『身为母亲/作为妻子』→ 本人女；『他的妻子/她的丈夫』→ 本人相反。"""
    t = text or ""
    self_female = any(k in t for k in ("身为母亲", "作为母亲", "为人母", "身为妻子", "作为妻子", "为人妻", "身为女儿", "作为姐姐", "作为妹妹"))
    self_male = any(k in t for k in ("身为父亲", "作为父亲", "为人父", "身为丈夫", "作为丈夫", "身为儿子", "作为哥哥", "作为弟弟"))
    # 反向：本人的配偶是谁 → 本人相反性别
    spouse_is_wife = any(k in t for k in ("他的妻子", "自己的妻子", "亡妻", "的妻子", "的新娘"))
    spouse_is_husband = any(k in t for k in ("她的丈夫", "自己的丈夫", "亡夫", "的丈夫"))
    if self_female or spouse_is_husband:
        if not (self_male or spouse_is_wife):
            return "女"
    if self_male or spouse_is_wife:
        if not (self_female or spouse_is_husband):
            return "男"
    return ""


def _infer_one_gender(llm: LLMClient, brief: str) -> str:
    """对单个角色聚焦判定，强制二选一（不允许未知）。失败返回 ''。"""
    system = (
        "判断这个角色**本人**的性别。简介里可能提到别人（配偶、母亲、宿敌），"
        "绝不要被他人带偏，只判断角色本人。结合姓名倾向、社会角色与背景综合判断，"
        "**必须**在『男』『女』中二选一，不得返回未知或其它。只输出一个字：男 或 女。"
    )
    try:
        raw = llm.complete(system, f"角色档案：\n{brief}").strip().strip("`").strip()
        for ch in raw:
            if ch in ("男", "女"):
                return ch
    except Exception:
        pass
    return ""


def infer_and_store_genders(repo: Repository, llm: LLMClient | None = None) -> dict[str, str]:
    """显式判断每个角色**本人**的性别并存入 persona.arc_state['gender']（不改 schema）。

    三层保证（瑕疵1 修复）：①批量 LLM 强制二选一；②仍空缺者逐个聚焦补判一次；
    ③再不行用"明确指向本人的关系词"确定性兜底。从自由文本统计代词的旧法已弃用（会被他人带偏）。
    无 LLM 时仅用关系词兜底。返回 {agent_id: '男'|'女'}。
    """
    personas = repo.list_personas()
    if not personas:
        return {}
    name_to_aid = {p.name: p.agent_id for p in personas}
    result: dict[str, str] = {}

    # ⓪ 种子/共创已声明性别的角色：直接采用，权威，不再 LLM 推断（轻量版方案）
    for p in personas:
        g = (p.arc_state or {}).get("gender")
        if g in ("男", "女"):
            result[p.agent_id] = g
    pending = [p for p in personas if p.agent_id not in result]

    # ① 批量判定（强制二选一）—— 只判尚未声明的
    if llm is not None and pending:
        lines = [f"- {_persona_brief(repo, p)}" for p in pending]
        system = (
            "你要判断每个角色**本人**的生理性别。**严重注意**：角色简介里常常提到别的人"
            "（其配偶、母亲、妹妹、宿敌等），**绝不要**被这些他人的性别带偏——只判断角色本人。"
            "例如『一个要找回亡妻的男人』，本人是男；『私藏着旧情人备份的她』，本人是女。"
            "结合姓名倾向、社会角色与背景综合判断，每个角色都**必须**在『男』『女』中二选一，"
            "不得返回未知。"
            '只输出 JSON：{"角色名":"男"或"女", ...}，不要任何解释。'
        )
        user = "角色清单（每行一个角色）：\n" + "\n".join(lines)
        try:
            raw = llm.complete(system, user).strip().strip("`")
            if raw.lower().startswith("json"):
                raw = raw[4:].strip()
            i, j = raw.find("{"), raw.rfind("}")
            data = json.loads(raw[i:j + 1] if 0 <= i < j else raw)
            for nm, g in data.items():
                aid = name_to_aid.get(nm)
                g = str(g).strip()
                if aid and g in ("男", "女"):
                    result[aid] = g
        except Exception:
            pass

    # ② 逐个补判仍空缺者 + ③ 关系词兜底
    for p in personas:
        if p.agent_id in result:
            continue
        brief = _persona_brief(repo, p)
        g = _infer_one_gender(llm, brief) if llm is not None else ""
        if g not in ("男", "女"):
            g = _gender_by_relation(brief)
        if g in ("男", "女"):
            result[p.agent_id] = g

    # 存储
    for aid, g in result.items():
        p = repo.get_persona(aid)
        if p is not None:
            arc = dict(p.arc_state or {})
            arc["gender"] = g
            repo.update_arc_state(aid, arc)
    return result


def lock_motif_canon(repo: Repository, llm: LLMClient | None = None) -> dict[str, str]:
    """问题3 修复：把每个角色关联意象/道具的**固定外观设定**(材质/颜色/来历/有无刻字)
    一次性定死并存入 entity.attributes['canon']，叙述层只能引用、不得每场另编
    （治"同一支钢笔刻字一会儿『婉』一会儿『平安』、材质一会儿犀飞利一会儿派克"）。
    无 LLM 时不生成（留空，narrator 不约束）。返回 {object_id: canon}。"""
    if llm is None:
        return {}
    result: dict[str, str] = {}
    name_of = {e.entity_id: e.name for e in repo.list_entities()}
    for p in repo.list_personas():
        for oid in (p.motif_objects or []):
            ent = repo.get_entity(oid)
            if ent is None or ent.type != "object":
                continue
            if (ent.attributes or {}).get("canon"):  # 已定则跳过（幂等）
                continue
            nm = ent.name
            if nm.isascii():  # 英文 id 残留，跳过
                continue
            system = (
                "你为小说设定一件随身道具的**固定外观**，供全书叙述统一引用。"
                "用一句话定死：材质/颜色/来历，以及**是否有刻字（若有，刻什么字、谁刻的，此后不得更改）**。"
                "不要写情节，只写这件物的固定设定。直接输出这句话，不要引号、不要解释。"
            )
            user = f"道具：{nm}（属于角色「{p.name}」）。角色背景：{(p.want or '')[:50]}。"
            try:
                canon = llm.complete(system, user).strip().strip("「」\"'").split("\n")[0][:120]
            except Exception:
                canon = ""
            if canon:
                repo.update_entity_attributes(oid, {"canon": canon})
                result[oid] = canon
    return result


def lock_hidden_identities(repo: Repository, llm: LLMClient | None = None) -> dict[str, dict]:
    """问题5 修复：把"某角色的隐藏身份/真实职务"（如『书局女子实为七十六号特工队长』）
    一次性固化，并**绑入揭示链**——治"身份揭示突兀（苏静突然成队长）"。

    机制：
      ① 为持有隐藏身份的角色建一条**他自己知道、主角不知道**的身份 fact →
         build_master 的 _build_reveal_chain 会自动把它收进揭示链当核心真相，
         于是该身份只能经"探索驱动揭示"在里程碑章被主角撞破（受控揭示，不再凭空）。
      ② 在 entity.attributes['identity'] 存 {public(揭示前中性称谓), true(揭示后头衔), fact_id}，
         叙述层据揭示状态给该角色"当前可用称谓"（未解锁只能用中性称呼）。
    无 LLM 时不生成（不臆造身份）。须在 build_master 之前调用。返回 {agent_id: identity}。"""
    if llm is None:
        return {}
    from .models import Fact, KnowledgeItem
    import uuid as _uuid
    personas = repo.list_personas()
    hero = personas[0].agent_id if personas else None
    result: dict[str, dict] = {}
    for p in personas:
        ent = repo.get_entity(p.agent_id)
        if ent is None or ent.type != "character":
            continue
        if (ent.attributes or {}).get("identity"):  # 幂等：已定则跳过
            continue
        # 该角色已知的、关于自己的秘密（cast_named_characters 可能已埋）
        my_secrets = [k.version_content for k in repo.get_agent_ledger(p.agent_id)
                      if any(p.agent_id in (f.involved_entities or [])
                             for f in [repo.get_fact(k.fact_id)] if f)]
        brief = _persona_brief(repo, p)
        system = (
            "你是小说设定师。判断这个角色是否藏着一个**对其他人保密的真实身份/职务/立场**"
            "（如：表面是书局女子、实则是特务队长；表面是账房、实则是卧底；表面是路人、实则是某派首脑）。"
            "若有，给出：①揭示前别人对他的**中性称呼**（如『书局的女子』『柜台后的人』，不暴露身份）；"
            "②揭示后可用的**真实头衔称呼**（如『苏队长』『陈处长』）；③用一句话陈述这条真实身份。"
            "若这个角色并无需要隐藏的特殊身份（就是个普通明面人物），返回 has_identity=false。"
            '只输出 JSON：{"has_identity":true或false,"public":"中性称呼","true":"真实头衔称呼","identity":"一句话身份真相"}'
        )
        user = f"角色档案：\n{brief}\n他自己知道的秘密：{('；'.join(my_secrets) or '（无）')}\n只输出 JSON。"
        try:
            raw = llm.complete(system, user).strip().strip("`")
            if raw.lower().startswith("json"):
                raw = raw[4:].strip()
            i, j = raw.find("{"), raw.rfind("}")
            data = json.loads(raw[i:j + 1] if 0 <= i < j else raw)
        except Exception:
            continue
        if not isinstance(data, dict) or not data.get("has_identity"):
            continue
        public = str(data.get("public", "")).strip()
        true_app = str(data.get("true", "")).strip()
        identity = str(data.get("identity", "")).strip()
        if not (public and true_app and identity):
            continue
        # 身份 fact：角色本人知道、主角不知道 → 自动进揭示链当核心真相（受控揭示）
        fid = f"f_idn_{_uuid.uuid4().hex[:6]}"
        repo.append_fact(Fact(fid, "state", identity, involved_entities=[p.agent_id]))
        repo.insert_knowledge(KnowledgeItem(p.agent_id, fid, identity, 1.0, 0))
        ident = {"public": public, "true": true_app, "fact_id": fid}
        repo.update_entity_attributes(p.agent_id, {"identity": ident})
        result[p.agent_id] = ident
    return result


def cast_named_characters(repo: Repository, bible_text: str, want_text: str,
                          llm: LLMClient | None = None) -> list[str]:
    """P4a：从世界圣经 + 主角欲望文本里抽取**被点名却尚无角色卡的核心人物**，建实体+卡。

    支持身份关联：若抽出的人物实为现有某角色的"别名/同一人"（如"还潮换名"），
    则不新建，而是把 {same_as/alias_of: 现有名} 写进现有卡的 relationship_map。
    无 LLM 时不做（避免误抽）。返回新建/关联的人物名列表。
    """
    if llm is None:
        return []
    existing = [(e.entity_id, e.name) for e in repo.list_entities() if e.type == "character"]
    existing_names = [nm for _, nm in existing]
    name_to_aid = {nm: aid for aid, nm in existing}
    system = (
        "你是选角统筹。从给定的世界设定与主角欲望文本中，找出**被明确点名、对核心冲突至关重要、"
        "但尚未在现有角色名单里**的人物（如被调查者、关键当事人、宿敌）。\n"
        "【优先判断同一人】很多悬疑设定里，被点名者其实就是现有某角色的别名/化名/换名归来/双重身份"
        "（例如『失踪的沈晚』与现有的『林晚』很可能是同一人改名归来）。遇到名字相近、命运呼应、"
        "或设定明示『以另一个名字回来』的情况，**必须**用 alias_of 指向那个现有名字，把他们当作同一人，"
        "而不是新建一个独立角色。只有确实是全新的第三方，才作为新人列出（alias_of=null）。\n"
        "【判断在场状态 presence】关键：很多核心被点名者其实是**缺席**的——已死、已失踪、被卖掉记忆、"
        "仅存在于主角回忆/寻找之中（例如『主角要找回的亡妻』『七年前消失的妹妹』）。这类人是故事的"
        "牵引与悬念，**绝不能**让他们一开场就活生生站在场景里说话，presence 必须填 absent。"
        "只有当前确实能到场活动、与人交锋的人物才填 present。\n"
        '只输出 JSON：{"characters":[{"name":"…","role_hint":"在冲突中的功能","alias_of":"现有名字或null",'
        '"presence":"present 或 absent","secret":"只有他知道的一条秘密或null"}]}'
    )
    user = (
        f"现有角色名单：{('、'.join(existing_names)) or '（无）'}\n\n"
        f"[世界设定]\n{bible_text[:1500]}\n\n[主角欲望]\n{want_text[:400]}\n\n只输出 JSON。"
    )
    try:
        raw = llm.complete(system, user).strip().strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
        i, j = raw.find("{"), raw.rfind("}")
        data = json.loads(raw[i:j + 1] if 0 <= i < j else raw)
    except Exception:
        return []
    out: list[str] = []
    for c in (data.get("characters") or []):
        if not isinstance(c, dict):
            continue
        name = str(c.get("name", "")).strip()
        if not name:
            continue
        alias_of = c.get("alias_of")
        # ① 别名/同一人：写进现有卡 relationship_map，不新建
        if alias_of and alias_of in name_to_aid:
            aid = name_to_aid[alias_of]
            card = repo.get_card_for_agent(aid)
            if card is not None:
                rm = dict(card.relationship_map or {})
                rm["same_as"] = sorted(set(rm.get("same_as", []) + [name])) if isinstance(rm.get("same_as"), list) else [name]
                card.relationship_map = rm
                repo.add_card(card)
                out.append(name)
            continue
        # ② 已存在同名 → 跳过
        if name in existing_names:
            continue
        # ③ 新建被点名核心人物（supporting 级，出生即建实体+persona+卡）。
        # absent=已死/已失踪/仅存于记忆者 → 标记后不被拉进 cast 真实行动（保住"缺席"的悬念）。
        absent = str(c.get("presence", "")).strip().lower() == "absent"
        agent_id = f"named_{uuid.uuid4().hex[:6]}"
        repo.insert_entity(Entity(agent_id, "character", name, {"named": True, "absent": absent}))
        repo.insert_persona(Persona(
            agent_id=agent_id, name=name,
            want=str(c.get("role_hint", "")), values=[],
            arc_state={"last_change_tick": 0, "last_flaw_cost_tick": 0, "changed": False,
                       "absent": absent},
            cost_ledger=[],
        ))
        repo.add_card(CharacterCard(
            card_id=f"card_{uuid.uuid4().hex[:8]}", agent_id=agent_id, tier="supporting",
            slot_key=f"named_{name}", name=name,
            one_liner=str(c.get("role_hint", "")),
            defining_trait=str(c.get("role_hint", "")),
        ))
        # 携带秘密 → 制造与主角的信息不对称
        secret = c.get("secret")
        if secret:
            from .models import Fact, KnowledgeItem
            sfid = f"f_named_{uuid.uuid4().hex[:6]}"
            repo.append_fact(Fact(sfid, "state", str(secret), involved_entities=[agent_id]))
            repo.insert_knowledge(KnowledgeItem(agent_id, sfid, str(secret), 1.0, 0))
        existing_names.append(name)
        name_to_aid[name] = agent_id
        out.append(name)
    return out


def _llm_card_spec(slot_key: str, tier: str, context: str, llm: LLMClient | None,
                   existing_names: list[str] | None = None) -> dict | None:
    if llm is None:
        return None
    fields = _TIER_FIELDS.get(tier, _TIER_FIELDS["extra"])
    schema = "{" + ", ".join(f'"{f}":"…"' for f in fields) + "}"
    # P4b 取名去重：把现有角色名喂入，明令不得撞名/同姓扎堆/共用末字
    names = existing_names or []
    dedup = ""
    if names:
        surnames = sorted({nm[0] for nm in names if nm})
        lastchars = sorted({nm[-1] for nm in names if nm})
        dedup = (
            f"\n【去重硬约束】现有角色名：{('、'.join(names))}。新名字必须："
            f"①不得与上述任何名字相同或近似；②不得用已出现的姓氏（{'、'.join(surnames)}）开头，避免同姓扎堆；"
            f"③不得与现有名字共用末字（{'、'.join(lastchars)}）；④绝不使用『无名客』『无名遗物』等占位名。"
        )
    system = (
        f"你是选角导演。为一个功能位设计一个**{tier}**级角色（戏份越高字段越全）。"
        f"要贴合语境、有辨识度、名字像真人。"
        f"【取名规则】名字须与作品语言一致：中文作品一律用中文名（外来名取音译，如『约翰』），"
        f"**不要用拉丁字母名（如 John）**。所有字段也用中文填写。{dedup}只输出 JSON：{schema}"
    )
    user = f"功能位：{slot_key}\n语境：{context or '（自定）'}\n只输出 JSON。"
    try:
        raw = llm.complete(system, user).strip().strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
        i, j = raw.find("{"), raw.rfind("}")
        data = json.loads(raw[i:j + 1] if 0 <= i < j else raw)
        return data if isinstance(data, dict) and data.get("name") else None
    except Exception:
        return None


# P4b：离线回退用的中文姓/名素材池（避免"无名客N"占位名）
_FALLBACK_SURNAMES = ["陆", "苏", "卫", "顾", "裴", "崔", "宋", "唐", "钟", "葛", "祝", "盛"]
_FALLBACK_GIVEN = ["承", "知", "明", "砚", "舟", "迟", "晏", "桓", "屿", "棠", "燃", "拙"]


def _distinct_name(base: str, existing: list[str]) -> str:
    """确定性生成一个与现有名字不撞名/不同姓扎堆/不共用末字的中文名。"""
    used_surnames = {nm[0] for nm in existing if nm}
    used_last = {nm[-1] for nm in existing if nm}
    for s in _FALLBACK_SURNAMES:
        if s in used_surnames:
            continue
        for g in _FALLBACK_GIVEN:
            cand = s + g
            if cand not in existing and cand[-1] not in used_last:
                return cand
    # 极端兜底：拼到不撞名为止
    for s in _FALLBACK_SURNAMES:
        for g in _FALLBACK_GIVEN:
            cand = s + g
            if cand not in existing:
                return cand
    return (base or "客") + str(len(existing) + 1)


def _fallback_card_spec(slot_key: str, tier: str, repo: Repository) -> dict:
    existing = [e.name for e in repo.list_entities() if e.type == "character"]
    return {
        "name": _distinct_name("", existing),
        "one_liner": f"在剧情里临时登场的{slot_key}",
        "voice_register": "言辞简短",
        "defining_trait": "来历不明",
        "core_desire": "为自己谋一条生路",
        "verbal_habits": "话到一半就停",
        "key_relation": "",
        "fatal_flaw": "疑心重",
        "motif_objects": [],
        "arc": "",
    }
