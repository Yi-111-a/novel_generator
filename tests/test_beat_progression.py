"""B1+B2：每章 ≥3 个各异节拍；导演逐场消费 beat（第 i 场喂 beats[i]，不重复演一拍）。"""
from __future__ import annotations

from novel_engine import db
from novel_engine.agent import CharacterAgent
from novel_engine.dilemma import DilemmaGenerator
from novel_engine.director import Director
from novel_engine.llm.mock import MockClient
from novel_engine.models import Entity, Fact, Foreshadow, KnowledgeItem, Persona
from novel_engine.monitors import Monitors
from novel_engine.planner import Planner
from novel_engine.repository import Repository
from novel_engine.validator import Validator


def _seed_repo() -> Repository:
    r = Repository(db.connect(":memory:"))
    r.insert_entity(Entity("loc_main", "location", "主场景", {}))
    for aid, name in [("hero", "云鹤子"), ("ally", "季拾遗"), ("villain", "墨渊")]:
        r.insert_entity(Entity(aid, "character", name, {}))
        r.insert_persona(Persona(agent_id=aid, name=name, want="求道",
                                 values=[{"name": "道心", "weight": 0.7}], fatal_flaw="执念"))
    r.append_fact(Fact("f_secret", "state", "旧案关键握在墨渊手里。", involved_entities=["villain"]))
    r.insert_knowledge(KnowledgeItem("villain", "f_secret", "旧案关键握在墨渊手里。", 1.0, 0))
    r.upsert_foreshadow(Foreshadow("fs_secret", "墨渊瞒着什么？", "f_secret", 1, True))
    return r


def test_chapter_has_at_least_three_distinct_beats():
    r = _seed_repo()
    p = Planner(r, llm=None, theme="证道")
    p.build_master(part_count=3, arcs_per_part=1)
    for _ in range(6):
        if p.next_chapter() is None:
            break
    for c in r.list_chapter_plans():
        assert len(c.beat_goals) >= 3, f"章{c.sequence_order}应有≥3拍"
        assert len(set(c.beat_goals)) == len(c.beat_goals), "同章节拍不得重复"


class _RecordingDilemma(DilemmaGenerator):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.beat_hints: list[str] = []

    def generate(self, tick, target=None, prefer_flaw=False, beat_hint="", beat_idx=0):
        self.beat_hints.append(beat_hint)
        return super().generate(tick, target=target, prefer_flaw=prefer_flaw,
                                beat_hint=beat_hint, beat_idx=beat_idx)


def test_director_consumes_beats_in_order():
    r = _seed_repo()
    planner = Planner(r, llm=None, theme="证道")
    planner.build_master(part_count=3, arcs_per_part=1)
    planner.plan_next_arc()
    ch = planner.next_chapter()
    gen = _RecordingDilemma(r, llm=None, theme="证道")
    d = Director(r, gen, CharacterAgent(r, MockClient()), Validator(r),
                 Monitors(r, flaw_max_free=2), planner=planner)

    # P1 多角色：K 个 actor 共用同一 beat_hint，走完 K 步才换下一拍
    K = min(len(ch.cast), 4) if ch.cast else 1
    total_steps = K * min(3, len(ch.beat_goals))
    for _ in range(total_steps):
        d.step()
    hints = [h for h in gen.beat_hints if h]
    # 每个 beat hint 应在 K 个连续步骤里重复出现（同一场里每个 actor 都收到同一拍提示）
    # 验证：前 K 步的 hint 都是 beats[0]，接下来 K 步是 beats[1]，依此类推
    for bi, beat in enumerate(ch.beat_goals[:3]):
        chunk = hints[bi * K: bi * K + K]
        assert all(h == beat for h in chunk), (
            f"beat[{bi}] 的 {K} 步提示应均为「{beat}」，实际：{chunk}"
        )
