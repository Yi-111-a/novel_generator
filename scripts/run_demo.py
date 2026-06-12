"""M1 演示：证明"隔离 + 不幻觉"。

运行：python scripts/run_demo.py
- 默认用 MockClient（无需 key）跑三段证据。
- 若设置 DEEPSEEK_API_KEY，则额外跑一次真实 DeepSeek 决策，确认同样经过校验层。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from novel_engine.agent import CharacterAgent  # noqa: E402
from novel_engine.config import load_llm_config  # noqa: E402
from novel_engine.engine import Engine  # noqa: E402
from novel_engine.llm import build_client  # noqa: E402
from novel_engine.llm.mock import MockClient  # noqa: E402
from novel_engine.seed import PROTAGONIST_ID, seed  # noqa: E402
from novel_engine.validator import Validator  # noqa: E402

ALLOWED = ["char_lin", "char_master", "char_senior", "loc_qingming", "obj_jade"]
SITUATION = (
    "深夜，林晚独自来到后山药园，想查清师父的死因。四下无人。"
)

LINE = "=" * 64


def show_ledger(repo, title):
    print(f"\n{title}")
    for k in repo.get_agent_ledger(PROTAGONIST_ID):
        print(f"  - [{k.fact_id}] {k.version_content}")


def main() -> None:
    print(LINE)
    print("M1 演示 · 隔离 + 不幻觉")
    print(LINE)

    repo = seed()
    validator = Validator(repo)

    # ---------- 证据 1：隔离 ----------
    print("\n【证据 1 · 隔离】林晚的账本只含她自己知道的事实。")
    show_ledger(repo, "林晚账本：")
    print("  注意：世界库里还有 fact_secret_killer（真凶=秦松），但不在她账本里 → 她不该'知道'。")

    # ---------- 证据 2：拦截幻觉 ----------
    print(f"\n{LINE}\n【证据 2 · 不幻觉】喂三个故意幻觉的动作，看校验层零 token 拦截。")
    bad_actions = [
        {  # a) 越权知情：引用账本外的 fact
            "intent": "质问真凶",
            "target": "char_senior",
            "dialogue": "秦松，是你杀了师父、偷了玉佩！",
            "referenced_facts": ["fact_secret_killer"],
            "referenced_entities": ["char_senior"],
            "chosen_value": "对师父的忠义",
        },
        {  # b) 引用不存在的实体
            "intent": "求助",
            "target": "char_ghost",
            "dialogue": "前辈请现身相助。",
            "referenced_entities": ["char_ghost"],
        },
        {  # c) 违反物理法则
            "intent": "联系外援",
            "dialogue": "我得用手机给宗主发个消息。",
            "inner_thought": "掏出手机……",
        },
    ]
    agent = CharacterAgent(repo, MockClient.from_actions(bad_actions))
    engine = Engine(repo, agent, validator, max_retries=0)
    labels = ["越权知情", "不存在的实体", "违反物理法则"]
    for label in labels:
        r = engine.run_tick(PROTAGONIST_ID, SITUATION, ALLOWED)
        status = "已落库" if r.committed else "被拦截/丢弃"
        print(f"\n  [{label}] intent={r.action.intent!r} → {status}")
        print(f"    校验：{r.validation.summary()}")

    # ---------- 证据 3：合法动作落库 ----------
    print(f"\n{LINE}\n【证据 3 · 合法动作】只用账本内信息的动作 → 通过、落库、账本新增。")
    good_action = {
        "intent": "查看",
        "target": "obj_jade",
        "dialogue": "",
        "inner_thought": "师父把它交给我，必有深意。",
        "chosen_value": "对师父的忠义",
        "referenced_facts": ["fact_jade_half"],
        "referenced_entities": ["obj_jade"],
    }
    agent2 = CharacterAgent(repo, MockClient.from_actions([good_action]))
    engine2 = Engine(repo, agent2, validator, max_retries=0)
    r = engine2.run_tick(PROTAGONIST_ID, SITUATION, ALLOWED, location_id="loc_qingming")
    print(f"\n  intent={r.action.intent!r} → {'已落库' if r.committed else '被拦截'}")
    print(f"    校验：{r.validation.summary()}")
    print(f"    新事件：{r.event_id}　新事实：{r.fact_id}")
    show_ledger(repo, "林晚账本（动作后，新增一条她亲历的事实）：")

    # ---------- 可选：真实 DeepSeek ----------
    cfg = load_llm_config()
    print(f"\n{LINE}\n【可选 · 真实 LLM】")
    if cfg.provider == "deepseek" and cfg.has_key:
        print(f"  检测到 key，调用 {cfg.model} 进行一次真实决策……")
        live_agent = CharacterAgent(repo, build_client(cfg))
        live_engine = Engine(repo, live_agent, validator, max_retries=2)
        r = live_engine.run_tick(PROTAGONIST_ID, SITUATION, ALLOWED, location_id="loc_qingming")
        print(f"  模型动作：intent={r.action.intent!r} target={r.action.target!r}")
        print(f"  内心：{r.action.inner_thought}")
        print(f"  校验：{r.validation.summary()}（尝试 {r.attempts} 次，{'落库' if r.committed else '丢弃'}）")
    else:
        print("  未设置 DEEPSEEK_API_KEY，跳过真实调用。（在 .env 填入后再跑即可）")

    print(f"\n{LINE}\nM1 演示结束。")


if __name__ == "__main__":
    main()
