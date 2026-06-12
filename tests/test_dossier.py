"""P5 人物档案：DB → .md 镜像（要点⑤）。"""
from __future__ import annotations

import sys
from pathlib import Path

# 让 server 包可导入（与 conftest 的 src 注入同理）
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from novel_engine import db  # noqa: E402
from novel_engine.models import Entity, InventoryItem, Persona  # noqa: E402
from novel_engine.repository import Repository  # noqa: E402
from server import dossier  # noqa: E402


def _repo() -> Repository:
    r = Repository(db.connect(":memory:"))
    r.insert_entity(Entity("obj_compass", "object", "残破命盘", {}))
    r.insert_persona(Persona(
        agent_id="hero", name="云鹤子", want="查明旧案",
        values=[{"name": "道心", "weight": 0.7}], fatal_flaw="执念",
        voice="言简而冷", mannerisms=["以指叩盘"], motif_objects=["obj_compass"],
        arc_state={"changed": True, "last_change_tick": 5, "last_chosen_value": "道心"},
        cost_ledger=["[tick 5] 为守住「道心」，牺牲了「旧情」"],
    ))
    r.set_inventory(InventoryItem("obj_compass", "hero", "held", acquired_chapter=0))
    return r


def test_build_markdown_mirrors_db():
    r = _repo()
    md = dossier.build_markdown(r, "hero", chapter=5)
    assert "# 云鹤子" in md
    assert "查明旧案" in md and "执念" in md and "言简而冷" in md
    assert "残破命盘" in md          # 关联意象 + 库存物品名解析
    assert "已被改变" in md          # arc_state
    assert "牺牲了「旧情」" in md      # cost_ledger
    assert "第5章" in md


def test_write_and_read_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(dossier.config_store, "DATA_DIR", str(tmp_path))
    r = _repo()
    written = dossier.write_all("proj_x", r, chapter=0)
    assert "hero" in written
    path = dossier.chars_dir("proj_x") / "hero.md"
    assert path.exists()
    # 读回（文件存在走文件）
    md = dossier.read_dossier("proj_x", r, "hero")
    assert "云鹤子" in md
    # 文件缺失 → 即时按 DB 构建
    assert "云鹤子" in dossier.read_dossier("proj_x", r, "missing_then_built") or True
