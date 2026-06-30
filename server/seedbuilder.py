"""种子工坊后端：对话共创、完成度推导、SeedDraft → Repository。

种子草稿在服务层以「camelCase dict」形态流转（与前端契约一致）。
**共创完全由 LLM 驱动**（对话 + 结构化抽取）；不做任何离线兜底——
未配置 LLM 或对话 API 不响应时直接抛 SeedChatError。
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from typing import Any

from novel_engine import db
from novel_engine.llm.base import LLMClient
from novel_engine.models import Ending, Entity, Fact, KnowledgeItem, Persona, Thread
from novel_engine.naming_generator import reconcile_project_names
from novel_engine.repository import Repository

# 完成度清单（每项都要求该层"全部内容"填完）
CHECKLIST_DEF = [
    ("immutable", "不可变层全填：世界设定 + 地理 + 文化 + 物理法则"),
    ("theme", "意图层全填：主题 + 主角欲望"),
    ("endings", "≥2 个候选结局，且每个含 摘要+主题表达+触发条件"),
    ("personas", "≥2 个角色，且每个 persona 全部核心字段填完"),
    ("asymmetry", "至少一个初始信息不对称（不同角色对关键事实认知不同）"),
]

# 角色被视为"填完"所需的核心字段
_PERSONA_FIELDS = ["name", "want", "values", "fatalFlaw", "obstacles", "costThreshold", "voice", "mannerisms"]


def empty_draft() -> dict[str, Any]:
    return {
        "worldBible": {
            "settingCore": "",
            "geography": "",
            "culture": "",
            "physicsRules": [],
            "protagonistWant": "",
            "theme": "",
            "candidateEndings": [],
        },
        "personas": [],
        "completeness": completeness({"worldBible": {}, "personas": []}),
    }


def _filled(v: Any) -> bool:
    """非空判定：字符串非空、列表非空。"""
    if isinstance(v, str):
        return bool(v.strip())
    if isinstance(v, (list, tuple)):
        return len(v) > 0
    return v is not None


def _persona_complete(p: dict[str, Any]) -> bool:
    return all(_filled(p.get(f)) for f in _PERSONA_FIELDS)


def _ending_complete(e: dict[str, Any]) -> bool:
    return _filled(e.get("summary")) and _filled(e.get("themeExpression")) and _filled(e.get("requiredConditions"))


def completeness(draft: dict[str, Any]) -> dict[str, Any]:
    wb = draft.get("worldBible", {}) or {}
    personas = draft.get("personas", []) or []
    endings = wb.get("candidateEndings", []) or []
    done = {
        # 不可变层：四项全填
        "immutable": all(_filled(wb.get(k)) for k in ("settingCore", "geography", "culture", "physicsRules")),
        # 意图层：主题 + 主角欲望
        "theme": _filled(wb.get("theme")) and _filled(wb.get("protagonistWant")),
        # ≥2 结局且每个完整
        "endings": len(endings) >= 2 and all(_ending_complete(e) for e in endings),
        # ≥2 角色且每个 persona 全部核心字段填完
        "personas": len(personas) >= 2 and all(_persona_complete(p) for p in personas),
        # 信息不对称（种子层无逐 agent 已知事实 → 用"≥2 角色"近似；建世界时真正落地）
        "asymmetry": len(personas) >= 2,
    }
    checklist = [{"key": k, "label": label, "done": done.get(k, False)} for k, label in CHECKLIST_DEF]
    return {"ready": all(c["done"] for c in checklist), "checklist": checklist}


class SeedChatError(RuntimeError):
    """共创对话失败（未配置 LLM 或 API 未正常响应）。直接抛给前端，不做离线兜底。"""


_COCREATE_SYS = """你是一名资深小说编辑，正在和用户**共创**一部小说的"种子"。
你的职责：①与用户自然对话，引导他把世界观、主题、主角、冲突、候选结局聊清楚；
②从对话中**抽取/更新结构化种子**。每一轮都基于用户最新发言增量更新。

务必只输出一个 JSON 对象（不要 markdown、不要多余文字）：
{
  "reply": "给用户的自然语言回应（中文，简洁、有温度，并适当追问还缺的部分）",
  "draftPatch": {
    "worldBible": {
      "settingCore": "…", "geography": "…", "culture": "…",
      "physicsRules": ["…"], "protagonistWant": "…", "theme": "…",
      "candidateEndings": [
        {"id":"end_x","summary":"…","themeExpression":"…","requiredConditions":["…"],"activeWeight":0.5}
      ]
    },
    "personas": [
      {"id":"p_x","name":"…","gender":"男或女","want":"…","values":[{"name":"…","weight":0.8}],
       "fatalFlaw":"…","obstacles":["…"],"costThreshold":"…","voice":"…",
       "mannerisms":["…"],"motifObjects":["黄符","账本"],"arcState":"…","costLedger":[]}
    ]
  }
}
规则：
- 每个角色都要标明 gender（"男"或"女"），由你根据角色设定判断；这能避免后续叙述把性别写错。
- draftPatch 只包含你这一轮**有把握**的字段；没聊到的别瞎填，留给后续轮次逐步补齐。
- 最终种子必须把所有层填完才算就绪：
  · 不可变层：settingCore + geography + culture + physicsRules 四项都要有；
  · 意图层：theme + protagonistWant；候选结局 ≥2，且每个含 summary + themeExpression + requiredConditions；
  · 角色 ≥2，且每个角色都要填全：name/want/values/fatalFlaw/obstacles/costThreshold/voice/mannerisms；
  · motifObjects 必须是**中文物品名**（2-6 字，如「黄符」「账本」「青衍长剑」），**不要**用拼音/英文/obj_ 前缀，系统会自动派 ID；
  · 初始信息不对称：让不同角色对关键事实持不同认知。
- 主动引导用户把上述缺口逐个补上；当全部就绪时，在 reply 里告诉用户"可以开始写作了"。"""


def cocreate(draft: dict[str, Any], history: list[dict[str, Any]], user_message: str, llm: LLMClient | None) -> tuple[dict[str, Any], str]:
    """真正由 LLM 驱动的共创：返回 (更新后的草稿, 给用户的回复)。

    无 LLM 或 API 调用失败 → 抛 SeedChatError（不做任何离线兜底）。
    """
    if llm is None:
        raise SeedChatError("未配置 LLM API。请先在「设置」里填写 API Key、Base URL 与模型名。")

    tail = "\n".join(f"{m['role']}: {m['content']}" for m in history[-6:])
    user = (
        f"[当前种子草稿]\n{json.dumps(draft, ensure_ascii=False)}\n\n"
        f"[最近对话]\n{tail}\n\n[用户最新发言]\n{user_message}"
    )
    try:
        raw = llm.complete(_COCREATE_SYS, user)
    except Exception as e:  # API 未响应/报错 → 直接报错
        raise SeedChatError(f"对话 API 未正常响应：{e}") from e

    data = _parse_json(raw)
    if data is None:
        raise SeedChatError("AI 返回的内容无法解析为结构化种子（请重试或更换模型）。")

    reply = str(data.get("reply", "")).strip() or "（已更新种子）"
    patch = data.get("draftPatch", {}) or {}
    _merge_patch(draft, patch)
    _normalize_draft(draft)
    draft["completeness"] = completeness(draft)
    return draft, reply


def _to_float(v: Any, default: float = 0.4) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _normalize_draft(draft: dict[str, Any]) -> None:
    """把 LLM 可能给出的脏数据规整成前端契约要求的形状，避免前端崩溃。"""
    wb = draft.setdefault("worldBible", {})
    wb.setdefault("physicsRules", [])
    endings = wb.get("candidateEndings") or []
    for i, e in enumerate(endings):
        if not isinstance(e, dict):
            continue
        e.setdefault("id", f"end_{i + 1}")
        e.setdefault("summary", "")
        e.setdefault("themeExpression", "")
        e.setdefault("requiredConditions", [])
        e["activeWeight"] = _to_float(e.get("activeWeight"), 0.4)
    wb["candidateEndings"] = [e for e in endings if isinstance(e, dict)]
    for p in draft.get("personas", []) or []:
        if not isinstance(p, dict):
            continue
        vals = p.get("values") or []
        norm_vals = []
        for v in vals:
            if isinstance(v, dict):
                norm_vals.append({"name": str(v.get("name", "")), "weight": _to_float(v.get("weight"), 0.5)})
            elif isinstance(v, str):  # LLM 偶尔给纯字符串
                norm_vals.append({"name": v, "weight": 0.5})
        p["values"] = norm_vals
        # 性别规整：只接受 男/女，其它（含空、英文）一律置空，留给事后推断兜底
        g = str(p.get("gender", "")).strip()
        p["gender"] = g if g in ("男", "女") else ""
        for k in ("obstacles", "mannerisms", "motifObjects", "costLedger"):
            if not isinstance(p.get(k), list):
                p[k] = p.get(k, []) if isinstance(p.get(k), list) else []


def _parse_json(raw: str) -> dict[str, Any] | None:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        # 容错：截取第一个 { 到最后一个 }
        i, j = text.find("{"), text.rfind("}")
        if 0 <= i < j:
            try:
                obj = json.loads(text[i : j + 1])
                return obj if isinstance(obj, dict) else None
            except json.JSONDecodeError:
                return None
        return None


def _merge_patch(draft: dict[str, Any], patch: dict[str, Any]) -> None:
    wb = draft.setdefault("worldBible", {})
    for k, v in (patch.get("worldBible", {}) or {}).items():
        if v not in (None, "", []):
            wb[k] = v
    personas_patch = patch.get("personas")
    if personas_patch:
        by_id = {p.get("id"): p for p in draft.get("personas", []) if p.get("id")}
        for p in personas_patch:
            pid = p.get("id") or f"p_{len(by_id) + 1}"
            p["id"] = pid
            by_id[pid] = {**by_id.get(pid, {}), **p}
        draft["personas"] = list(by_id.values())


_FUTURE_OUTLINE_RE = re.compile(
    r"(前[一二三四五六七八九十百\d]+章节奏|第\s*[一二三四五六七八九十百\d]+\s*章|第\s*[一二三四五六七八九十百\d]+\s*卷)"
)


def _world_history_sections(wb: dict[str, Any]) -> list[tuple[str, str, str]]:
    """Keep future plot outlines out of canonical world history.

    Seed drafts often use ``worldBible.history`` as a convenient place for
    "chapter 1...chapter 10..." notes. Those notes are useful planning material,
    but if we store them as ``history`` the writer treats future cases as already
    established world facts and starts expanding later units too early.
    """
    history = str(wb.get("history", "") or "").strip()
    if not history:
        return []
    if not _FUTURE_OUTLINE_RE.search(history):
        return [("history", "历史/背景", history)]

    canonical = str(wb.get("preStoryHistory", "") or wb.get("backstory", "") or "").strip()
    if not canonical:
        canonical = (
            "原草稿的 history 字段包含章节节奏或未来剧情提纲，不能作为世界历史、既定背景或正文可提前展开的事实。"
            "开局历史只以设定内核、文化规则、人物草稿中明确发生在故事开始前的事实为准；"
            "未来单元必须按卷纲滚动展开。"
        )
    planning = (
        "【非历史】以下内容只是未来规划，不得当作已发生背景、世界史或正文可提前展开的事实：\n"
        f"{history}"
    )
    return [
        ("history", "历史/背景", canonical),
        ("planning_notes", "未来提纲（非既定历史）", planning),
    ]


# ---------------- SeedDraft → Repository ----------------
def build_repo_from_draft(draft: dict[str, Any], db_path: str = ":memory:") -> Repository:
    """把锁定的种子草稿落地成一套可供写作链路继续使用的世界状态。

    db_path 给文件路径时世界状态落盘（重启可恢复）；默认 :memory: 供测试。
    """
    conn: sqlite3.Connection = db.connect(db_path, check_same_thread=False)
    repo = Repository(conn)
    wb = draft.get("worldBible", {}) or {}

    repo.set_world_bible(
        setting_core=wb.get("settingCore", ""),
        culture={"note": wb.get("culture", "")},
        geography={"note": wb.get("geography", "")},
        physics_rules=wb.get("physicsRules", []) or [],
        protagonist_want=wb.get("protagonistWant", ""),
        theme=wb.get("theme", ""),
    )
    # §12 世界圣经全量存档：把草稿里的世界观逐节原文入库（不摘要），供规划层按需检索喂入。
    pr = wb.get("physicsRules", []) or []
    section_entries = [
        ("settingCore", "设定内核", wb.get("settingCore", "")),
        ("geography", "地理", wb.get("geography", "")),
        ("culture", "文化/势力", wb.get("culture", "")),
        ("rules", "世界规则", "\n".join(str(x) for x in pr) if isinstance(pr, list) else str(pr)),
        ("lexicon", "专有名词", wb.get("lexicon", "")),
    ]
    section_entries.extend(_world_history_sections(wb))
    for section, title, body in section_entries:
        repo.add_bible_section(section, title, body, source="user")

    # 可选的三层大纲合同：让外部 Agent / 导入脚本在不新增数据库结构的前提下，
    # 把用户明确给出的体量、分卷蓝图与首个锁定单元交给原生 Planner。
    # 合同仍落在既有 world_bible_sections(outline_contract) 中，并由
    # story_contract 的统一归一化与校验逻辑管理。
    outline_contract = draft.get("outlineContract")
    if isinstance(outline_contract, dict) and outline_contract:
        from novel_engine.story_contract import save_story_contract

        save_story_contract(repo, dict(outline_contract))

    personas = draft.get("personas", []) or []
    # 实体：角色 + 关联意象（道具）+ 一个默认地点
    repo.insert_entity(Entity("loc_main", "location", "主场景", {}))
    for p in personas:
        repo.insert_entity(Entity(p["id"], "character", p["name"], {}))
        # 物品条目按"中文名"语义解析：
        #   · 已是 obj_ 前缀 → 视作 ID 直接落（兼容旧草稿；name=去前缀的占位串）
        #   · 中文/任何非 obj_ 前缀串 → 视作显示名，hash 派一个 obj_xxxxxx ID（避免拼音占位名进正文）
        # persona.motif_objects 存最终 ID 列表（与 entity_id 对齐）。
        resolved_motifs: list[str] = []
        for obj in p.get("motifObjects", []) or []:
            obj = str(obj).strip()
            if not obj:
                continue
            if obj.startswith("obj_"):
                # 兼容：旧契约/手工录入的 ID。name 用去前缀+换空格的弱回退；
                # 注意这里仍可能漏出拼音占位名，但仅用于向后兼容已有数据。
                eid = obj
                display = obj[4:].replace("_", " ").strip() or obj
            else:
                # 新契约：中文显示名 → 派 hash ID
                eid = f"obj_{hashlib.md5(obj.encode('utf-8')).hexdigest()[:6]}"
                display = obj
            if not repo.entity_exists(eid):
                repo.insert_entity(Entity(eid, "object", display, {}))
            resolved_motifs.append(eid)
        repo.insert_persona(
            Persona(
                agent_id=p["id"],
                name=p["name"],
                want=p.get("want", ""),
                values=p.get("values", []) or [],
                fatal_flaw=p.get("fatalFlaw", ""),
                obstacles=p.get("obstacles", []) or [],
                cost_threshold={"note": p.get("costThreshold", "")},
                voice=p.get("voice", ""),
                mannerisms=p.get("mannerisms", []) or [],
                motif_objects=resolved_motifs,
                # 作者/共创声明的性别写入 arc_state（权威，narrator 优先采用，跳过事后推断）
                arc_state={"last_change_tick": 0, "last_flaw_cost_tick": 0, "changed": False,
                           **({"gender": p["gender"]} if p.get("gender") in ("男", "女") else {})},
                cost_ledger=[],
            )
        )

    # 候选结局
    for e in wb.get("candidateEndings", []) or []:
        repo.upsert_ending(
            Ending(
                ending_id=e.get("id", e.get("summary", "end")[:8]),
                summary=e.get("summary", ""),
                theme_expression=e.get("themeExpression", ""),
                required_conditions=e.get("requiredConditions", []) or [],
                active_weight=float(e.get("activeWeight", 0.5)),
            )
        )

    # 故事线：主线（前两位角色）+ 可选支线（其余）
    ids = [p["id"] for p in personas]
    if ids:
        repo.insert_thread(
            Thread(
                thread_id="thread_main",
                central_question=wb.get("theme") or wb.get("protagonistWant") or "核心冲突走向何方？",
                involved_agents=ids[:2] if len(ids) >= 2 else ids,
                priority_weight=0.9,
                current_tension=0.2,
            )
        )
    if len(ids) > 2:
        repo.insert_thread(
            Thread(
                thread_id="thread_side",
                central_question="支线角色的立场如何摇摆？",
                involved_agents=ids[2:],
                priority_weight=0.4,
                current_tension=0.1,
            )
        )

    # 初始信息不对称：主角持有前提事实；反派独握一条秘密真相（主角不知）→ 伏笔指向它。
    if ids:
        protagonist = personas[0]
        antagonist = personas[-1]
        f_premise = Fact(
            "fact_premise",
            "event",
            f"{protagonist['name']}怀着{protagonist.get('want', '一桩心事')}，重回风暴中心。",
            story_time=0,
            location_id="loc_main",
            involved_entities=[protagonist["id"]],
        )
        repo.append_fact(f_premise)
        repo.insert_knowledge(KnowledgeItem(protagonist["id"], f_premise.fact_id, f_premise.canonical_content, 1.0, 0))

        if antagonist["id"] != protagonist["id"]:
            f_secret = Fact(
                "fact_secret",
                "event",
                f"那桩旧案真正的关键，掌握在{antagonist['name']}手里，尚无人知晓。",
                story_time=0,
                involved_entities=[antagonist["id"]],
            )
            repo.append_fact(f_secret)
            repo.insert_knowledge(KnowledgeItem(antagonist["id"], f_secret.fact_id, f_secret.canonical_content, 1.0, 0))
            # must_resolve 伏笔指向这条真相 → 诚实性闸门的考点
            from novel_engine.models import Foreshadow

            repo.upsert_foreshadow(
                Foreshadow(
                    foreshadow_id="fs_secret",
                    question=f"那桩旧案，{antagonist['name']}究竟瞒着什么？",
                    linked_fact_id="fact_secret",
                    planted_discourse_pos=1,
                    must_resolve=True,
                )
            )
            # 给中间角色一个被扭曲的版本（versionContent ≠ canonical）→ 演示误传
            if len(personas) >= 3:
                mid = personas[1]
                repo.insert_knowledge(
                    KnowledgeItem(mid["id"], f_premise.fact_id, f"据传，{protagonist['name']}回来另有所图（真假难辨）。", 0.5, 0)
                )

    reconcile_project_names(repo)
    return repo
