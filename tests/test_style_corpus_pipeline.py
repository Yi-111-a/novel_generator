from __future__ import annotations

from novel_engine import db
from novel_engine.llm.mock import MockClient
from novel_engine.models import SourceChapter, SourceDocument
from novel_engine.repository import Repository
from novel_engine.story_bible import DraftManager, StoryBibleBuilder
from novel_engine.story_bible.chapter_splitter import split_text_into_chapters
from novel_engine.style import build_style_corpus

SOURCE = (
    "第一章 风起\n"
    "林澈走进青铜城，风很冷。她抬头看见高处的灯，一时间没说话。\n"
    "“你终于来了。”苏眠说。\n"
    "第二章 暗潮\n"
    "苏眠把信封推过来，像是漫不经心，其实手指一直绷着。\n"
    "林澈笑了一下，说这东西最好别让我现在拆。"
)


def _bootstrap() -> Repository:
    repo = Repository(db.connect(":memory:"))
    repo.set_project_meta(project_type="continuation", project_status="writing", analysis_status="ready")
    repo.set_writing_settings(repo.get_writing_settings())
    return repo


def _import(repo: Repository, text: str = SOURCE) -> None:
    repo.clear_source_material()
    doc = SourceDocument(project_id="p1", filename="source.txt", format="txt", raw_text=text, created_at="now")
    doc.id = repo.insert_source_document(doc)
    for i, (title, body) in enumerate(split_text_into_chapters(text), 1):
        repo.insert_source_chapter(SourceChapter(
            project_id="p1",
            source_document_id=doc.id,
            chapter_no=i,
            title=title,
            text=body,
            word_count=len(body),
            summary=body[:50],
            created_at="now",
        ))


def test_build_style_corpus_persists_segments_and_clusters():
    repo = _bootstrap()
    _import(repo)
    summary = build_style_corpus(repo, project_id="p1")
    assert summary["segmentCount"] > 0
    assert len(repo.list_style_segments()) == summary["segmentCount"]
    assert len(repo.list_style_clusters()) == summary["clusterCount"]
    assert any(seg.discourse_type in {"dialogue", "narration", "action", "reflection"} for seg in repo.list_style_segments())


def test_draft_carries_style_metadata_and_reject_becomes_negative():
    repo = _bootstrap()
    _import(repo)
    StoryBibleBuilder(repo).build_for_continuation(title="测试续写")
    build_style_corpus(repo, project_id="p1")
    mgr = DraftManager(repo, MockClient(), project_id="p1")
    draft = mgr.generate(guidance="让她从对话里意识到对方在隐瞒信息", target_words=300)
    assert draft.candidate_group_id
    assert isinstance(draft.style_packet_json, dict)
    assert isinstance(draft.score_breakdown_json, dict)
    assert isinstance(draft.retrieved_segment_ids_json, list)
    mgr.reject(draft.id)
    negatives = repo.list_style_negative_samples()
    assert len(negatives) >= 1
    assert negatives[0].related_source_segment_ids_json == draft.retrieved_segment_ids_json


