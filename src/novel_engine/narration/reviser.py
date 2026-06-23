"""章节修订器（Reviser）。

审计查出问题后，不再从计划「整章从零重写」（那会冒出新人名/新标题/新越界），
而是把**已有正文 + 出问题的内容 + 硬约束白名单**一起交给 LLM，由 LLM 自己判断
每个问题是局部改几句还是重写某一段，并在原文基础上产出**完整修订正文**。

调用方只在「存在可改类 P0、分类为 prose_rewriteable」时调用本器，并在每轮后重新过
综合审计、保留最优版本（见 drafts.py 的改写回路）。
"""
from __future__ import annotations

import json
from contextlib import nullcontext
from dataclasses import dataclass

from ..chapter_scope_validator import compile_chapter_package
from ..llm.base import LLMClient
from ..models import ChapterPlan
from ..repository import Repository

REVISE_TEMPERATURE = 0.4


@dataclass
class RevisionResult:
    ok: bool
    prose: str = ""
    change_scope: str = ""


def _safe_json_loads(raw: str) -> dict | None:
    text = (raw or "").strip().strip("`")
    if text.lower().startswith("json"):
        text = text[4:].strip()
    start, end = text.find("{"), text.rfind("}")
    try:
        data = json.loads(text[start : end + 1] if 0 <= start < end else text)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


class Reviser:
    def __init__(self, repo: Repository, llm: LLMClient | None = None) -> None:
        self.repo = repo
        self.llm = llm

    def revise(
        self,
        plan: ChapterPlan,
        prose: str,
        violations: list[dict],
    ) -> RevisionResult:
        if self.llm is None or not (prose or "").strip():
            return RevisionResult(ok=False)
        problems = [
            v
            for v in (violations or [])
            if str(v.get("advice", "")).strip() and str(v.get("text", "")).strip()
        ]
        if not problems:
            return RevisionResult(ok=False)

        contract = compile_chapter_package(self.repo, plan)
        evidence_scoped = any(
            str(row.get("source_text", "")).strip()
            and str(row.get("repair_scope", "")) in {"sentence", "paragraph"}
            for row in problems
        )
        if evidence_scoped:
            problems = [
                {
                    **row,
                    "text": (
                        f"{str(row.get('text', '')).strip()} "
                        f"[source_text={str(row.get('source_text', '')).strip()}] "
                        f"[repair_scope={str(row.get('repair_scope', '')).strip()}]"
                    ).strip(),
                }
                for row in problems
            ]
        system = (
            "你是小说章节修订器。给你一章已写好的正文和审计查出的问题。"
            "任务：修正这些问题，同时**最大限度保留原文**。\n"
            "你自己判断每个问题——只改几句就够，还是要重写某一段/某一场。\n"
            "铁律：① 没被点名的内容尽量一字不改；② 绝不能为修一个问题而引入新人物、"
            "新道具、新地点或新组织，也不准改章节标题；③ 只能出现【可用清单】里的专有名词；"
            "④ 不得揭示未被允许的真相或未来章节的事件；⑤ 输出修订后的**完整正文**，不是片段。\n"
            '只输出 JSON：{"change_scope": "local或scene或rewrite", "prose": "完整修订正文"}'
        )
        user = (
            f"【可用清单·允许出场角色】{self._join(contract.get('allowed_cast'))}\n"
            f"【可用清单·允许地点】{self._join(contract.get('allowed_locations'))}\n"
            f"【可用清单·允许道具】{self._join(contract.get('allowed_items'))}\n"
            f"【可提及】{self._join(contract.get('may_mention'))}\n"
            f"【审计查出的问题】\n{self._format_problems(problems)}\n"
            f"【原文】\n{prose}\n\n"
            "请在原文基础上修订，保留所有没问题的内容，只改必要处。只输出 JSON。"
        )
        if evidence_scoped:
            user += (
                "\n本次是证据定位修复。禁止返回完整正文，必须只返回JSON："
                '{"change_scope":"local","edits":[{"old_text":"原文中唯一连续句段",'
                '"new_text":"修正后的句段"}]}。'
            )
        try:
            with self._scope():
                raw = self.llm.complete_at(system, user, REVISE_TEMPERATURE)
        except Exception:
            return RevisionResult(ok=False)
        data = _safe_json_loads(raw)
        if data is None:
            return RevisionResult(ok=False)
        edits = data.get("edits") if isinstance(data.get("edits"), list) else []
        if edits:
            revised = prose
            for edit in edits[:8]:
                if not isinstance(edit, dict):
                    return RevisionResult(ok=False)
                old = str(edit.get("old_text", "")).strip()
                new = str(edit.get("new_text", "")).strip()
                if not old or old not in revised or revised.count(old) != 1:
                    return RevisionResult(ok=False)
                revised = revised.replace(old, new, 1)
            if revised == prose:
                return RevisionResult(ok=False)
            return RevisionResult(ok=True, prose=revised, change_scope="local")
        if evidence_scoped:
            # Evidence-scoped P0 findings must be repaired by exact replacement.
            # Reject a full-chapter rewrite even if the model returned one.
            return RevisionResult(ok=False)
        revised = str(data.get("prose", "")).strip()
        if not revised:
            return RevisionResult(ok=False)
        return RevisionResult(
            ok=True,
            prose=revised,
            change_scope=str(data.get("change_scope", "")).strip(),
        )

    def _scope(self):
        scope = getattr(self.llm, "scope", None)
        if callable(scope):
            return scope(caller="chapter_reviser", meta={})
        return nullcontext()

    @staticmethod
    def _join(values) -> str:
        items = [str(v).strip() for v in (values or []) if str(v).strip()]
        return "、".join(items) if items else "（无）"

    @staticmethod
    def _format_problems(problems: list[dict]) -> str:
        lines: list[str] = []
        for idx, row in enumerate(problems, start=1):
            lines.append(
                f"{idx}. 类型={row.get('type', '')}"
                f" | 出问题的内容：{str(row.get('text', '')).strip()}"
                f" | 改法：{str(row.get('advice', '')).strip()}"
            )
        return "\n".join(lines)
