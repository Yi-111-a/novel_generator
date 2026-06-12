"""§13.3 戏剧问题去重：相邻章不得问同一句（破"十章问同一句"循环）。离线确定性路径。"""
from __future__ import annotations

from novel_engine import db
from novel_engine.models import Entity, Fact, Foreshadow, KnowledgeItem, Persona
from novel_engine.planner import Planner
from novel_engine.repository import Repository


def _seed_repo() -> Repository:
    r = Repository(db.connect(":memory:"))
    r.insert_entity(Entity("loc_main", "location", "主场景", {}))
    for aid, name in [("hero", "云鹤子"), ("ally", "季拾遗"), ("villain", "墨渊")]:
        r.insert_entity(Entity(aid, "character", name, {}))
        r.insert_persona(Persona(agent_id=aid, name=name, want="求道"))
    r.append_fact(Fact("f_secret", "state", "旧案关键握在墨渊手里。", involved_entities=["villain"]))
    r.insert_knowledge(KnowledgeItem("villain", "f_secret", "旧案关键握在墨渊手里。", 1.0, 0))
    r.upsert_foreshadow(Foreshadow("fs_secret", "墨渊瞒着什么？", "f_secret", 1, True))
    return r


def test_q_grams_similarity_helpers():
    p = Planner(_seed_repo(), llm=None)
    assert p._question_similar("这一步会把局面推向何处？",
                               ["这一步会把局面推向何处？"]) is True
    assert p._question_similar("藏在背后的真相是什么？",
                               ["这段关系将走向何方？"]) is False


def test_distinct_question_avoids_recent():
    p = Planner(_seed_repo(), llm=None)
    recent = ["这一步会把局面推向何处？"]
    q = p._distinct_question("rising", recent)
    assert not p._question_similar(q, recent)


def test_generated_chapters_not_all_same_question():
    r = _seed_repo()
    p = Planner(r, llm=None, theme="证道")
    p.build_master(part_count=3)
    questions: list[str] = []
    for _ in range(8):
        ch = p.next_chapter()
        if ch is None:
            break
        # 模拟"该章已写完"，让下一章在新 base_seq 上生成
        ch.status = "done"
        r.upsert_chapter_plan(ch)
        if ch.dramatic_question and not ch.reveal_gate:
            questions.append(ch.dramatic_question)
    assert len(questions) >= 3
    # 相邻非里程碑章不得雷同
    for a, b in zip(questions, questions[1:]):
        assert not p._question_similar(a, [b]), f"相邻章问题雷同：{a} / {b}"
