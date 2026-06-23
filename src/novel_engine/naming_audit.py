from __future__ import annotations

from collections import Counter, defaultdict

from .naming_profile import (
    CharacterNameRecord,
    CultureNamingStyle,
    NameBatchAuditIssue,
    NameBatchAuditResult,
    NamingProfile,
)


def _template_family(name: str) -> str:
    if "·" in name:
        return "middle_dot"
    if "-" in name or "—" in name:
        return "hyphen"
    if len(name) == 2:
        return "len2"
    if len(name) == 3:
        return "len3"
    if len(name) >= 4:
        return "len4p"
    return "other"


def audit_name_batch(
    records: list[CharacterNameRecord],
    profile: NamingProfile,
    styles: list[CultureNamingStyle],
) -> NameBatchAuditResult:
    issues: list[NameBatchAuditIssue] = []
    style_by_id = {style.style_id: style for style in styles}
    motif_counts: Counter[str] = Counter()
    template_counts: Counter[str] = Counter()
    style_templates: dict[str, Counter[str]] = defaultdict(Counter)
    alias_count = 0
    rare_counts: Counter[str] = Counter()
    for record in records:
        name = record.primary_name
        template = _template_family(name)
        template_counts[template] += 1
        style_templates[record.culture_style_id][template] += 1
        if any([record.nickname, record.public_alias, record.enemy_label]):
            alias_count += 1
        if "·" in name:
            rare_counts["middle_dot"] += 1
        if "-" in name or "—" in name:
            rare_counts["hyphen"] += 1
        for token in profile.motif_token_budget:
            motif_counts[token] += name.count(token)
    for token, budget in profile.motif_token_budget.items():
        if motif_counts[token] > budget:
            issues.append(
                NameBatchAuditIssue(
                    code="motif_budget_exceeded",
                    severity="blocker",
                    message=f"高频主题词「{token}」超预算：{motif_counts[token]}/{budget}",
                    metrics={"token": token, "count": motif_counts[token], "budget": budget},
                )
            )
    for template, count in template_counts.items():
        if count >= max(4, len(records) // 2 + 1):
            issues.append(
                NameBatchAuditIssue(
                    code="template_overuse",
                    severity="fail",
                    message=f"命名模板 {template} 过多：{count}",
                    metrics={"template": template, "count": count},
                )
            )
    for style_id, counter in style_templates.items():
        if len(counter) >= 3 and sum(1 for count in counter.values() if count > 0) >= 3:
            issues.append(
                NameBatchAuditIssue(
                    code="style_drift",
                    severity="warn",
                    message=f"风格 {style_id} 内部模板漂移较大",
                    affected_agent_ids=[r.agent_id for r in records if r.culture_style_id == style_id],
                    metrics=dict(counter),
                )
            )
    for rare_key, quota in profile.rare_structure_quota.items():
        if rare_counts[rare_key] > quota:
            issues.append(
                NameBatchAuditIssue(
                    code="rare_structure_quota_exceeded",
                    severity="fail",
                    message=f"稀有结构 {rare_key} 超额：{rare_counts[rare_key]}/{quota}",
                    metrics={"kind": rare_key, "count": rare_counts[rare_key], "quota": quota},
                )
            )
    if records and alias_count / len(records) > 0.6:
        issues.append(
            NameBatchAuditIssue(
                code="alias_ratio_too_high",
                severity="blocker",
                message="附属称呼占比过高，疑似外号顶替主名",
                metrics={"alias_count": alias_count, "total": len(records)},
            )
        )
    ai_trace_count = sum(1 for r in records if any(token in r.primary_name for token in ("锈", "灰", "骨", "肺")))
    if records and ai_trace_count >= max(3, len(records) // 2 + 1):
        issues.append(
            NameBatchAuditIssue(
                code="ai_trace_cluster",
                severity="fail",
                message="批量出现 AI 痕迹命名结构",
                metrics={"count": ai_trace_count, "total": len(records)},
            )
        )
    blocked = any(issue.severity == "blocker" for issue in issues)
    regeneration_queue = sorted(
        {
            agent_id
            for issue in issues
            for agent_id in issue.affected_agent_ids
        }
    )
    return NameBatchAuditResult(
        ok=not any(issue.severity in ("fail", "blocker") for issue in issues),
        blocked=blocked,
        profile_id=profile.profile_id,
        issues=issues,
        summary_metrics={
            "motif_counts": dict(motif_counts),
            "template_counts": dict(template_counts),
            "rare_counts": dict(rare_counts),
            "alias_ratio": (alias_count / len(records)) if records else 0.0,
            "styles": {
                style_id: style_by_id.get(style_id).culture_name if style_id in style_by_id else style_id
                for style_id in style_templates
            },
        },
        regeneration_queue=regeneration_queue,
    )
