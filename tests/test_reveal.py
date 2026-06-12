from novel_engine.narration.reveal import commit_reveals, plan_reveals
from novel_engine.seed import PROTAGONIST_ID, SENIOR_ID, seed_m3


def test_reader_starts_blank_then_grows():
    repo = seed_m3(ticks=3)
    assert repo.list_reader_knowledge() == []  # 开篇读者一无所知
    # mystery_set 一开始 = 全部 facts（读者都不知道）
    assert set(repo.mystery_set()) == {f.fact_id for f in repo.list_facts()}


def test_plan_only_reveals_pov_known_facts():
    repo = seed_m3(ticks=3)
    # 林晚的某个亲历事件
    ev = next(e for e in repo.list_events() if PROTAGONIST_ID in e.actors)
    plan = plan_reveals(repo, PROTAGONIST_ID, [ev], reveal_budget=5)
    pov_known = {k.fact_id for k in repo.get_agent_ledger(PROTAGONIST_ID)}
    assert all(fid in pov_known for fid in plan.reveal)


def test_commit_reveals_writes_reader_ledger_and_shrinks_mystery():
    repo = seed_m3(ticks=3)
    ev = next(e for e in repo.list_events() if PROTAGONIST_ID in e.actors)
    plan = plan_reveals(repo, PROTAGONIST_ID, [ev], reveal_budget=1)
    revealed = commit_reveals(repo, plan, PROTAGONIST_ID, discourse_pos=1)
    if plan.reveal:
        assert revealed
        for fid in revealed:
            assert repo.reader_knows(fid)
            assert fid not in repo.mystery_set()


def test_irony_set_reader_knows_but_pov_doesnt():
    """读者经林晚视角得知某事 → 对秦松而言这就是反讽素材（他视角不一定知道）。"""
    repo = seed_m3(ticks=3)
    ev = next(e for e in repo.list_events() if PROTAGONIST_ID in e.actors)
    plan = plan_reveals(repo, PROTAGONIST_ID, [ev], reveal_budget=1)
    commit_reveals(repo, plan, PROTAGONIST_ID, 1)
    # 找一个读者已知、而某视角不知道的 fact 即构成 irony
    irony = repo.irony_set(SENIOR_ID)
    reader = {r.fact_id for r in repo.list_reader_knowledge()}
    assert set(irony) <= reader
