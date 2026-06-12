from novel_engine.agent import CharacterAgent
from novel_engine.dilemma import DilemmaGenerator
from novel_engine.director import Director
from novel_engine.llm.mock import MockClient
from novel_engine.monitors import Monitors
from novel_engine.seed import THEME, seed_m2
from novel_engine.validator import Validator

LEGAL_ACTION = {
    "intent": "质问",
    "target": "char_senior",
    "dialogue": "师兄，那天后山到底发生了什么？",
    "inner_thought": "就算粉身碎骨，我也要问到底。",
    "chosen_value": "对师父的忠义",
    "referenced_facts": [],            # 不引用任何 fact → 不越权
    "referenced_entities": ["char_senior"],
}


def build_director(repo):
    gen = DilemmaGenerator(repo, llm=None, theme=THEME)  # 规则构造，不消耗 mock
    agent = CharacterAgent(repo, MockClient.from_actions([LEGAL_ACTION]))
    return Director(repo, gen, agent, Validator(repo), Monitors(repo, flaw_max_free=2))


def test_step_commits_and_writes_back():
    repo = seed_m2()
    director = build_director(repo)
    step = director.step()

    assert step.dilemma is not None
    assert step.result is not None and step.result.committed
    target = step.dilemma.target_agent

    persona = repo.get_persona(target)
    assert persona.arc_state.get("last_change_tick") == step.tick
    assert persona.arc_state.get("changed") is True
    assert len(persona.cost_ledger) >= 1  # 付出了代价


def test_flaw_alarm_eventually_forces_flaw_dilemma():
    """连续若干拍若弱点零成本，监控报警 → 导演改造一个逼弱点的两难。"""
    repo = seed_m2()
    director = build_director(repo)  # flaw_max_free=2
    saw_flaw_pressed = False
    for _ in range(5):
        step = director.step()
        if step.writeback.get("flaw_pressed"):
            saw_flaw_pressed = True
            break
    assert saw_flaw_pressed
