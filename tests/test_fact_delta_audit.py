from __future__ import annotations

import json

from novel_engine import db
from novel_engine.llm.base import LLMClient
from novel_engine.models import ChapterPlan, Fact
from novel_engine.narration.fact_delta import (
    _subject_relevant,
    audit_fact_delta,
    commit_fact_delta,
)
from novel_engine.repository import Repository


def test_subject_relevant_aligns_name_variants_and_rejects_others():
    proposed = {"林晚"}
    assert _subject_relevant("林晚", proposed)
    assert _subject_relevant("死者林晚", proposed)  # 包含匹配
    assert not _subject_relevant("程行", proposed)
    assert not _subject_relevant("", proposed)


class _FactLLM(LLMClient):
    def complete(self, system: str, user: str) -> str:
        return self.complete_at(system, user, 0.0)

    def complete_at(self, system: str, user: str, temperature: float) -> str:
        if "小说事实抽取器" in system:
            return json.dumps(
                {
                    "assertions": [
                        {
                            "subject": "陈野",
                            "predicate": "shop_experience_years",
                            "value": "3",
                            "fact_class": "narration",
                            "source_text": "陈野已经经营这家店三年",
                            "immutable": False,
                        }
                    ]
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "conflicts": [
                    {
                        "type": "canonical_fact_conflict",
                        "source_text": "陈野已经经营这家店三年",
                        "established_fact": "陈野第1章首次进入店铺",
                        "message": "经营三年与首次进入直接矛盾",
                        "repair": "改为陈野今天第一次进入店铺",
                    }
                ]
            },
            ensure_ascii=False,
        )


def test_fact_delta_blocks_direct_canon_conflict_and_commits_only_after_accept():
    repo = Repository(db.connect(":memory:"))
    repo.append_fact(
        Fact(
            fact_id="fd_old",
            fact_type="narrative_assertion",
            canonical_content="陈野.shop_experience_years=0",
            structured={
                "assertions": [
                    {
                        "subject": "陈野",
                        "predicate": "shop_experience_years",
                        "value": "0",
                        "fact_class": "narration",
                        "source_text": "陈野第1章首次进入店铺",
                        "source_chapter": 1,
                    }
                ]
            },
            story_time=1,
        )
    )
    plan = ChapterPlan("c4", "a", 4)
    prose = "陈野已经经营这家店三年。"
    result = audit_fact_delta(repo, plan, prose, _FactLLM(), {})

    assert result.violations[0]["type"] == "canonical_fact_conflict"
    assert not any(f.story_time == 4 for f in repo.list_facts())

    commit_fact_delta(repo, result.assertions, chapter_no=4)
    assert any(f.story_time == 4 for f in repo.list_facts())


def test_claim_is_not_treated_as_objective_canon():
    class ClaimLLM(_FactLLM):
        def complete_at(self, system: str, user: str, temperature: float) -> str:
            if "小说事实抽取器" in system:
                return json.dumps(
                    {
                        "assertions": [
                            {
                                "subject": "嫌疑人",
                                "predicate": "innocent",
                                "value": "true",
                                "fact_class": "claim",
                                "source_text": "他说自己没有杀人",
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            raise AssertionError("claims must not enter contradiction judgment")

    repo = Repository(db.connect(":memory:"))
    result = audit_fact_delta(
        repo,
        ChapterPlan("c2", "a", 2),
        "他说自己没有杀人。",
        ClaimLLM(),
        {},
    )
    assert result.assertions[0]["fact_class"] == "claim"
    assert result.violations == []


def test_only_same_subject_established_facts_are_compared():
    """① 主体裁剪：审计时只把同主体的旧事实喂进去，不同主体的被过滤掉。"""
    captured: dict = {}

    class CaptureLLM(_FactLLM):
        def complete_at(self, system: str, user: str, temperature: float) -> str:
            if "小说事实抽取器" in system:
                return json.dumps({"assertions": [{
                    "subject": "陈野", "predicate": "shop_experience_years", "value": "3",
                    "fact_class": "narration", "source_text": "陈野已经经营这家店三年",
                }]}, ensure_ascii=False)
            captured["established"] = json.loads(user)["established_facts"]
            return json.dumps({"conflicts": []}, ensure_ascii=False)

    repo = Repository(db.connect(":memory:"))
    for fid, subj, txt in [
        ("fd_a", "陈野", "陈野第1章首次进入店铺"),
        ("fd_b", "程行", "程行三年前已死亡"),  # 不同主体，应被过滤
    ]:
        repo.append_fact(Fact(
            fact_id=fid, fact_type="narrative_assertion",
            canonical_content=f"{subj}.x=y",
            structured={"assertions": [{
                "subject": subj, "predicate": "x", "value": "y",
                "fact_class": "narration", "source_text": txt, "source_chapter": 1,
            }]},
            story_time=1,
        ))

    audit_fact_delta(repo, ChapterPlan("c4", "a", 4), "陈野已经经营这家店三年。", CaptureLLM(), {})
    subjects = {row["subject"] for row in captured.get("established", [])}
    assert subjects == {"陈野"}  # 程行（不同主体）已被裁掉


def test_scene_token_bridges_same_crime_scene_across_name_variants():
    """方案1 现场归并：旧事实"17号储藏室"与本章"17/18共用隔墙"主体名各异、互不包含，
    但共享门牌号 17号 → 被拉到一起比对，且事实带上 scene_tokens 供 LLM 对齐。"""
    captured: dict = {}

    class CaptureLLM(_FactLLM):
        def complete_at(self, system: str, user: str, temperature: float) -> str:
            if "小说事实抽取器" in system:
                return json.dumps({"assertions": [{
                    "subject": "17号与18号共用隔墙", "predicate": "crack_orientation", "value": "纵向",
                    "fact_class": "narration", "source_text": "17号与18号共用隔墙有一条纵向裂缝",
                }]}, ensure_ascii=False)
            captured["established"] = json.loads(user)["established_facts"]
            captured["proposed"] = json.loads(user)["proposed_objective_facts"]
            return json.dumps({"conflicts": []}, ensure_ascii=False)

    repo = Repository(db.connect(":memory:"))
    for fid, subj, txt in [
        ("fd_w", "锦澜湾17号别墅地下室储藏室", "17号储藏室南墙有一条横向水泥缝"),  # 共享 17号 → 纳入
        ("fd_p", "林晚", "林晚是死者"),  # 无门牌号、异主体 → 过滤
    ]:
        repo.append_fact(Fact(
            fact_id=fid, fact_type="narrative_assertion", canonical_content=f"{subj}.x=y",
            structured={"assertions": [{
                "subject": subj, "predicate": "wall", "value": "横向",
                "fact_class": "narration", "source_text": txt, "source_chapter": 4,
            }]},
            story_time=4,
        ))

    prose = "17号与18号共用隔墙有一条纵向裂缝。"
    audit_fact_delta(repo, ChapterPlan("c5", "a", 5), prose, CaptureLLM(), {})

    est_subjects = {row["subject"] for row in captured.get("established", [])}
    assert "锦澜湾17号别墅地下室储藏室" in est_subjects  # 门牌号桥接纳入
    assert "林晚" not in est_subjects                    # 无关主体仍被裁
    assert captured["established"][0]["scene_tokens"] == ["17号"]
    assert "17号" in captured["proposed"][0]["scene_tokens"]
