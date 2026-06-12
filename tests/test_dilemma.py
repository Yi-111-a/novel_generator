from novel_engine.dilemma import DilemmaGenerator
from novel_engine.seed import PROTAGONIST_ID, THEME, seed_m2


def test_generate_returns_scored_dilemma():
    repo = seed_m2()
    gen = DilemmaGenerator(repo, llm=None, theme=THEME)
    d = gen.generate(tick=1, target=PROTAGONIST_ID)
    assert d is not None
    assert d.target_agent == PROTAGONIST_ID
    assert len(d.colliding_pair) == 2 and d.colliding_pair[0] != d.colliding_pair[1]
    assert d.score > 0
    assert d.situation and d.why_no_escape


def test_value_vs_value_for_two_valued_persona():
    repo = seed_m2()
    gen = DilemmaGenerator(repo, llm=None, theme=THEME)
    persona = repo.get_persona(PROTAGONIST_ID)
    a, b, kind = gen.select_colliding_pair(persona)
    assert kind == "value_vs_value"
    assert {a, b} == {"对师父的忠义", "明哲保身"}


def test_prefer_flaw_forces_flaw_collision():
    repo = seed_m2()
    gen = DilemmaGenerator(repo, llm=None, theme=THEME)
    persona = repo.get_persona(PROTAGONIST_ID)
    a, b, kind = gen.select_colliding_pair(persona, prefer_flaw=True)
    assert kind == "flaw_vs_value"
    assert persona.fatal_flaw in (a, b)


def test_select_target_prefers_high_priority_thread():
    repo = seed_m2()
    gen = DilemmaGenerator(repo, llm=None, theme=THEME)
    target = gen.select_target(tick=1)
    # 两位主角都在 thread_main(0.9)，目标应落在其中之一
    assert target in {PROTAGONIST_ID, "char_senior"}
