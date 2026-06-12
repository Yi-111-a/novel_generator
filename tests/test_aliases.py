"""Fix B：显式化名抽取（治"种子写了化名秦书白，正文却全叫真名"）。"""
from __future__ import annotations

from novel_engine import db
from novel_engine.casting import lock_aliases
from novel_engine.models import Entity, Persona
from novel_engine.repository import Repository


def _repo() -> Repository:
    r = Repository(db.connect(":memory:"))
    r.insert_entity(Entity("p_shen", "character", "沈砚", {}))
    r.insert_entity(Entity("p_zhao", "character", "赵九", {}))
    r.insert_persona(Persona(agent_id="p_shen", name="沈砚"))
    r.insert_persona(Persona(agent_id="p_zhao", name="赵九"))
    return r


WANT = "潜伏在七十六号的地下党员沈砚（化名「秦书白」）要把一份名单送出去。"


def test_alias_extracted_public_is_cover_true_is_real():
    r = _repo()
    out = lock_aliases(r, WANT)
    idn = r.get_entity("p_shen").attributes.get("identity")
    assert idn and idn["public"] == "秦书白" and idn["true"] == "沈砚" and idn.get("is_alias")
    assert "p_shen" in out


def test_alias_fact_known_to_self_only():
    r = _repo()
    lock_aliases(r, WANT)
    fid = r.get_entity("p_shen").attributes["identity"]["fact_id"]
    assert r.agent_knows_fact("p_shen", fid)          # 本人知道自己的化名
    assert not r.agent_knows_fact("p_zhao", fid)      # 外人不知（进揭示链）


def test_no_alias_no_identity():
    r = _repo()
    lock_aliases(r, "赵九是个普通的招待。")            # 无化名
    assert r.get_entity("p_zhao").attributes.get("identity") is None


def test_idempotent_skips_existing_identity():
    r = _repo()
    r.update_entity_attributes("p_shen", {"identity": {"public": "X", "true": "沈砚", "fact_id": "f0"}})
    lock_aliases(r, WANT)
    assert r.get_entity("p_shen").attributes["identity"]["public"] == "X"   # 不覆盖已有


def test_ascii_alias_filtered():
    r = _repo()
    lock_aliases(r, "沈砚（化名「Q」）")               # 单字母 ASCII 不算
    assert r.get_entity("p_shen").attributes.get("identity") is None
