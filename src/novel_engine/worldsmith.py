"""世界生成器（设计文档 §2 结构性节拍："调用世界生成 / 注入外部事件"）。

让剧情需要时**自动登场新角色 / 新道具**，并把它真正接进世界状态：
  - 落 Entity（character/object）+（角色还落 Persona）
  - 落一条 canonical fact + 一个"登场/现身"事件（故事时间推进、可被 SSE 广播）
  - 角色携带一条只有自己知道的秘密 → 制造新的信息不对称
  - 新角色并入张力最低的故事线，从而能参与后续两难

LLM 生成（受物理法则与主题约束）；无 LLM 时走确定性规则回退。
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass

from .llm.base import LLMClient
from .models import Entity, Event, Fact, KnowledgeItem, Persona
from .repository import Repository


@dataclass
class Introduction:
    kind: str  # character | object
    entity_id: str
    name: str
    event_id: str
    fact_id: str


class WorldSmith:
    def __init__(self, repo: Repository, llm: LLMClient | None = None, theme: str = "") -> None:
        self.repo = repo
        self.llm = llm
        self.theme = theme

    # ---------- 自动决定引入什么 ----------
    def introduce(self, tick: int) -> Introduction | None:
        chars = [e for e in self.repo.list_entities() if e.type == "character"]
        objs = [e for e in self.repo.list_entities() if e.type == "object"]
        # 角色偏多则补道具，否则补角色
        if len(chars) > len(objs) + 1:
            return self.introduce_object(tick)
        return self.introduce_character(tick)

    # ---------- 新角色 ----------
    def introduce_character(self, tick: int, name: str | None = None) -> Introduction:
        spec = self._character_spec(name)
        cid = f"char_gen_{uuid.uuid4().hex[:6]}"
        self.repo.insert_entity(Entity(cid, "character", spec["name"], {}))
        self.repo.insert_persona(
            Persona(
                agent_id=cid,
                name=spec["name"],
                want=spec.get("want", ""),
                values=spec.get("values", []) or [],
                fatal_flaw=spec.get("fatalFlaw", ""),
                obstacles=spec.get("obstacles", []) or [],
                cost_threshold={"note": spec.get("costThreshold", "")},
                voice=spec.get("voice", ""),
                mannerisms=spec.get("mannerisms", []) or [],
                motif_objects=[],
                arc_state={"last_change_tick": tick, "last_flaw_cost_tick": 0, "changed": False},
                cost_ledger=[],
            )
        )
        intro = spec.get("intro") or f"{spec['name']}出现在故事里。"
        event_id, fact_id = self._land(cid, spec["name"], "登场", intro, tick)

        # 携带一条只有自己知道的秘密 → 新的信息不对称
        secret = spec.get("secret")
        if secret:
            sfid = f"f_secret_{uuid.uuid4().hex[:6]}"
            self.repo.append_fact(Fact(sfid, "state", secret, story_time=tick, involved_entities=[cid]))
            self.repo.insert_knowledge(KnowledgeItem(cid, sfid, secret, 1.0, tick))

        self._attach_to_thread(cid)
        return Introduction("character", cid, spec["name"], event_id, fact_id)

    # ---------- 新道具 ----------
    def introduce_object(
        self, tick: int, name: str | None = None, holder_agent_id: str | None = None, chapter: int = 0
    ) -> Introduction:
        spec = self._object_spec(name)
        oid = f"obj_gen_{uuid.uuid4().hex[:6]}"
        self.repo.insert_entity(Entity(oid, "object", spec["name"], {"desc": spec.get("desc", "")}))
        intro = spec.get("intro") or f"{spec['name']}出现了。"
        event_id, fact_id = self._land(oid, spec["name"], "现物", intro, tick)
        # 物品归属：新得之物即写入持有者库存（防"凭空拥有"——有人得到才进库，且可追溯）
        if holder_agent_id is not None:
            from .models import InventoryItem

            self.repo.set_inventory(
                InventoryItem(oid, holder_agent_id=holder_agent_id, status="held",
                              acquired_chapter=chapter, note=spec.get("desc", ""))
            )
        return Introduction("object", oid, spec["name"], event_id, fact_id)

    # ---------- 公共：落事件 + 事实 ----------
    def _land(self, ent_id: str, name: str, action: str, content: str, tick: int) -> tuple[str, str]:
        event_id = f"ev_{uuid.uuid4().hex[:8]}"
        fact_id = f"f_{uuid.uuid4().hex[:8]}"
        self.repo.append_event(
            Event(event_id, story_time=tick, actors=[ent_id], action_type=action,
                  payload={"note": content}, perceivers=[ent_id])
        )
        self.repo.append_fact(
            Fact(fact_id, "event", content, story_time=tick, involved_entities=[ent_id], source_event_id=event_id)
        )
        return event_id, fact_id

    def _attach_to_thread(self, cid: str) -> None:
        threads = self.repo.list_threads()
        if not threads:
            return
        t = min(threads, key=lambda x: x.current_tension)
        if cid not in t.involved_agents:
            t.involved_agents.append(cid)
            self.repo.insert_thread(t)

    # ---------- 规格生成（LLM + 规则回退） ----------
    def _character_spec(self, name: str | None) -> dict:
        if self.llm is not None:
            spec = self._llm_spec("character", name)
            if spec:
                if name:
                    spec["name"] = name
                return spec
        from .casting import _distinct_name
        existing = [e.name for e in self.repo.list_entities() if e.type == "character"]
        nm = name or _distinct_name("", existing)
        return {
            "name": nm,
            "want": "在乱局里替自己谋一条生路",
            "values": [{"name": "自保", "weight": 0.6}, {"name": "旧日的承诺", "weight": 0.4}],
            "fatalFlaw": "疑心过重，先下手为强",
            "obstacles": ["来历不明", "无人信他"],
            "costThreshold": "可弃旧名，不可弃性命",
            "voice": "言辞闪烁，不肯交底",
            "mannerisms": ["压低斗笠", "指节叩刀鞘"],
            "intro": f"{nm}自雨幕里走出，谁也说不清他是敌是友。",
            "secret": f"{nm}此行另有目的，只是还不肯说。",
        }

    def _object_spec(self, name: str | None) -> dict:
        n = sum(1 for e in self.repo.list_entities() if e.type == "object") + 1
        if self.llm is not None:
            spec = self._llm_spec("object", name)
            if spec:
                if name:
                    spec["name"] = name
                return spec
        nm = name or f"无名遗物{n}"
        return {"name": nm, "desc": "一件来历成谜的旧物。", "intro": f"角落里翻出一件旧物——{nm}，似与往事相系。"}

    def _llm_spec(self, kind: str, name: str | None) -> dict | None:
        rules = "；".join(self.repo.get_physics_rules())
        names = "、".join(e.name for e in self.repo.list_entities())
        char_names = [e.name for e in self.repo.list_entities() if e.type == "character"]
        if kind == "character":
            schema = ('{"name","want","values":[{"name","weight"}],"fatalFlaw","obstacles":[],'
                      '"costThreshold","voice","mannerisms":[],"intro","secret"}')
            task = "设计一个**新登场角色**，要与已有角色形成张力或信息差。"
            dedup = (
                f"【去重硬约束】新名字不得与现有角色（{'、'.join(char_names)}）相同或近似，"
                f"不得同姓扎堆、不得共用末字，绝不用『无名客』等占位名。"
                if char_names else ""
            )
        else:
            schema = '{"name","desc","intro"}'
            task = "设计一件**新出现的道具/物件**，最好能牵出谜团或推动冲突。"
            dedup = "绝不用『无名遗物』等占位名，给它一个具体、有来历感的名字。"
        system = (
            f"你为一个主题为「{self.theme}」的故事扩充世界。{task}\n"
            f"必须符合世界硬约束：{rules or '（无）'}。"
            f"【取名规则】名字与字段须与作品语言一致：中文作品一律用中文名（外来名取音译，如『约翰』），"
            f"**不要用拉丁字母名（如 John）**。{dedup}只输出 JSON：{schema}"
        )
        user = f"已有实体：{names}。" + (f"指定名字：{name}。" if name else "请自拟贴合世界的名字。") + " 只输出 JSON。"
        try:
            raw = self.llm.complete(system, user)  # type: ignore[union-attr]
            text = raw.strip().strip("`")
            if text.lower().startswith("json"):
                text = text[4:].strip()
            i, j = text.find("{"), text.rfind("}")
            data = json.loads(text[i : j + 1] if 0 <= i < j else text)
            return data if isinstance(data, dict) and data.get("name") else None
        except Exception:
            return None
