"""POV 视角合格性：反派(藏着未揭身份)不当 POV，防读者从其视角提前全知泄底。"""
from __future__ import annotations

from novel_engine import db
from novel_engine.casting import pov_eligible
from novel_engine.models import Entity, Persona, ReaderKnowledge
from novel_engine.repository import Repository


def _repo() -> Repository:
    r = Repository(db.connect(":memory:"))
    # 主角：化名(对外身份) → 合格
    r.insert_entity(Entity("hero", "character", "沈砚",
                           {"identity": {"public": "秦书白", "true": "沈砚", "fact_id": "f_alias", "is_alias": True}}))
    # 盟友：无隐藏身份 → 合格
    r.insert_entity(Entity("ally", "character", "赵九", {}))
    # 反派：藏着七十六号身份(非化名) → 未揭示前不合格
    r.insert_entity(Entity("foe", "character", "苏静",
                           {"identity": {"public": "舞女", "true": "特工队长", "fact_id": "f_foe"}}))
    for a, n in [("hero", "沈砚"), ("ally", "赵九"), ("foe", "苏静")]:
        r.insert_persona(Persona(agent_id=a, name=n))
    return r


def test_hero_and_ally_eligible():
    r = _repo()
    assert pov_eligible(r, "hero", "hero") is True
    assert pov_eligible(r, "ally", "hero") is True


def test_alias_holder_eligible():
    r = _repo()
    # 主角化名是自己的对外身份，不算泄底 → 合格（即便不作为 hero 传入）
    assert pov_eligible(r, "hero", None) is True


def test_hidden_villain_not_eligible_until_revealed():
    r = _repo()
    assert pov_eligible(r, "foe", "hero") is False          # 反派身份未揭 → 不可当 POV
    r.reveal_to_reader(ReaderKnowledge(fact_id="f_foe", revealed_version="苏静是特工队长",
                                       revealed_discourse_pos=1, via_pov="hero"))
    assert pov_eligible(r, "foe", "hero") is True            # 揭示给读者后 → 可当 POV
