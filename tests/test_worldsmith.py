from novel_engine.agent import CharacterAgent
from novel_engine.dilemma import DilemmaGenerator
from novel_engine.director import Director
from novel_engine.llm.mock import MockClient
from novel_engine.monitors import Monitors
from novel_engine.seed import THEME, seed_m2
from novel_engine.validator import Validator
from novel_engine.worldsmith import WorldSmith


def test_introduce_character_lands_in_world():
    repo = seed_m2()
    ws = WorldSmith(repo, llm=None, theme=THEME)  # 规则回退，确定性
    before_personas = len(repo.list_personas())
    before_entities = len(repo.list_entities())
    before_events = len(repo.list_events())

    intro = ws.introduce_character(tick=5)

    assert intro.kind == "character"
    assert repo.entity_exists(intro.entity_id)
    assert repo.get_persona(intro.entity_id) is not None
    assert len(repo.list_personas()) == before_personas + 1
    assert len(repo.list_entities()) == before_entities + 1
    # 登场事件 + 事实落库
    assert len(repo.list_events()) > before_events
    assert repo.fact_exists(intro.fact_id)
    # 新角色带一条只有自己知道的秘密（信息差）
    ledger = repo.get_agent_ledger(intro.entity_id)
    assert any(k.confidence == 1.0 for k in ledger)


def test_introduce_object_lands_entity_and_event():
    repo = seed_m2()
    ws = WorldSmith(repo, llm=None, theme=THEME)
    intro = ws.introduce_object(tick=3, name="残破符箓")
    assert intro.kind == "object"
    ent = next(e for e in repo.list_entities() if e.entity_id == intro.entity_id)
    assert ent.type == "object" and ent.name == "残破符箓"
    assert repo.fact_exists(intro.fact_id)


def test_new_character_joins_a_thread():
    repo = seed_m2()
    ws = WorldSmith(repo, llm=None, theme=THEME)
    intro = ws.introduce_character(tick=2)
    joined = any(intro.entity_id in t.involved_agents for t in repo.list_threads())
    assert joined


def test_director_structural_beat_introduces_entity():
    repo = seed_m2()
    ws = WorldSmith(repo, llm=None, theme=THEME)
    director = Director(
        repo,
        DilemmaGenerator(repo, llm=None, theme=THEME),
        CharacterAgent(repo, MockClient()),
        Validator(repo),
        Monitors(repo),
        worldsmith=ws,
        structural_every=1,  # 每拍都做结构性节拍
    )
    before = len(repo.list_entities())
    step = director.step()
    assert step.writeback.get("structural") is not None
    assert len(repo.list_entities()) == before + 1


def test_director_without_worldsmith_unchanged():
    """默认不传 worldsmith → 不会引入新实体（向后兼容）。"""
    repo = seed_m2()
    director = Director(
        repo,
        DilemmaGenerator(repo, llm=None, theme=THEME),
        CharacterAgent(repo, MockClient()),
        Validator(repo),
        Monitors(repo),
    )
    before = len(repo.list_entities())
    director.step()
    assert len(repo.list_entities()) == before
