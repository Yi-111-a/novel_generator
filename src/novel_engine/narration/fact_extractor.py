"""B1 FactExtractor（大纲驱动重构脚手架）：从刚写好的正文**抽出结构化事实**，按隔离规则回写状态库。

把"真相"从"模拟产生"翻成"从正文抽出"——但仍是唯一、不可变、隔离的真相源。
抽取用约束层低温（complete_at temperature=0）；只抽不二次创作。无 LLM → 空 delta（不臆造）。

B1-a 阶段：独立组件，单测验证"facts 落库 + 隔离（只 pov∪present 拿到 knowledge）+ 道具易手 + 揭示"。
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field

from ..llm.base import LLMClient
from ..models import CharacterChapterLog, Entity, Event, Fact, KnowledgeItem
from ..propagation import Propagator
from ..repository import Repository

EXTRACT_TEMPERATURE = 0.0  # 约束层：确定性抽取，不创作


@dataclass
class SceneDelta:
    new_facts: list[dict] = field(default_factory=list)   # [{content, involved:[ids], location}]
    reveals: list[str] = field(default_factory=list)       # 命中并揭示的 fact_id（∩ may_reveal）
    item_transfers: list[dict] = field(default_factory=list)
    character_beats: list[dict] = field(default_factory=list)
    chosen_value: str = ""
    emotion: dict = field(default_factory=dict)            # {emotion, intensity, cause}
    cost: str = ""


def _uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


class FactExtractor:
    def __init__(self, repo: Repository, llm: LLMClient | None = None,
                 propagator: Propagator | None = None) -> None:
        self.repo = repo
        self.llm = llm
        self.propagator = propagator or Propagator(repo)

    def extract(self, prose: str, pov: str, present: list[str], spec) -> SceneDelta:
        """从正文抽变更。spec 提供 may_reveal（只允许揭示这些）。无 LLM → 空 delta。"""
        if self.llm is None or not (prose or "").strip():
            return SceneDelta()
        may = list(getattr(spec, "may_reveal", []) or [])
        present_names = "、".join(present) or "（无）"
        system = (
            "你是叙事事实抽取器。从给定小说正文里**只抽取实际发生的结构化变更**，不要二次创作、不要推断未写出的事。"
            "输出 JSON：{"
            "new_facts:[{content:'谁做了什么/发生了什么(一句)', involved:[在场相关角色名], location:''}], "
            "reveals:[本场被揭开的线索/真相，只能从给定的可揭示清单里选], "
            "item_transfers:[{obj:'器物名', to:'现持有者名', status:'transferred|lost|consumed|destroyed|sacrificed'}], "
            "character_beats:[{name:'角色名', action:'本场行为', psychology:'心理变化', intention:'下一步意图', items_changed:['物品名']}], "
            "chosen_value:'POV 本场做出的关键抉择/行动转折——只要 POV 做了一个**有后果的选择或决断**"
            "（如决定取走/放下某物、决定跟踪/摊牌/退避/出手、决定信任或怀疑某人），就**简短写出**它；"
            "真的什么决断都没有才留空', "
            "emotion:{emotion:'', intensity:0到1, cause:''}, "
            "cost:'POV 付出的代价(没有则空)'}\n"
            "【道具】凡正文里被人**拿走/交出/塞入/藏起/丢失/夺取/消耗/销毁/献祭**的具体器物（信件、纸条、戏票、耳环、"
            "钥匙、照片、凶器等），都要登进 item_transfers，哪怕它是本场第一次出现。"
            "status 说明：transferred=换手给某人，lost=遗失/不知去向，consumed=被消耗用尽（如药剂喝掉），"
            "destroyed=被物理摧毁（如烧毁/打碎），sacrificed=被献祭/以此为代价交换（如用怀表换通道）。"
            "被 consumed/destroyed/sacrificed 的物品之后**不可再出现**。"
        )
        user = (f"POV：{pov}\n在场：{present_names}\n可揭示清单（reveals 只能从这里选）：{('、'.join(may) or '无')}\n\n"
                f"正文：\n{prose[:3000]}\n\n只输出 JSON。")
        try:
            raw = self.llm.complete_at(system, user, EXTRACT_TEMPERATURE).strip().strip("`")
        except Exception:
            return SceneDelta()
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
        i, j = raw.find("{"), raw.rfind("}")
        try:
            data = json.loads(raw[i:j + 1] if 0 <= i < j else raw)
        except Exception:
            return SceneDelta()
        if not isinstance(data, dict):
            return SceneDelta()
        em = data.get("emotion") if isinstance(data.get("emotion"), dict) else {}
        # reveals 只保留在 may_reveal 内的（公平谜题：不许越权揭示）
        reveals = [r for r in (data.get("reveals") or []) if r in may] if may else []
        return SceneDelta(
            new_facts=[f for f in (data.get("new_facts") or []) if isinstance(f, dict) and f.get("content")],
            reveals=reveals,
            item_transfers=[t for t in (data.get("item_transfers") or []) if isinstance(t, dict) and t.get("obj")],
            character_beats=[b for b in (data.get("character_beats") or [])
                             if isinstance(b, dict) and b.get("name")],
            chosen_value=str(data.get("chosen_value", "")).strip(),
            emotion=em,
            cost=str(data.get("cost", "")).strip(),
        )

    def commit(self, delta: SceneDelta, pov: str, present: list[str], tick: int,
               chapter=None) -> list[str]:
        """按隔离规则回写。返回创建的 event_id 列表（供 director 把场链到事件）。
        Fix C 焦点化：每条事实只给 **POV + 该事实真实参与者（限本场 cast 内）** 感知，
        旁观者不自动获知（治"整 cast 越权知情"）。"""
        present_set = set(present)
        name_to_id = {p.name: p.agent_id for p in self.repo.list_personas()}
        for e in self.repo.list_entities():
            name_to_id.setdefault(e.name, e.entity_id)
        beat_id = getattr(chapter, "chapter_id", None)
        loc = (chapter.location_ids[0] if chapter and chapter.location_ids else None)

        event_ids: list[str] = []
        for nf in delta.new_facts:
            content = str(nf.get("content", "")).strip()
            if not content:
                continue
            involved = [name_to_id.get(n, n) for n in (nf.get("involved") or []) if n]
            # Fix C 焦点化：感知者 = POV + 该事实参与者（限 cast 内）；旁观者不自动获知。
            perceivers_f = list(dict.fromkeys(
                [pov] + [a for a in involved if a in present_set]))
            eid, fid = _uid("ev"), _uid("f")
            self.repo.append_event(Event(
                event_id=eid, story_time=tick, actors=involved or [pov],
                action_type="narrated",
                payload={"content": content, "chosen_value": delta.chosen_value},
                location_id=loc, perceivers=perceivers_f, beat_id=beat_id,
            ))
            self.repo.append_fact(Fact(
                fact_id=fid, fact_type="event", canonical_content=content,
                structured={"involved": involved}, story_time=tick,
                location_id=loc, involved_entities=involved or [pov], source_event_id=eid,
            ))
            self.propagator.perceive(fid, content, perceivers_f, tick, eid)
            event_ids.append(eid)

            # W5 知识图谱·增量：本事件参与者两两 related_to 互相升 intensity；
            # 知情者→事实 knows 边（事实节点用 fid）。剧情焦点自然浮上，久未交互的边由章末衰减。
            ch_seq_now = getattr(chapter, "sequence_order", 0) if chapter else 0
            bump = getattr(self.repo, "bump_edge_attention", None)
            if bump and involved:
                # related_to：参与者两两（限 character，不包含 location/object）
                char_ids = [a for a in involved
                            if (self.repo.get_entity(a) and self.repo.get_entity(a).type == "character")]
                for i in range(len(char_ids)):
                    for j in range(i + 1, len(char_ids)):
                        a, b = char_ids[i], char_ids[j]
                        bump(a, "related_to", b, ch_seq_now,
                             meta_patch={"last_event": eid})
                        bump(b, "related_to", a, ch_seq_now,
                             meta_patch={"last_event": eid})
                # knows：感知者→事实
                for who in perceivers_f:
                    bump(who, "knows", fid, ch_seq_now,
                         delta=0.25, meta_patch={"event": eid})

        # B2/B6.1 道具易手 → inventory。叙事道具（正文里被易手、但还没登记成实体的器物）
        # 在此**入册**（来自正文、由抽取器标记 → 合法登记，非凭空），再 transfer_item。
        ch_seq = getattr(chapter, "sequence_order", 0) if chapter else 0
        existing_objs = {e.entity_id for e in self.repo.list_entities() if e.type == "object"}
        for t in delta.item_transfers:
            name = str(t.get("obj", "")).strip()
            oid = name_to_id.get(name)
            if not oid or oid not in existing_objs:
                # B6.1：未登记的叙事道具 → 入册（限 2-8 字中文具体器物名，过滤泛指/英文残留）
                if name and 2 <= len(name) <= 8 and not name.isascii():
                    oid = _uid("obj")
                    self.repo.insert_entity(Entity(oid, "object", name, {"source": "narrated"}))
                    existing_objs.add(oid)
                    name_to_id[name] = oid
                else:
                    continue
            status = str(t.get("status", "transferred"))
            to = name_to_id.get(str(t.get("to", "")))
            _GONE = ("lost", "consumed", "destroyed", "sacrificed")
            note = f"第{ch_seq}章：{name}→{t.get('to', '') or ('已' + status if status in _GONE else '遗失')}"
            self.repo.transfer_item(
                oid, None if status in _GONE else to, ch_seq,
                note=note, status=status)
            if chapter is not None and to and oid not in (chapter.items_present or []):
                chapter.items_present.append(oid)
                self.repo.upsert_chapter_plan(chapter)

        # B2 揭示 → 揭示链 prereq 门控 + POV 习得真相 + 正规 commit_reveals 推读者账本
        if delta.reveals:
            self._commit_reveals(delta.reveals, pov, tick, ch_seq)

        # 情绪余温 / 代价
        if delta.emotion.get("emotion"):
            try:
                self.repo.bump_emotion(pov, str(delta.emotion.get("emotion")),
                                       float(delta.emotion.get("intensity", 0.5) or 0.5),
                                       str(delta.emotion.get("cause", "")), tick)
            except Exception:
                pass
        if delta.cost:
            try:
                self.repo.append_cost(pov, delta.cost)
            except Exception:
                pass
        self._commit_character_beats(delta, name_to_id, present_set, chapter)
        return event_ids

    def _commit_character_beats(self, delta: SceneDelta, name_to_id: dict[str, str],
                                present_set: set[str], chapter=None) -> None:
        if chapter is None or not delta.character_beats:
            return
        ch_seq = int(getattr(chapter, "sequence_order", 0) or 0)
        if not ch_seq:
            return
        for beat in delta.character_beats:
            name = str(beat.get("name", "")).strip()
            aid = name_to_id.get(name, name)
            if aid not in present_set:
                continue
            items = [str(x).strip() for x in (beat.get("items_changed") or []) if str(x).strip()]
            self.repo.insert_character_log(CharacterChapterLog(
                agent_id=aid,
                chapter_seq=ch_seq,
                actions=str(beat.get("action", "")).strip(),
                psychology=str(beat.get("psychology", "")).strip(),
                intention=str(beat.get("intention", "")).strip(),
                items_changed=items,
            ))

    def _commit_reveals(self, fids: list[str], pov: str, tick: int, ch_seq: int) -> list[str]:
        """B2 公平谜题：只揭示**前置线索已发现**的揭示链节点；POV 当下习得该真相；
        再走正规 commit_reveals 把 POV 持有的版本推给读者账本（POV 不知则跳过）。"""
        from .reveal import RevealPlan, commit_reveals

        nodes = self.repo.list_reveal_nodes()
        discovered = {n.node_id for n in nodes if n.discovered}
        to_reader: list[str] = []
        for fid in fids:
            # 找指向该 fact、尚未发现、且 prereq 全部已发现的节点
            unlockable = [n for n in nodes if n.fact_id == fid and not n.discovered
                          and all(p in discovered for p in (n.prereq_node_ids or []))]
            if not unlockable and any(n.fact_id == fid and not n.discovered for n in nodes):
                continue  # 有节点但 prereq 未满 → 不许越级揭示（线索还没出来不能揭真相）
            for n in unlockable:
                self.repo.mark_node_discovered(n.node_id, ch_seq or tick)
                discovered.add(n.node_id)
            # POV 探索所得：当下习得这条真相（若还不知道，且有 canonical 事实可习得）
            fact = self.repo.get_fact(fid)
            if fact is not None and not self.repo.agent_knows_fact(pov, fid):
                self.repo.insert_knowledge(KnowledgeItem(
                    pov, fid, fact.canonical_content, 0.9, tick, fact.source_event_id))
            to_reader.append(fid)
        if to_reader:
            pos = len(self.repo.list_scenes()) + 1
            return commit_reveals(self.repo, RevealPlan(reveal=to_reader), pov, pos)
        return []
