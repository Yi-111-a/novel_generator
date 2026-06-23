"""离线护栏评测 (offline guard-rail eval).

量化 Harness 对长篇连续性硬错误的拦截能力。完全离线、确定性、不调用 LLM。

对每个指标，构造一组「对抗样本」(故意埋入违规) 与「干净样本」(无违规)，
然后用两种配置跑同一批样本：

  - 基线 (护栏关)：没有检测器/没有 Reviser，违规原样出库 → 残留率 = 100%。
  - 当前 Harness (护栏开)：跑真实检测器 → 残留率 = 漏检率 (miss)，并报误报率 (FP)。

注意：「基线」不是被删掉的旧 director/editor 管线 (那需要重跑 LLM)，而是
「同一批样本在没有该护栏时会原样放行」的可证下界。这衡量的是护栏强度
(召回 + 误报)，不是端到端生成质量。

跑法:  python scripts/eval_harness.py
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from novel_engine import db
from novel_engine.models import ChapterPlan, Entity, Persona
from novel_engine.narration.audit import _P0_VIOLATION_TYPES, _phantom_items, _violation_severity
from novel_engine.narration.story_clock import audit_time_regression, format_minutes
from novel_engine.disclosure import disclosure_stage, set_disclosure_schedule
from novel_engine.chapter_scope_validator import validate_chapter_scope
from novel_engine.repository import Repository


# --------------------------------------------------------------------------- #
# 样本类型：一个待审章节 = (repo 世界状态, 章节计划, 正文)，外加是否埋了违规。
# detect() 返回 True 表示「当前 Harness 判定有违规」。
# --------------------------------------------------------------------------- #
@dataclass
class Case:
    repo: Repository
    chapter: ChapterPlan
    prose: str
    has_violation: bool          # ground truth
    violation_type: str = ""     # 命中的 P0 类型 (用于 Controller 重写率)


@dataclass
class Metric:
    key: str
    name: str
    detect: Callable[[Case], bool]
    cases: list[Case]


# --------------------------------------------------------------------------- #
# 公共种子
# --------------------------------------------------------------------------- #
def _base_repo() -> Repository:
    r = Repository(db.connect(":memory:"))
    r.insert_entity(Entity("loc_main", "location", "槐荫巷44号", {}))
    r.insert_entity(Entity("hero", "character", "云鹤子", {}))
    r.insert_persona(Persona(agent_id="hero", name="云鹤子", want="求道"))
    return r


# --------------------------------------------------------------------------- #
# 1. 时间线冲突率  (story_clock.audit_time_regression)
# --------------------------------------------------------------------------- #
def _seed_prev_clock(r: Repository, end_clock: int = 4 * 60 + 17) -> None:
    r.upsert_timeline_entry({
        "chapter_no": 8, "end_clock": end_clock,
        "end_clock_text": format_minutes(end_clock), "deadlines": [],
    })


def metric_timeline() -> Metric:
    cases: list[Case] = []
    # 对抗：上一章 04:17，本章倒流到更早
    for prose in ("凌晨四点的槐荫巷44号，灯还亮着。", "三点半，他还没睡。", "凌晨两点，雨停了。"):
        r = _base_repo(); _seed_prev_clock(r)
        cases.append(Case(r, ChapterPlan("c13", "a", 13), prose, True, "story_clock_regression"))
    # 干净：时间前进 或 显式次日
    for prose in ("早上七点，校车快来了。", "次日凌晨四点，槐荫巷44号。", "傍晚六点，天暗了。"):
        r = _base_repo(); _seed_prev_clock(r)
        cases.append(Case(r, ChapterPlan("c13", "a", 13), prose, False))

    def detect(c: Case) -> bool:
        return audit_time_regression(c.repo, c.chapter, c.prose) is not None

    return Metric("timeline", "时间线冲突率", detect, cases)


# --------------------------------------------------------------------------- #
# 2. 已销毁道具复活率  (audit._phantom_items 道具台账)
# --------------------------------------------------------------------------- #
def _repo_with_sacrificed_item() -> Repository:
    r = _base_repo()
    r.insert_entity(Entity("obj_compass", "object", "玄铁罗盘", {}))
    r.transfer_item("obj_compass", "hero", 1)          # hero 持有
    r.transfer_item("obj_compass", None, 5, status="sacrificed", note="第5章焚毁")
    return r


def metric_item_revival() -> Metric:
    cases: list[Case] = []
    # 对抗：已焚毁的「玄铁罗盘」在第10章正文复活 (不在台账白名单)
    for prose in ("他摸出玄铁罗盘，指针疯狂打转。",
                  "玄铁罗盘又一次在掌心发烫。",
                  "她把玄铁罗盘举到月光下。"):
        r = _repo_with_sacrificed_item()
        ch = ChapterPlan("c10", "a", 10, cast=["hero"], pov_agent="hero", location_ids=["loc_main"])
        cases.append(Case(r, ch, prose, True, "structural_defect"))
    # 干净：不提复活道具
    for prose in ("他空着手站在巷口。", "罗盘早已不在，他只能凭记忆走。", "月光照在空荡的掌心。"):
        r = _repo_with_sacrificed_item()
        ch = ChapterPlan("c10", "a", 10, cast=["hero"], pov_agent="hero", location_ids=["loc_main"])
        cases.append(Case(r, ch, prose, False))

    def detect(c: Case) -> bool:
        return len(_phantom_items(c.repo, c.chapter, c.prose)) > 0

    return Metric("item_revival", "已销毁道具复活率", detect, cases)


# --------------------------------------------------------------------------- #
# 3. 角色越权知情率 / 后续剧情泄露率  (disclosure 闸门)
#    一个被设为「第10章才披露」的秘密实体，在第2章正文被点名 → 越权/泄露。
# --------------------------------------------------------------------------- #
def _repo_with_concealed_secret() -> Repository:
    r = _base_repo()
    r.insert_entity(Entity("obj_truth", "object", "黑塔真名", {}))
    set_disclosure_schedule(r, "obj_truth", foreshadow_from=10, reveal_chapter=12)
    return r


def metric_disclosure() -> Metric:
    secret_name = "黑塔真名"
    cases: list[Case] = []
    # 对抗：第2章就把第10章才该披露的秘密点破
    for prose in (f"他忽然明白了，{secret_name}原来就是答案。",
                  f"她低声念出{secret_name}。",
                  f"卷宗末尾赫然写着{secret_name}。"):
        r = _repo_with_concealed_secret()
        cases.append(Case(r, ChapterPlan("c2", "a", 2), prose, True, "future_event_leak"))
    # 干净：不提前点破
    for prose in ("他还看不懂卷宗上的纹路。", "答案仍藏在迷雾里。", "她合上卷宗，一无所获。"):
        r = _repo_with_concealed_secret()
        cases.append(Case(r, ChapterPlan("c2", "a", 2), prose, False))

    def detect(c: Case) -> bool:
        # 闸门：实体处于 stage 0 (完全封存) 却在正文出现 → 越权/泄露
        ent = c.repo.get_entity("obj_truth")
        if not ent or ent.name not in c.prose:
            return False
        return disclosure_stage(c.repo, "obj_truth", c.chapter.sequence_order) == 0

    return Metric("disclosure", "越权知情/剧情泄露率", detect, cases)


# --------------------------------------------------------------------------- #
# 4. 角色越权登场率  (chapter_scope_validator 白名单)
#    不在本章授权 cast 的角色出现在正文 → unauthorized_character。
# --------------------------------------------------------------------------- #
def _scope_repo() -> Repository:
    r = _base_repo()
    r.insert_entity(Entity("villain", "character", "墨渊", {}))
    r.insert_persona(Persona(agent_id="villain", name="墨渊", want="夺道"))
    return r


def _scope_chapter() -> ChapterPlan:
    return ChapterPlan(
        "c3", "a", 3, cast=["hero"], pov_agent="hero",
        location_ids=["loc_main"], allowed_entity_ids=["hero", "loc_main"],
    )


def metric_scope() -> Metric:
    cases: list[Case] = []
    # 对抗：未授权角色「墨渊」越权登场
    for prose in ("墨渊推门而入，盯着他。", "巷口传来墨渊的冷笑。", "墨渊的影子落在墙上。"):
        cases.append(Case(_scope_repo(), _scope_chapter(), prose, True, "unauthorized_character"))
    # 干净：只有授权主角
    for prose in ("云鹤子独自站在槐荫巷44号。", "他在巷子里来回踱步。", "云鹤子抬头看了看灯。"):
        cases.append(Case(_scope_repo(), _scope_chapter(), prose, False))

    def detect(c: Case) -> bool:
        try:
            result = validate_chapter_scope(c.repo, c.chapter, c.prose, llm=None)
        except Exception:
            return False
        return any(v.get("type") == "unauthorized_character"
                   for v in result.get("violations", []))

    return Metric("scope", "角色越权登场率", detect, cases)


# --------------------------------------------------------------------------- #
# 评测执行
# --------------------------------------------------------------------------- #
def run_metric(m: Metric) -> dict:
    adv = [c for c in m.cases if c.has_violation]
    clean = [c for c in m.cases if not c.has_violation]
    caught = sum(1 for c in adv if m.detect(c))
    fp = sum(1 for c in clean if m.detect(c))
    recall = caught / len(adv) if adv else 0.0
    return {
        "metric": m,
        "n_adv": len(adv), "n_clean": len(clean),
        "recall": recall,
        "baseline_residual": 1.0,            # 护栏关：对抗样本全部出库
        "current_residual": 1.0 - recall,    # 护栏开：残留 = 漏检
        "fp": fp / len(clean) if clean else 0.0,
    }


def controller_rewrite_rate(metrics: list[Metric]) -> tuple[float, float]:
    """Controller 重写率 = 整个语料中被判 P0 → 路由到 Reviser 的草稿占比。
    基线 (无 Controller) = 0% 拦截，全部草稿原样出库。"""
    total = 0
    routed = 0
    for m in metrics:
        for c in m.cases:
            total += 1
            if not m.detect(c):
                continue
            vt = c.violation_type
            sev = _violation_severity(vt) if vt else ("P0" if vt in _P0_VIOLATION_TYPES else "P1")
            if sev == "P0":
                routed += 1
    return 0.0, (routed / total if total else 0.0)


def build_report(metrics: list[Metric]) -> str:
    rows = [run_metric(m) for m in metrics]
    base_ctrl, cur_ctrl = controller_rewrite_rate(metrics)

    def pct(x: float) -> str:
        return f"{x * 100:.1f}%"

    out: list[str] = []
    out.append("# 离线护栏评测 (确定性, 无 LLM)")
    out.append("")
    out.append("| 指标 | 基线(护栏关) | 当前 Harness | 召回 | 误报 | 样本(对抗/干净) |")
    out.append("|------|------|------|------|------|------|")
    for r in rows:
        m = r["metric"]
        out.append(f"| {m.name} | {pct(r['baseline_residual'])} 残留 | "
                   f"{pct(r['current_residual'])} 残留 | {pct(r['recall'])} | "
                   f"{pct(r['fp'])} | {r['n_adv']}/{r['n_clean']} |")
    out.append(f"| Controller 重写率(P0 拦截占比) | {pct(base_ctrl)} | {pct(cur_ctrl)} | - | - | 全语料 |")
    out.append("")
    out.append("说明: 「残留」= 违规最终留在正文的比例。基线=护栏关时对抗样本 100% 出库;")
    out.append("当前 Harness 残留 = 漏检率 (1 - 召回)。误报 = 干净样本被错误拦下的比例。")
    out.append("Controller 重写率 = 被判 P0 而路由到 Reviser 的草稿占比 (基线无此拦截 = 0%)。")
    out.append("")
    out.append("基线 = 同一批样本在没有该护栏时会原样放行的可证下界, 非重跑旧 LLM 管线。")
    return "\n".join(out)


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # Windows GBK 控制台兜底
    except Exception:
        pass
    metrics = [metric_timeline(), metric_item_revival(), metric_disclosure(), metric_scope()]
    report = build_report(metrics)
    print("\n" + report)
    dest = Path(__file__).resolve().parent.parent / "docs" / "eval-harness-report.md"
    dest.write_text(report + "\n", encoding="utf-8")
    print(f"\n[written] {dest}")


if __name__ == "__main__":
    main()
