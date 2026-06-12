from novel_engine.narration.selection import compute_drama_score, select_and_order
from novel_engine.seed import THEME, seed_m3


def test_drama_score_rewards_choices_and_dialogue():
    repo = seed_m3(ticks=4)
    events = repo.list_events()
    assert events
    scores = [compute_drama_score(repo, e, THEME) for e in events]
    # 带抉择+对白+主题词的对峙应是高分；安静铺垫应是低分 → 戏剧分有区分度
    assert max(scores) >= 0.8
    assert min(scores) < 0.5


def test_select_and_order_picks_opening_and_decouples_order():
    repo = seed_m3(ticks=4)
    sel = select_and_order(repo, THEME, threshold=0.5)
    assert sel.selected
    assert sel.opening_event_id
    # 开场事件排在话语序第一位
    assert sel.selected[0].event_id == sel.opening_event_id
    # drama_score 已被后置标注
    assert repo.get_event_drama_score(sel.opening_event_id) is not None


def test_low_threshold_keeps_all():
    repo = seed_m3(ticks=3)
    sel = select_and_order(repo, THEME, threshold=0.0)
    assert len(sel.selected) == len(repo.list_events())
