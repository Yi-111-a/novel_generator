"""新章节级写作链（story bible / 草稿 / 验收 / 原文管理）端到端，覆盖续写项目链路。

旁路替换旧 planning/scene 链：本测试只走 story_bible 模块 + Repository，
不依赖外部 LLM（用 MockClient），保证新链可独立落地与回归。
"""
from __future__ import annotations

from novel_engine import db
from novel_engine.continuation import (
    build_continuation_snapshot,
    distill_continuation_graph,
    distill_continuation_structures,
    distill_continuation_world,
    ensure_continuation_chapter_plan,
    next_chapter_no,
)
from novel_engine.llm.mock import MockClient
from novel_engine.models import (
    AcceptedChapterRecord,
    ChapterDraftRecord,
    SourceChapter,
    SourceDocument,
)
from novel_engine.repository import Repository
from novel_engine.story_bible import DraftManager, StoryBibleBuilder
from novel_engine.story_bible.chapter_splitter import split_text_into_chapters
from novel_engine.style import build_style_corpus, distill_author_experience
from server.projects import Project

SOURCE = (
    "第一章 风起\n林澈走进青岚城，天色阴沉，他握紧腰间的玉佩。\n"
    "第二章 暗涌\n苏眠在旧书院等他，桌上放着一封密信。"
)


def _bootstrap() -> Repository:
    repo = Repository(db.connect(":memory:"))
    repo.set_project_meta(project_type="continuation", project_status="writing", analysis_status="ready")
    repo.set_writing_settings(repo.get_writing_settings())
    return repo


def _import(repo: Repository, text: str = SOURCE) -> None:
    repo.clear_source_material()
    doc = SourceDocument(project_id="p1", filename="b.txt", format="txt", raw_text=text, created_at="now")
    doc.id = repo.insert_source_document(doc)
    for i, (title, body) in enumerate(split_text_into_chapters(text), 1):
        repo.insert_source_chapter(SourceChapter(
            project_id="p1", source_document_id=doc.id, chapter_no=i, title=title,
            text=body, word_count=len(body), summary=body[:50], created_at="now"))


def test_chapter_splitter_finds_titled_chapters():
    chs = split_text_into_chapters(SOURCE)
    assert [t for t, _ in chs] == ["第一章 风起", "第二章 暗涌"]


def test_default_writing_settings_present():
    repo = _bootstrap()
    ws = repo.get_writing_settings()
    assert ws.target_words > 0 and ws.max_words >= ws.min_words


def test_build_continuation_bible_from_source():
    repo = _bootstrap()
    _import(repo)
    rec = StoryBibleBuilder(repo).build_for_continuation(title="测试续写")
    assert rec.source_type == "continuation"
    assert len(rec.timeline_json) == 2
    assert repo.get_story_bible_record() is not None


def test_draft_generate_accept_indexes_scene_and_event():
    repo = _bootstrap()
    _import(repo)
    StoryBibleBuilder(repo).build_for_continuation(title="测试续写")
    mgr = DraftManager(repo, MockClient(), project_id="p1")
    draft = mgr.generate(guidance="林澈发现密信秘密", target_words=300, mode="manual")
    assert draft.id > 0 and draft.chapter_no == 1
    assert draft.status == "pending_acceptance"
    accepted = mgr.accept(draft.id)
    assert accepted.chapter_no == 1
    assert len(repo.list_accepted_chapters()) == 1
    # 验收落库：场景 + 事件兼容旧阅读链路
    assert len(repo.list_scenes()) == 1
    assert len(repo.list_events()) >= 1


def test_auto_write_multiple_chapters():
    repo = _bootstrap()
    _import(repo)
    ws = repo.get_writing_settings()
    ws.require_human_acceptance = False
    repo.set_writing_settings(ws)
    mgr = DraftManager(repo, MockClient(), project_id="p1")
    ids = mgr.auto_write(chapters=3, target_words=200, guidance="继续推进")
    assert len(ids) == 3
    assert len(repo.list_accepted_chapters()) == 3
    # 章号递增、无重复
    nos = [c.chapter_no for c in repo.list_accepted_chapters()]
    assert nos == [1, 2, 3]


def test_auto_write_stops_on_pending_acceptance():
    repo = _bootstrap()
    _import(repo)
    mgr = DraftManager(repo, MockClient(), project_id="p1")
    ids = mgr.auto_write(chapters=3, target_words=200, guidance="继续推进")
    assert len(ids) == 1
    assert len(repo.list_accepted_chapters()) == 0
    draft = repo.get_chapter_draft(ids[0])
    assert draft is not None
    assert draft.status == "pending_acceptance"


def test_visible_drafts_hide_blocked_history_after_chapter_acceptance():
    repo = _bootstrap()
    blocked = ChapterDraftRecord(
        project_id="p1",
        chapter_no=2,
        title="旧拦截稿",
        prose="旧正文",
        status="blocked",
        created_at="before",
    )
    blocked.id = repo.create_chapter_draft(blocked)
    current = ChapterDraftRecord(
        project_id="p1",
        chapter_no=2,
        title="确认稿",
        prose="新正文",
        status="accepted",
        created_at="after",
        accepted_at="after",
    )
    current.id = repo.create_chapter_draft(current)
    repo.insert_accepted_chapter(
        AcceptedChapterRecord(
            project_id="p1",
            draft_id=current.id,
            chapter_no=2,
            title=current.title,
            prose=current.prose,
            summary="摘要",
            created_at="after",
        )
    )

    visible_ids = [draft.id for draft in repo.list_visible_chapter_drafts()]

    assert current.id in visible_ids
    assert blocked.id not in visible_ids
    assert repo.get_chapter_draft(blocked.id) is not None


def test_outline_only_draft_has_no_prose():
    repo = _bootstrap()
    _import(repo)
    mgr = DraftManager(repo, MockClient(), project_id="p1")
    draft = mgr.generate(guidance="梗概", target_words=300, outline_only=True)
    assert draft.status == "draft"
    assert draft.prose == ""
    assert draft.outline.strip() != ""


def test_prose_draft_carries_audit_snapshot():
    repo = _bootstrap()
    _import(repo)
    mgr = DraftManager(repo, MockClient(), project_id="p1")
    draft = mgr.generate(guidance="林澈发现密信秘密", target_words=300)
    audit = draft.context_snapshot_json.get("audit")
    assert isinstance(audit, dict)
    assert {"ok", "severity", "checks", "rewriteAdvice"} <= set(audit.keys())
    assert isinstance(audit["checks"], dict)


def test_reject_draft_marks_status():
    repo = _bootstrap()
    _import(repo)
    mgr = DraftManager(repo, MockClient(), project_id="p1")
    draft = mgr.generate(guidance="待否决", target_words=200)
    mgr.reject(draft.id)
    assert repo.get_chapter_draft(draft.id).status == "rejected"
    assert len(repo.list_accepted_chapters()) == 0


def test_source_chapter_edit_and_resplit():
    repo = _bootstrap()
    _import(repo)
    chs = repo.list_source_chapters()
    repo.update_source_chapter(chs[0].id, title="第一章 改名", text="林澈走入青岚城。")
    edited = repo.list_source_chapters()[0]
    assert edited.title == "第一章 改名"
    assert edited.word_count == len("林澈走入青岚城。")


def test_continue_current_book_next_chapter_no():
    repo = _bootstrap()
    _import(repo)
    repo.set_project_meta(write_mode="continue_current_book", latest_source_chapter_no=2, chapter_start_no=3)
    assert next_chapter_no(repo) == 3
    mgr = DraftManager(repo, MockClient(), project_id="p1")
    draft = mgr.generate(guidance="继续", target_words=200)
    assert draft.chapter_no == 3


def test_new_series_book_next_chapter_no():
    repo = _bootstrap()
    _import(repo)
    repo.set_project_meta(write_mode="new_series_book", latest_source_chapter_no=2, chapter_start_no=1)
    assert next_chapter_no(repo) == 1
    mgr = DraftManager(repo, MockClient(), project_id="p1")
    draft = mgr.generate(guidance="开新书", target_words=200)
    assert draft.chapter_no == 1


def test_story_bible_continuation_payload_uses_meta():
    repo = _bootstrap()
    _import(repo)
    repo.set_project_meta(
        write_mode="new_series_book",
        current_book_title="龙族：新章",
        source_book_title="龙族 VII",
        continuation_hint="多年后，新的混血种醒来。",
        time_position="多年后",
        protagonist_strategy="新主角",
        inherit_unresolved_threads=1,
    )
    rec = StoryBibleBuilder(repo).build_for_continuation(title="测试续写")
    assert rec.world_config_json["source_book_title"] == "龙族 VII"
    assert rec.narrative_constraints_json["write_mode"] == "new_series_book"
    assert rec.narrative_constraints_json["current_book_title"] == "龙族：新章"


def test_continuation_job_steps_payload():
    repo = _bootstrap()
    _import(repo)
    meta = repo.get_continuation_meta()
    meta.continuation_phase = "distilling"
    repo.set_continuation_meta(meta)
    from novel_engine.models import ContinuationJobRecord

    repo.upsert_continuation_job(ContinuationJobRecord(
        id="job_1",
        project_id="p1",
        phase="B4",
        progress=4,
        total=7,
        status="running",
        config_json={
            "steps": [
                {"code": "B1", "label": "导入分章", "status": "done"},
                {"code": "B2", "label": "世界书", "status": "done"},
                {"code": "B3", "label": "人物地点势力", "status": "done"},
                {"code": "B4", "label": "系列状态", "status": "running"},
            ]
        },
        created_at="now",
        updated_at="now",
    ))
    job = repo.latest_continuation_job()
    assert job is not None
    assert job.phase == "B4"
    assert job.config_json["steps"][3]["status"] == "running"


def test_continuation_requires_lock_before_draft_generation():
    project = Project("测试续写", project_type="continuation")
    project.ensure_writing_repo()
    assert project.repo is not None
    _import(project.repo)
    project.repo.set_project_meta(
        project_type="continuation",
        project_status="writing",
        analysis_status="ready",
        continuation_ready=False,
    )
    res = project.create_chapter_draft({"guidance": "继续", "targetWords": 200})
    assert res["ok"] is False
    assert res["error"] == "continuation_not_locked"


def test_project_style_diagnostics_contains_voice_and_revision_metadata():
    project = Project("测试续写", project_type="continuation")
    project.ensure_writing_repo()
    assert project.repo is not None
    _import(project.repo)
    project.repo.set_project_meta(
        project_type="continuation",
        project_status="writing",
        analysis_status="ready",
        continuation_ready=True,
    )
    StoryBibleBuilder(project.repo).build_for_continuation(title="测试续写")
    build_style_corpus(project.repo, project_id=project.id)
    draft = DraftManager(project.repo, MockClient(), project_id=project.id).generate(
        guidance="熟人对话里试探秘密",
        target_words=280,
    )
    diagnostics = project.style_diagnostics()
    assert diagnostics["latestDraft"]["id"] == draft.id
    assert "characterVoiceCoverage" in diagnostics["corpus"]
    assert "revisionHistory" in diagnostics["latestDraft"]


def test_continuation_distill_populates_world_entities_and_graph():
    repo = _bootstrap()
    _import(repo)
    distill_continuation_world(repo, llm=None)
    distill_continuation_structures(repo, llm=None)
    graph = distill_continuation_graph(repo, llm=None)
    rec = StoryBibleBuilder(repo).build_for_continuation(title="测试续写")
    snapshot = build_continuation_snapshot(repo)
    assert rec.world_config_json["sections"]
    assert len(repo.list_personas()) >= 1
    assert len(repo.list_locations()) >= 1
    assert graph["edges"] >= 1
    assert len(rec.relationships_json) >= 1
    assert snapshot["graph_summary"]["edge_count"] >= 1


def test_continuation_runtime_plan_uses_world_graph_and_threads():
    repo = _bootstrap()
    _import(repo)
    repo.set_project_meta(write_mode="continue_current_book", latest_source_chapter_no=2, chapter_start_no=3)
    distill_continuation_world(repo, llm=None)
    distill_continuation_structures(repo, llm=None)
    distill_continuation_graph(repo, llm=None)
    StoryBibleBuilder(repo).build_for_continuation(title="娴嬭瘯缁啓")
    plan = ensure_continuation_chapter_plan(repo, target_words=320, guidance="")
    assert plan is not None
    assert plan.sequence_order == 3
    assert plan.cast
    assert plan.location_ids
    assert plan.pov_agent == plan.cast[0]
    assert len(plan.beat_goals) >= 3
    assert plan.reveal_gate
    assert "continuation_runtime" == plan.arc_id


def test_draft_generation_auto_builds_seeded_continuation_plan():
    repo = _bootstrap()
    _import(repo)
    repo.set_project_meta(write_mode="continue_current_book", latest_source_chapter_no=2, chapter_start_no=3)
    distill_continuation_world(repo, llm=None)
    distill_continuation_structures(repo, llm=None)
    distill_continuation_graph(repo, llm=None)
    StoryBibleBuilder(repo).build_for_continuation(title="娴嬭瘯缁啓")
    assert next((c for c in repo.list_chapter_plans() if c.sequence_order == 3), None) is None
    draft = DraftManager(repo, None, project_id="p1").generate(guidance="", target_words=260)
    plan = next((c for c in repo.list_chapter_plans() if c.sequence_order == 3), None)
    assert plan is not None
    assert draft.chapter_no == 3
    assert draft.context_snapshot_json["chapter_plan"]["pov_agent"] == plan.pov_agent
    assert draft.context_snapshot_json["chapter_plan"]["location_ids"] == plan.location_ids
    if plan.location_ids:
        location = repo.get_location(plan.location_ids[0])
        assert location is not None
        assert location.name in draft.prose


def test_author_experience_layer_distills_life_model(tmp_path):
    repo = _bootstrap()
    _import(repo)
    essay = (
        "我一直记得少年时那种站在人群外面的感觉，像是每个人都知道要去哪里，只有我不知道。"
        "所以我学会先开玩笑，先把羞耻说成笑话，像是这样就不会受伤。\n\n"
        "后来我才知道，真正推动一个人往前走的，不只是梦想，还有那种不想再被丢下的倔强。"
    )
    essay_path = tmp_path / "essay.txt"
    essay_path.write_text(essay, encoding="utf-8")
    meta = repo.get_continuation_meta()
    meta.experience_layer_enabled = True
    meta.experience_source_path = str(essay_path)
    meta.experience_style_level = "max"
    repo.set_continuation_meta(meta)
    model = distill_author_experience(
        repo,
        project_id="p1",
        source_path=str(essay_path),
        llm=None,
        source_text=SOURCE,
    )
    assert model is not None
    assert model.core_wound_json
    assert repo.latest_author_life_model() is not None
    assert repo.get_continuation_meta().active_life_model_id == model.model_id
    summary = repo.style_corpus_summary()
    assert summary["lifeModel"] is not None


def test_project_continuation_distill_keeps_active_life_model_id(tmp_path):
    project = Project("测试续写", project_type="continuation")
    project.ensure_writing_repo()
    assert project.repo is not None
    _import(project.repo)
    essay = (
        "我一直记得少年时那种站在人群外面的感觉，像是每个人都知道要去哪里，只有我不知道。"
        "所以我学会先开玩笑，先把羞耻说成笑话，像是这样就不会受伤。"
    )
    essay_path = tmp_path / "essay.txt"
    essay_path.write_text(essay, encoding="utf-8")
    project.set_continuation_settings(
        {
            "experienceLayerEnabled": True,
            "experienceLayerMode": "essay_plus_text",
            "experienceSourcePath": str(essay_path),
            "experienceStyleLevel": "max",
        }
    )
    res = project.start_continuation_distill(
        {
            "sampleMode": "full",
            "graphDetail": "medium",
            "styleSampleSegments": 6,
            "generateAws": True,
            "enableStyleSkill": True,
            "extractUnresolvedThreads": True,
            "extractCharacterEndings": True,
            "extractFactionState": True,
            "extractExpandableRegions": True,
        }
    )
    assert res["ok"] is True
    meta = project.repo.get_continuation_meta()
    assert meta.active_life_model_id != ""
    assert len(project.repo.list_edges()) >= 1
    story_bible = project.repo.get_story_bible_record()
    assert story_bible is not None
    assert story_bible.world_config_json["sections"]
    assert len(story_bible.relationships_json) >= 1
    assert project.style_diagnostics()["corpus"]["lifeModel"] is not None
