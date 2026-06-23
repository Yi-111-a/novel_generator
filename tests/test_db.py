from novel_engine import db
from novel_engine.repository import Repository


def test_schema_tables_created():
    conn = db.connect(":memory:")
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    names = {r["name"] for r in rows}
    assert {"world_bible", "entities", "facts", "events", "agent_knowledge", "persona"} <= names


def test_facts_events_are_append_only_at_repo_layer():
    """append-only 的契约：repository 不提供 update/delete facts/events 的接口。"""
    repo = Repository(db.connect(":memory:"))
    public = {m for m in dir(repo) if not m.startswith("_")}
    forbidden = {"update_fact", "delete_fact", "update_event", "delete_event"}
    assert forbidden.isdisjoint(public)
    # facts/events 只暴露 append_*
    assert "append_fact" in public and "append_event" in public


def test_build_checkpoints_round_trip():
    repo = Repository(db.connect(":memory:"))
    assert repo.get_build_checkpoints() == {}
    repo.mark_build_checkpoint("W1_world_skill", "running", meta={"llmLogs": 2})
    assert repo.build_checkpoint_status("W1_world_skill") == "running"
    repo.mark_build_checkpoint("W1_world_skill", "done", meta={"llmLogs": 3})
    checkpoints = repo.get_build_checkpoints()
    assert checkpoints["W1_world_skill"]["status"] == "done"
    assert checkpoints["W1_world_skill"]["meta"]["llmLogs"] == 3
