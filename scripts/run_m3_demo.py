"""M3 演示：剪辑层 + 读者账本 + 反抽象渲染。

运行：python scripts/run_m3_demo.py
流程：先跑导演循环产出一批 events（模拟"真"），再把它们剪辑、删选、重排、
渲染成小说（叙述"好看"）。证明"能把事件变成不流水账的散文"。

默认 MockClient + 离线模板（无需 key、确定性）；设 DEEPSEEK_API_KEY 则用真实 DeepSeek 渲染散文。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from novel_engine.config import load_llm_config  # noqa: E402
from novel_engine.llm import build_client  # noqa: E402
from novel_engine.narration.editor import Editor  # noqa: E402
from novel_engine.narration.style import AntiAbstractValidator  # noqa: E402
from novel_engine.seed import THEME, seed_m3  # noqa: E402

LINE = "=" * 64


def main() -> None:
    cfg = load_llm_config()
    repo = seed_m3(ticks=4)  # 先产出事件流

    print(LINE)
    print("M3 演示 · 剪辑层 / 读者账本 / 反抽象渲染")
    print(LINE)

    # ---------- 证据 1：选材 + 排序（话语序 ≠ 故事序） ----------
    print("\n【证据 1 · 选材+排序】不是每个事件都进小说；开场选最高潮（in medias res）。")
    llm = build_client(cfg) if (cfg.provider == "deepseek" and cfg.has_key) else None
    editor = Editor(repo, llm=llm, theme=THEME, threshold=0.5, reveal_budget=1, max_rewrites=2)
    sel = editor.select()
    print(f"  事件总数={len(repo.list_events())}　入选场景={len(sel.selected)}　概述/跳过={len(sel.skipped)}")
    print("  故事时间序 vs 话语顺序：")
    for pos, ev in enumerate(sel.selected, start=1):
        ds = repo.get_event_drama_score(ev.event_id)
        flag = "  ← 开场(最高潮)" if ev.event_id == sel.opening_event_id else ""
        print(f"    话语#{pos}  ← story_time={ev.story_time}  drama={ds}{flag}")

    # ---------- 证据 2：揭示 + 读者账本 ----------
    print(f"\n{LINE}\n【证据 2 · 读者账本】渲染前读者一无所知；逐场揭示，mystery_set 收缩。")
    print(f"  渲染前：reader_knowledge={len(repo.list_reader_knowledge())} 条，"
          f"mystery_set={len(repo.mystery_set())} 条未揭真相。")

    renders = editor.run()

    print(f"  渲染后：reader_knowledge={len(repo.list_reader_knowledge())} 条，"
          f"mystery_set={len(repo.mystery_set())} 条仍是悬念。")
    print("  读者账本（按揭示话语位置）：")
    for rk in repo.list_reader_knowledge():
        print(f"    [话语#{rk.revealed_discourse_pos} via {rk.via_pov}] {rk.revealed_version}")

    # ---------- 证据 3：反抽象渲染产物 ----------
    print(f"\n{LINE}\n【证据 3 · 反抽象渲染】情绪靠动作/物件/感官，禁止直接点名。")
    style = AntiAbstractValidator()
    for r in renders:
        chk = style.check(r.scene.prose_text)
        print(f"\n  —— 第 {r.scene.discourse_order} 场（POV={r.scene.pov}，"
              f"张力={r.scene.target_tension}，反抽象校验={chk.summary()}，重写{r.style_attempts}次）——")
        for line in r.scene.prose_text.splitlines():
            print(f"    {line}")
        if r.scene.newly_revealed:
            print(f"    （本场向读者揭示：{r.scene.newly_revealed}）")

    print(f"\n{LINE}")
    if llm is not None:
        print(f"（散文由真实 {cfg.model} 渲染，并经反抽象闸门把关）")
    else:
        print("（未设置 DEEPSEEK_API_KEY，散文用离线模板渲染；填 key 后由 DeepSeek 写）")
    print("M3 演示结束。")


if __name__ == "__main__":
    main()
