from novel_engine.models import Action
from novel_engine.seed import PROTAGONIST_ID, seed
from novel_engine.validator import Validator


def make_validator():
    repo = seed()
    return repo, Validator(repo)


def test_unauthorized_fact_blocked():
    """越权知情：引用世界库存在、但不在自己账本里的 fact。"""
    _, v = make_validator()
    action = Action(
        intent="质问",
        target="char_senior",
        referenced_facts=["fact_secret_killer"],
        referenced_entities=["char_senior"],
    )
    res = v.check(PROTAGONIST_ID, action)
    assert not res.ok
    assert any(x.code == "unauthorized_fact" for x in res.violations)
    assert res.needs_llm_fix


def test_unknown_entity_blocked():
    _, v = make_validator()
    action = Action(intent="求助", target="char_ghost", referenced_entities=["char_ghost"])
    res = v.check(PROTAGONIST_ID, action)
    assert not res.ok
    assert any(x.code == "unknown_entity" for x in res.violations)


def test_physics_violation_blocked():
    _, v = make_validator()
    action = Action(intent="联系外援", dialogue="我用手机发消息给宗主。")
    res = v.check(PROTAGONIST_ID, action)
    assert not res.ok
    assert any(x.code == "physics_violation" for x in res.violations)


def test_unknown_fact_blocked():
    _, v = make_validator()
    action = Action(intent="回忆", referenced_facts=["fact_does_not_exist"])
    res = v.check(PROTAGONIST_ID, action)
    assert not res.ok
    assert any(x.code == "unknown_fact" for x in res.violations)


def test_legal_action_passes():
    _, v = make_validator()
    action = Action(
        intent="查看",
        target="obj_jade",
        inner_thought="师父把它交给我。",
        referenced_facts=["fact_jade_half"],
        referenced_entities=["obj_jade"],
    )
    res = v.check(PROTAGONIST_ID, action)
    assert res.ok, res.summary()
