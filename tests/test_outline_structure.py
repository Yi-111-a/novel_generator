"""B0.7 大纲层结构闸门：把方法.txt 的结构性主题上推到 planner（骨架层）。

① 反升华 exit_state ② 道德灰度+非圆满 ③ 留悬副线 ④ 伏笔埋设/回收 ⑤ 人物弧线阶段 ⑥ 情感滞后 beat。
全部零 schema：落在 planner 提示词/常量/逻辑 + 现成的 Persona.arc_state / RevealNode.kind。
"""
from __future__ import annotations

import json

from novel_engine import db
from novel_engine.llm.base import LLMClient
from novel_engine.models import Arc, Persona
from novel_engine.planner import _ROLE_ARC, _ROLE_BEATS, Planner
from novel_engine.repository import Repository


def _repo() -> Repository:
    r = Repository(db.connect(":memory:"))
    r.insert_persona(Persona(agent_id="hero", name="沈砚", want="查清真相", fatal_flaw="多疑"))
    r.insert_persona(Persona(agent_id="ally", name="赵九", want="自保"))
    r.insert_persona(Persona(agent_id="foe", name="苏静", want="复仇"))
    return r


# ---- ⑥ 情感滞后 beat 进 resolution 节拍模板 ----
def test_resolution_beats_have_affective_lag():
    beats = _ROLE_BEATS["resolution"]
    assert any("引爆" in b for b in beats)        # 滞后情感在余波章某细节处崩塌


# ---- ⑤ 人物弧线阶段写入 arc_state ----
def test_seed_arc_phases_writes_five_stage_arc():
    r = _repo()
    p = Planner(r, llm=None)
    p._seed_arc_phases(r.list_personas())
    hero = r.get_persona("hero")
    phases = hero.arc_state.get("arc_phases")
    assert isinstance(phases, list) and len(phases) == 5
    assert "多疑" in phases[0]                     # 起点用了主角的 fatal_flaw
    assert "相反" in _ROLE_ARC["climax"]           # climax 阶段=相反抉择（弧线转折）


# ---- ③ 故意留悬一条副线（不回收）----
def test_build_subplots_adds_dangling_node():
    r = _repo()
    p = Planner(r, llm=None)
    personas = r.list_personas()
    nodes: list = []
    p._build_subplots(personas, hero="hero", truth_holders=set(), nodes=nodes, seq=0, cap=2)
    kinds = [n.kind for n in nodes]
    assert "dangling" in kinds                     # ≥1 条副线留悬
    assert "clue" in kinds                         # 同时仍有正常会回收的副线
    dangling = next(n for n in nodes if n.kind == "dangling")
    assert dangling.fact_id is None and not dangling.discovered   # 永不回收


# ---- ①②④⑤ 提示注入（捕获 _chapter_spec 的 system 提示）----
class _Capture(LLMClient):
    def __init__(self) -> None:
        self.systems: list[str] = []

    def complete(self, system: str, user: str) -> str:
        self.systems.append(system)
        return json.dumps({"beats": ["甲", "乙", "丙"], "location": "码头",
                           "question": "他能脱身吗？", "exit_state": "一封信落入对手手中",
                           "props": []}, ensure_ascii=False)

    @property
    def name(self) -> str:
        return "Capture"


def _spec(role: str, has_reveal: bool):
    r = _repo()
    llm = _Capture()
    p = Planner(r, llm=llm)
    arc = Arc(arc_id="a1", part_id="p1", sequence_order=1, title="", summary="谍影重重", target_chapters=5)
    p._chapter_spec(None, arc, role, has_reveal, locs=[("loc1", "码头"), ("loc2", "阁楼")],
                    prev_loc="loc2")
    return "\n".join(llm.systems)


def test_anti_sermon_exit_state_constraint():
    sysmsg = _spec("climax", has_reveal=False)
    assert "升华" in sysmsg and "点题" in sysmsg       # ① exit_state 禁内心升华/点题


def test_moral_grayness_on_milestone():
    assert "道德" in _spec("climax", has_reveal=False)    # ② 里程碑章注入道德灰度
    assert "道德" not in _spec("setup", has_reveal=False)  # 非里程碑不注入（铺垫章保持轻）


def test_foreshadow_asymmetric_plant_vs_payoff():
    plant = _spec("setup", has_reveal=True)
    payoff = _spec("twist", has_reveal=True)
    assert "埋设" in plant and "闲笔" in plant            # ④ 埋设期压低
    assert "回收" in payoff and "塌陷" in payoff          # ④ 回收期放大


def test_arc_phase_injected_with_opposite_choice_on_milestone():
    assert "相反" in _spec("twist", has_reveal=False)     # ⑤ 里程碑推相反抉择
    assert "弧线" in _spec("rising", has_reveal=False)    # 非里程碑也注入阶段（不强调相反）


def test_pov_per_beat_constraint_injected():
    # POV 跟着节拍：每个 beat 标视角人物，beat 内不混别人内心
    r = _repo()
    llm = _Capture()
    p = Planner(r, llm=llm)
    arc = Arc(arc_id="a1", part_id="p1", sequence_order=1, title="", summary="谍影", target_chapters=5)
    p._chapter_spec(None, arc, "rising", False, locs=[("loc1", "码头")], prev_loc=None,
                    cast_names="沈砚、赵九、苏静")
    sysmsg = "\n".join(llm.systems)
    assert "beat_povs" in sysmsg and "视角" in sysmsg
    assert "不要混入别人的内心" in sysmsg
    assert "沈砚、赵九、苏静" in sysmsg                  # 在场角色供 LLM 选每拍视角
