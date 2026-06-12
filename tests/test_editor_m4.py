from novel_engine.narration.editor import Editor
from novel_engine.narration.foreshadow import honesty_gate, finalize_ending
from novel_engine.narration.style import AntiAbstractValidator
from novel_engine.seed import THEME, seed_m4


def build_editor(repo):
    return Editor(
        repo, llm=None, theme=THEME, threshold=0.5, reveal_budget=1,
        tension=True, foreshadows=True, exposition=True, max_consecutive_high=1,
    )


def test_editor_inserts_breather_and_pays_off_via_exposition():
    repo = seed_m4()
    renders = build_editor(repo).run()
    roles = [r.role for r in renders]
    assert "breather" in roles  # 高潮间插入了喘息场景

    # 喘息场景滴灌背景 → 预埋的背景伏笔被回收
    fs = repo.get_foreshadow("fs_bg_massacre")
    assert fs.status == "paid_off"
    assert repo.reader_knows("fact_bg_massacre")


def test_m4_prose_still_passes_anti_abstract():
    repo = seed_m4()
    renders = build_editor(repo).run()
    style = AntiAbstractValidator()
    for r in renders:
        assert style.check(r.scene.prose_text).ok


def test_unresolved_foreshadow_blocks_ending_until_backfill():
    repo = seed_m4()
    build_editor(repo).run()
    # 真凶伏笔在中段不会被揭 → 诚实性闸门应阻止收尾
    assert not honesty_gate(repo).ok
    # 回填回收节拍后放行并定稿
    assert finalize_ending(repo, "end_truth", auto_backfill=True).ok
    finals = [e for e in repo.list_endings() if e.status == "final"]
    assert finals and finals[0].ending_id == "end_truth"
