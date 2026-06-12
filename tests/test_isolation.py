from novel_engine.agent import CharacterAgent
from novel_engine.engine import Engine
from novel_engine.llm.mock import MockClient
from novel_engine.seed import PROTAGONIST_ID, seed
from novel_engine.validator import Validator


def test_ledger_only_returns_own_facts():
    repo = seed()
    ledger = repo.get_agent_ledger(PROTAGONIST_ID)
    fids = {k.fact_id for k in ledger}
    assert "fact_master_dead" in fids
    assert "fact_jade_half" in fids
    # 世界库里的真凶事实存在，但不在主角账本
    assert repo.fact_exists("fact_secret_killer")
    assert "fact_secret_killer" not in fids


def test_prompt_excludes_facts_outside_ledger():
    repo = seed()
    agent = CharacterAgent(repo, MockClient())
    system = agent.build_system_prompt(PROTAGONIST_ID, ["obj_jade"])
    # 账本内事实出现在 prompt；账本外真相不出现
    assert "fact_jade_half" in system
    assert "fact_secret_killer" not in system
    assert "真凶是师兄秦松" not in system


def test_other_agent_ledger_empty():
    repo = seed()
    assert repo.get_agent_ledger("char_senior") == []


def test_legal_action_commits_and_grows_ledger():
    repo = seed()
    validator = Validator(repo)
    before = len(repo.get_agent_ledger(PROTAGONIST_ID))
    good = {
        "intent": "查看",
        "target": "obj_jade",
        "referenced_facts": ["fact_jade_half"],
        "referenced_entities": ["obj_jade"],
    }
    engine = Engine(repo, CharacterAgent(repo, MockClient.from_actions([good])), validator, max_retries=0)
    r = engine.run_tick(PROTAGONIST_ID, "查看玉佩", ["obj_jade"], location_id="loc_qingming")
    assert r.committed
    assert repo.fact_exists(r.fact_id)
    after = len(repo.get_agent_ledger(PROTAGONIST_ID))
    assert after == before + 1
