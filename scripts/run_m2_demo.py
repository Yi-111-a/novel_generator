"""M2 演示：多角色 + 信息传播（含扭曲）+ 内在冲突生成器 + 导演循环。

运行：python scripts/run_m2_demo.py
目标：证明"能自动造出有张力的抉择"，并展示信息在不同账本里如何分化/误传。

默认全程 MockClient（无需 key）；若设置 DEEPSEEK_API_KEY，
两难处境的"具体化"会改用真实 DeepSeek（§6.2）。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from novel_engine.agent import CharacterAgent  # noqa: E402
from novel_engine.config import load_llm_config  # noqa: E402
from novel_engine.dilemma import DilemmaGenerator  # noqa: E402
from novel_engine.director import Director  # noqa: E402
from novel_engine.llm import build_client  # noqa: E402
from novel_engine.llm.mock import MockClient  # noqa: E402
from novel_engine.monitors import Monitors  # noqa: E402
from novel_engine.propagation import Propagator  # noqa: E402
from novel_engine.seed import PROTAGONIST_ID, SENIOR_ID, THEME, seed_m2  # noqa: E402
from novel_engine.validator import Validator  # noqa: E402

LINE = "=" * 64

# 一个对任何处境都合法的应答（不引用账本外 fact，只点名在场实体）
LEGAL_ACTION = {
    "intent": "逼问真相",
    "target": SENIOR_ID,
    "dialogue": "师兄，我不退了——那天后山，到底发生了什么？",
    "inner_thought": "攥紧那半枚玉佩，指节发白。",
    "chosen_value": "对师父的忠义",
    "referenced_facts": [],
    "referenced_entities": [SENIOR_ID],
}


def main() -> None:
    cfg = load_llm_config()
    repo = seed_m2()

    print(LINE)
    print("M2 演示 · 多角色 / 信息传播(扭曲) / 内在冲突 / 导演循环")
    print(LINE)

    # ---------- 证据 1：信息传播 + 扭曲 ----------
    print("\n【证据 1 · 传播与扭曲】同一真相在不同账本里会分化。")
    prop = Propagator(repo)
    canonical = next(
        k for k in repo.get_agent_ledger(SENIOR_ID) if k.fact_id == "fact_jade_location"
    )
    print(f"  秦松（亲历者）账本：{canonical.version_content}  [confidence={canonical.confidence}]")
    prop.tell(SENIOR_ID, PROTAGONIST_ID, "fact_jade_location", tick=1)
    lin_item = next(
        k for k in repo.get_agent_ledger(PROTAGONIST_ID) if k.fact_id == "fact_jade_location"
    )
    print(f"  林晚（听秦松转述）账本：{lin_item.version_content}  [confidence={lin_item.confidence}]")
    print("  → version_content 已偏离 canonical，可信度衰减 = 误传。")
    print("\n  conflict_pairs（同一 fact、不同版本 → 人物冲突的种子）：")
    for c in repo.find_conflict_pairs():
        print(f"    fact={c['fact_id']}")
        for h in c["holders"]:
            print(f"      - {h['agent_id']}: {h['version']}")

    # ---------- 证据 2：内在冲突生成器 ----------
    print(f"\n{LINE}\n【证据 2 · 内在冲突生成器】导演自动造一个'两者无法兼得'的处境。")
    gen_llm = build_client(cfg) if (cfg.provider == "deepseek" and cfg.has_key) else None
    gen = DilemmaGenerator(repo, llm=gen_llm, theme=THEME)
    d = gen.generate(tick=1, target=PROTAGONIST_ID)
    print(f"  目标角色：{d.target_agent}")
    print(f"  相撞的两个元素：「{d.colliding_pair[0]}」 vs 「{d.colliding_pair[1]}」")
    print(f"  处境：{d.situation}")
    print(f"  封死退路：{d.why_no_escape}")
    print(f"  两难评分（stakes+不可逆+主题+弱点压力）：{d.score}")

    # ---------- 证据 3：导演循环（多拍）+ 反完美监控 ----------
    print(f"\n{LINE}\n【证据 3 · 导演循环】交权给角色自主决定 → 校验落库 → 写回弧线/代价。")
    print("  （连续几拍弱点零成本后，监控会报警，导演改造一个逼弱点反噬的两难。）")
    director = Director(
        repo,
        DilemmaGenerator(repo, llm=gen_llm, theme=THEME),
        CharacterAgent(repo, MockClient.from_actions([LEGAL_ACTION])),
        Validator(repo),
        Monitors(repo, flaw_max_free=2),
    )
    for _ in range(5):
        step = director.step()
        if step.dilemma is None:
            continue
        alarm_note = ""
        if any(a.kind == "flaw_free_too_long" for a in step.alarms):
            alarm_note = "  ⚠ 反完美报警 → 本拍逼弱点"
        committed = step.result.committed if step.result else False
        print(f"\n  [tick {step.tick}] 目标={step.dilemma.target_agent} "
              f"两难=「{step.dilemma.colliding_pair[0]}」vs「{step.dilemma.colliding_pair[1]}」"
              f" 评分={step.dilemma.score}{alarm_note}")
        if step.result:
            print(f"    角色选择：{step.result.action.intent}（优先 {step.result.action.chosen_value}）"
                  f" → {'落库' if committed else '丢弃'}")
        if step.writeback:
            wb = step.writeback
            print(f"    写回：守住「{wb['won']}」舍「{wb['lost']}」"
                  f"{'｜弱点付出代价' if wb['flaw_pressed'] else ''}")

    # 末态：林晚的代价台账与弧线
    lin = repo.get_persona(PROTAGONIST_ID)
    print(f"\n  林晚弧线 arc_state：{lin.arc_state}")
    print("  林晚代价台账 cost_ledger：")
    for c in lin.cost_ledger:
        print(f"    - {c}")

    # ---------- 张力 ----------
    print(f"\n{LINE}\n【故事线张力】")
    for t in repo.list_threads():
        print(f"  {t.thread_id}: tension={t.current_tension} (优先级 {t.priority_weight})")

    print(f"\n{LINE}")
    if gen_llm is not None:
        print(f"（两难处境由真实 {cfg.model} 具体化）")
    else:
        print("（未设置 DEEPSEEK_API_KEY，两难用规则模板构造；填 key 后处境会更具体）")
    print("M2 演示结束。")


if __name__ == "__main__":
    main()
