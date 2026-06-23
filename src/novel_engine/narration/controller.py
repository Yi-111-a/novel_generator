"""B4 Controller（大纲驱动重构：双向控制器）。

在 scripted 写场后、落库前做语义把关：
  ① 主判定（1 次低温 LLM）：本场是否在演这个 beat？是否与 POV 已知事实矛盾、或写了 POV 不可能知道的事？
  ② 解离反推验证（关键场——本场有揭示时——独立第二次低温 LLM）：
     仅凭正文，读者能否合理推出本场揭开的真相？正文没铺垫就凭空抛结论 = 因果链断裂（语义三角测定）。

不过 → 反馈给上层写作流程，由 SceneWriter 带反馈重写一次。约束层用 temperature=0。无 LLM → 宽松放行。
"""
from __future__ import annotations

import json

from ..llm.base import LLMClient
from ..repository import Repository

JUDGE_TEMPERATURE = 0.0


def _json(llm: LLMClient, system: str, user: str) -> dict | None:
    try:
        raw = llm.complete_at(system + " 只输出合法 JSON。", user, JUDGE_TEMPERATURE).strip().strip("`")
    except Exception:
        return None
    if raw.lower().startswith("json"):
        raw = raw[4:].strip()
    i, j = raw.find("{"), raw.rfind("}")
    try:
        d = json.loads(raw[i:j + 1] if 0 <= i < j else raw)
        return d if isinstance(d, dict) else None
    except Exception:
        return None


class Controller:
    def __init__(self, repo: Repository, llm: LLMClient | None = None) -> None:
        self.repo = repo
        self.llm = llm

    def check(self, prose: str, spec, present: list[str]) -> tuple[bool, str]:
        if self.llm is None or not (prose or "").strip():
            return True, ""
        ok, fb = self._judge(prose, spec, present)
        if not ok:
            return False, fb
        if getattr(spec, "may_reveal", None):   # 关键场：独立反推验证因果自洽
            ok2, fb2 = self._reverse(prose, spec)
            if not ok2:
                return False, fb2
        return True, ""

    def _judge(self, prose: str, spec, present: list[str]) -> tuple[bool, str]:
        known = "\n".join(f"- {k.version_content}" for k in (getattr(spec, "pov_known", []) or [])) or "（空）"
        system = (
            "你是小说连续性审校。只判硬伤，不评文笔。对给定正文做两项检查，有一项明显出问题即判不合格：\n"
            "① 在不在 beat：正文是否在演下面这个【本拍目标】，而不是跑题或原地空转？\n"
            "② 隔离/矛盾：正文是否写了与【POV 已知】明显矛盾的事，或让 POV 知道了他此刻不可能知道的事？\n"
            '只输出 JSON：{"ok": true或false, "feedback": "若不合格，一句话指出问题与修改方向；合格则空串"}'
        )
        user = (f"【本拍目标】{getattr(spec, 'beat', '')}\n【POV 已知】\n{known}\n\n"
                f"【正文】\n{(prose or '')[:3000]}\n\n只输出 JSON。")
        d = _json(self.llm, system, user)
        if d is None:
            return True, ""   # 评分失败不阻断
        return (bool(d.get("ok", True)), str(d.get("feedback", "")).strip())

    def _reverse(self, prose: str, spec) -> tuple[bool, str]:
        """解离反推（独立调用）：仅凭正文，读者能否推出本场揭开的真相？无铺垫凭空抛 = 因果断裂。"""
        # 取本场要揭示真相的内容（POV 持有版本优先，回退 canonical）
        pov_known = {k.fact_id: k.version_content for k in (getattr(spec, "pov_known", []) or [])}
        truths = []
        for fid in (spec.may_reveal or []):
            txt = pov_known.get(fid)
            if not txt:
                f = self.repo.get_fact(fid)
                txt = f.canonical_content if f else ""
            if txt:
                truths.append(txt)
        if not truths:
            return True, ""
        system = (
            "你是侦探式逻辑审校。下面给你一段小说正文，以及它**意图让读者得出的结论**。"
            "请**仅凭正文本身**判断：正文里是否存在能让读者合理推导出这个结论的线索/铺垫/因果？"
            "如果正文没有任何铺垫、结论是凭空抛出来的，就是因果链断裂。\n"
            '只输出 JSON：{"consistent": true或false, "reason": "一句话说明"}'
        )
        user = (f"【意图让读者得出的结论】{'；'.join(truths)}\n\n【正文】\n{(prose or '')[:3000]}\n\n只输出 JSON。")
        d = _json(self.llm, system, user)
        if d is None:
            return True, ""
        if bool(d.get("consistent", True)):
            return True, ""
        reason = str(d.get("reason", "")).strip()
        return False, (f"本场凭空抛出了一个结论而正文没有铺垫（{reason}）。"
                       f"请在正文里补足能让读者推出它的线索与因果，不要直接宣布真相。")
