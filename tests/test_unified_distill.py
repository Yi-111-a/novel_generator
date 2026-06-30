from __future__ import annotations

import json

from novel_engine import db
from novel_engine.continuation import (
    build_chapter_blocks,
    extract_unified_blocks,
    get_knowledge_package,
    reduce_unified_distillation,
    synthesize_knowledge_package,
)
from novel_engine.llm.base import LLMClient
from novel_engine.llm.mock import MockClient
from novel_engine.models import SourceChapter, SourceDocument
from novel_engine.repository import Repository


class ScriptedClient(LLMClient):
    def __init__(self, responses: list[dict]):
        self.responses = [json.dumps(item, ensure_ascii=False) for item in responses]
        self.calls = 0

    def complete(self, system: str, user: str) -> str:
        return self.complete_at(system, user, None)

    def complete_at(self, system: str, user: str, temperature: float | None = None) -> str:
        response = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return response

    @property
    def name(self) -> str:
        return "Scripted"


def _repo_with_chapters(texts: list[str]) -> Repository:
    repo = Repository(db.connect(":memory:"))
    doc = SourceDocument(project_id="p1", filename="book.txt", format="txt", raw_text="", created_at="now")
    doc.id = repo.insert_source_document(doc)
    for index, text in enumerate(texts, 1):
        repo.insert_source_chapter(SourceChapter(
            project_id="p1",
            source_document_id=doc.id,
            chapter_no=index,
            title=f"第{index}章",
            text=text,
            word_count=len(text),
            summary=text[:80],
            created_at="now",
        ))
    return repo


def test_block_builder_preserves_complete_chapters():
    repo = _repo_with_chapters(["甲" * 8000, "乙" * 8000, "丙" * 8000])
    blocks = build_chapter_blocks(repo.list_source_chapters(), target_chars=15000, max_chapters=25)
    assert [block.chapter_nos for block in blocks] == [[1], [2], [3]]
    assert "".join(ch.text for block in blocks for ch in block.chapters) == "甲" * 8000 + "乙" * 8000 + "丙" * 8000


def test_unified_extraction_reduces_state_and_builds_package():
    chapter_text = "林渊来到黑石城。他击败赵青，成为内门弟子。老人看见玉佩后神色异常。"
    repo = _repo_with_chapters([chapter_text])
    extraction = {
        "coverage": {
            "chapters_received": [1],
            "chapters_processed": [1],
            "possibly_incomplete": [],
            "warnings": [],
        },
        "entities": [
            {
                "temp_id": "e1",
                "type": "character",
                "name": "林渊",
                "aliases": ["林师弟"],
                "first_seen_chapter": 1,
                "evidence": {"chapter": 1, "quote": "林渊来到黑石城"},
                "confidence": 0.98,
            },
            {
                "temp_id": "e2",
                "type": "location",
                "name": "黑石城",
                "aliases": [],
                "first_seen_chapter": 1,
                "evidence": {"chapter": 1, "quote": "黑石城"},
                "confidence": 0.95,
            },
        ],
        "events": [
            {
                "chapter": 1,
                "order": 1,
                "summary": "林渊击败赵青",
                "participants": ["林渊", "赵青"],
                "location": "黑石城",
                "time_expression": "",
                "cause": "",
                "result": "林渊成为内门弟子",
                "importance": "major",
                "evidence": {"chapter": 1, "quote": "他击败赵青"},
            }
        ],
        "state_changes": [
            {
                "chapter": 1,
                "order": 2,
                "entity": "林师弟",
                "field": "identity",
                "operation": "replace",
                "old_value": "外门弟子",
                "new_value": "内门弟子",
                "reason_event": "击败赵青",
                "certainty": "explicit",
                "evidence": {"chapter": 1, "quote": "成为内门弟子"},
            }
        ],
        "knowledge_assertions": [
            {
                "chapter": 1,
                "category": "stable_trait",
                "subject": "林渊",
                "claim": "擅长正面战斗",
                "certainty": "strongly_implied",
                "evidence": {"chapter": 1, "quote": "击败赵青"},
                "supersedes": [],
            }
        ],
        "plot_threads": [
            {
                "chapter": 1,
                "kind": "open",
                "question": "老人为何认识玉佩？",
                "resolution": "",
                "evidence": {"chapter": 1, "quote": "老人看见玉佩后神色异常"},
                "confidence": 0.9,
            }
        ],
        "style_samples": [
            {
                "chapter": 1,
                "type": "narration",
                "speaker": "",
                "reason": "简洁动作叙述",
                "text": "林渊来到黑石城。",
                "features": ["短句"],
            }
        ],
    }
    client = ScriptedClient([extraction])
    summary = extract_unified_blocks(repo, client, target_chars=40000, max_workers=1)
    assert summary["blocks"] == 1
    assert summary["calls"] == 1
    assert summary["needsReview"] == 0

    reduced = reduce_unified_distillation(repo)
    assert reduced["events"] == 1
    assert reduced["state_changes"] == 1
    assert reduced["threads"] == 1
    entity = next(row for row in repo.list_entities() if row.name == "林渊")
    assert reduced["final_states"][entity.entity_id]["identity"] == "内门弟子"

    result = synthesize_knowledge_package(repo, MockClient())
    assert result["stats"]["usedFallback"] is True
    package = get_knowledge_package(repo)["package"]
    assert package["characters"][0]["final_state"]["identity"] == "内门弟子"
    assert package["plot_threads"][0]["status"] == "open"
    assert package["style_profile"]["sample_count"] == 1


def test_b4_synthesis_backfills_profiles_and_resolves_threads():
    repo = _repo_with_chapters([
        "林渊在黑石城遇见赵青。林渊性格沉稳，渴望变强。",
        "林渊击败赵青，突破至筑基期。",
    ])
    extraction = {
        "coverage": {"chapters_received": [1, 2], "chapters_processed": [1, 2]},
        "entities": [
            {"temp_id": "E1", "type": "character", "name": "林渊", "aliases": [],
             "first_seen_chapter": 1, "evidence": {"chapter": 1, "quote": "林渊在黑石城"}, "confidence": 1.0},
            {"temp_id": "E2", "type": "character", "name": "赵青", "aliases": [],
             "first_seen_chapter": 1, "evidence": {"chapter": 1, "quote": "遇见赵青"}, "confidence": 0.9},
        ],
        "events": [
            {"chapter": 1, "order": 1, "summary": "林渊遇见赵青", "participants": ["E1", "E2"],
             "location": "黑石城", "importance": "major", "evidence": {"chapter": 1, "quote": "林渊在黑石城遇见赵青"}},
            {"chapter": 2, "order": 1, "summary": "林渊击败赵青", "participants": ["E1", "E2"],
             "location": "黑石城", "importance": "major", "evidence": {"chapter": 2, "quote": "林渊击败赵青"}},
        ],
        "state_changes": [
            {"chapter": 2, "order": 1, "entity": "E1", "field": "境界", "operation": "replace",
             "old_value": "练气期", "new_value": "筑基期", "certainty": "explicit",
             "evidence": {"chapter": 2, "quote": "突破至筑基期"}},
        ],
        "knowledge_assertions": [
            {"chapter": 1, "category": "stable_trait", "subject": "林渊", "claim": "性格沉稳",
             "certainty": "explicit", "evidence": {"chapter": 1, "quote": "林渊性格沉稳"}, "supersedes": []},
        ],
        "plot_threads": [
            {"chapter": 1, "kind": "open", "question": "林渊能否击败赵青？", "resolution": "",
             "evidence": {"chapter": 1, "quote": "遇见赵青"}, "confidence": 0.9},
        ],
        "style_samples": [
            {"chapter": 1, "type": "narration", "text": "林渊在黑石城遇见赵青。", "features": ["短句"]},
        ],
    }
    extract_unified_blocks(repo, ScriptedClient([extraction]), target_chars=40000, max_workers=1)
    reduce_unified_distillation(repo)

    global_synth = {
        "world_setting": {"summary": "一个修真世界"},
        "style_profile": {"overall_voice": "冷峻凌厉", "continuation_dos": ["保持短句"]},
        "relationship_graph": [
            {"src_name": "林渊", "dst_name": "赵青", "relation": "对手", "sentiment": "负面",
             "detail": "竞争内门名额", "chapters": [1, 2]},
        ],
        "thread_resolutions": [
            {"thread_id": "", "question": "林渊能否击败赵青？", "status": "resolved",
             "resolved_chapter": 2, "resolution": "林渊在第二章击败赵青", "evidence": "林渊击败赵青"},
        ],
        "entity_merge_candidates": [{"names": ["赵青", "赵青儿"], "reason": "名字相近", "confidence": 0.4}],
    }
    char_profiles = {
        "profiles": [
            {"id": "", "name": "林渊", "identity": "黑石城弟子", "role": "主角",
             "one_liner": "沉稳坚韧的修真少年", "personality": ["沉稳", "坚韧"],
             "core_desire": "变强", "goals": ["突破筑基"], "speech_style": "少言",
             "growth_arc": "从练气到筑基", "evidence_chapters": [1, 2], "confidence": 0.9},
            {"id": "", "name": "赵青", "identity": "黑石城弟子", "role": "对手",
             "one_liner": "高傲的同门", "personality": ["高傲"], "confidence": 0.7},
        ]
    }
    result = synthesize_knowledge_package(repo, ScriptedClient([global_synth, char_profiles]))
    package = result["package"]

    assert result["stats"]["usedFallback"] is False
    assert result["stats"]["profiledCharacters"] == 2
    assert package["world_setting"]["summary"] == "一个修真世界"
    assert package["style_profile"]["overall_voice"] == "冷峻凌厉"
    # 文风画像的确定性样本统计仍保留
    assert package["style_profile"]["sample_count"] == 1

    lin = next(c for c in package["characters"] if c["name"] == "林渊")
    assert lin["one_liner"] == "沉稳坚韧的修真少年"
    assert "沉稳" in lin["personality"]
    assert lin["profiled"] is True
    # 程序演算的书末状态不被覆盖
    assert lin["final_state"]["境界"] == "筑基期"

    rel = package["relationship_graph"][0]
    assert rel["relation"] == "对手"

    thread = package["plot_threads"][0]
    assert thread["status"] == "resolved"
    assert thread["resolved_chapter"] == 2

    assert any(u.get("category") == "entity_merge" for u in package["uncertainties"])


def test_invalid_coverage_is_recovered_by_splitting_block():
    repo = _repo_with_chapters(["第一章正文。" * 100, "第二章正文。" * 100])
    invalid = {
        "coverage": {"chapters_received": [1, 2], "chapters_processed": [1]},
        "entities": [], "events": [], "state_changes": [],
        "knowledge_assertions": [], "plot_threads": [], "style_samples": [],
    }
    left = {
        "coverage": {"chapters_received": [1], "chapters_processed": [1]},
        "entities": [], "events": [], "state_changes": [],
        "knowledge_assertions": [], "plot_threads": [], "style_samples": [],
    }
    right = {
        "coverage": {"chapters_received": [2], "chapters_processed": [2]},
        "entities": [], "events": [], "state_changes": [],
        "knowledge_assertions": [], "plot_threads": [], "style_samples": [],
    }
    client = ScriptedClient([invalid, left, right])
    summary = extract_unified_blocks(repo, client, target_chars=10000, max_chapters=25, max_workers=1)
    assert summary["blocks"] == 1
    assert summary["calls"] == 3
    assert summary["recovered"] == 1
    assert summary["needsReview"] == 0
