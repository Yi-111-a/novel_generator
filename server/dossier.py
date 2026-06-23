"""Character dossier markdown snapshots for frontend and debugging."""
from __future__ import annotations

from pathlib import Path

from novel_engine.casting import cast_or_get, ensure_cards_for_personas  # noqa: F401
from novel_engine.repository import Repository

from . import config_store

CHAR_DIRNAME = "characters"


def chars_dir(project_id: str) -> Path:
    return Path(config_store.DATA_DIR) / "projects" / project_id / CHAR_DIRNAME


def _entity_name(repo: Repository, eid: str) -> str:
    for entity in repo.list_entities():
        if entity.entity_id == eid:
            return entity.name
    return eid


def build_markdown(repo: Repository, agent_id: str, chapter: int | None = None) -> str:
    persona = repo.get_persona(agent_id)
    if persona is None:
        return ""

    name_record = repo.get_character_name(agent_id)
    display_name = repo.get_character_display_name(agent_id, persona.name)
    held = repo.items_held_by(agent_id)
    known = repo.get_agent_ledger(agent_id)
    arc = persona.arc_state or {}
    card = repo.get_card_for_agent(agent_id)

    stamp = f"（更新于 第 {chapter} 章 / 第{chapter}章）" if chapter else "（初始档案）"
    tier_cn = {"lead": "主角", "supporting": "配角", "extra": "龙套"}
    tier_txt = f" · {tier_cn.get(card.tier, card.tier)}" if card else ""

    lines: list[str] = [
        f"# {display_name}",
        f"> 角色档案 {stamp}{tier_txt} · 由世界状态库镜像生成，随情节演进",
        "",
    ]

    if name_record:
        lines.append("## 命名")
        lines.append(f"- **主名**：{name_record.primary_name or display_name}")
        if name_record.short_name:
            lines.append(f"- **短称**：{name_record.short_name}")
        if name_record.nickname:
            lines.append(f"- **外号**：{name_record.nickname}")
        if name_record.honorific:
            lines.append(f"- **敬称**：{name_record.honorific}")
        if name_record.public_alias:
            lines.append(f"- **旧称/别名**：{name_record.public_alias}")
        if name_record.enemy_label:
            lines.append(f"- **敌方称呼**：{name_record.enemy_label}")
        lines.append("")

    if card:
        lines.append("## 身份（选角卡）")
        if card.one_liner:
            lines.append(f"- **职能**：{card.one_liner}")
        if card.defining_trait:
            lines.append(f"- **定义性特征**：{card.defining_trait}")
        if card.key_relation:
            lines.append(f"- **关键关系**：{card.key_relation}")
        if card.appearance:
            lines.append(f"- **生理（外貌/身材/标志）**：{card.appearance}")
        if card.social_role:
            lines.append(f"- **社会（出身/家庭/阶层/隶属）**：{card.social_role}")
        if card.psychology:
            lines.append(f"- **心理（性格/三观/恐惧）**：{card.psychology}")
        if card.backstory:
            lines.append(f"- **小传**：{card.backstory}")
        if card.arc:
            lines.append(f"- **角色弧线**：{card.arc}")
        lines.append("")

    lines.append("## 设定核心")
    lines.append(f"- **欲望**：{persona.want or '（未定）'}")
    if persona.values:
        values = "、".join(f"{item.get('name')}({item.get('weight')})" for item in persona.values)
        lines.append(f"- **珍视**：{values}")
    lines.append(f"- **致命弱点**：{persona.fatal_flaw or '（未定）'}")
    if persona.obstacles:
        lines.append(f"- **阻碍**：{'、'.join(persona.obstacles)}")
    lines.append("")

    lines.append("## 表达层")
    lines.append(f"- **说话方式**：{persona.voice or '（未定）'}")
    if persona.mannerisms:
        lines.append(f"- **习惯动作**：{'、'.join(persona.mannerisms)}")
    if persona.motif_objects:
        motifs = "、".join(_entity_name(repo, object_id) for object_id in persona.motif_objects)
        lines.append(f"- **关联意象**：{motifs}")
    lines.append("")

    change_note = ""
    if arc.get("changed"):
        tick = arc.get("last_change_tick")
        chosen = arc.get("last_chosen_value")
        if tick:
            change_note = f"（最近变化于第{tick}拍，守住“{chosen}”）"
    lines.append("## 当前状态")
    lines.append(f"- **弧线**：{'已被改变' if arc.get('changed') else '尚未转变'}{change_note}")
    held_names = "、".join(_entity_name(repo, item.object_id) for item in held) if held else "（无）"
    lines.append(f"- **持有物品**：{held_names}")
    lines.append(f"- **已知线索/真相**：{len(known)} 条")
    lines.append("")

    get_logs = getattr(repo, "get_character_logs", None)
    logs = get_logs(agent_id, last_n=5) if get_logs else []
    lines.append("## 近期轨迹")
    if logs:
        for log in logs:
            bits: list[str] = []
            if log.actions:
                bits.append(f"行为：{log.actions}")
            if log.psychology:
                bits.append(f"心理：{log.psychology}")
            if log.intention:
                bits.append(f"下一步意图：{log.intention}")
            if log.items_changed:
                bits.append(f"物品变化：{'、'.join(log.items_changed)}")
            lines.append(f"- 第 {log.chapter_seq} 章：" + (" / ".join(bits) if bits else "（无摘要）"))
    else:
        lines.append("- （尚无逐章轨迹）")
    lines.append("")

    lines.append("## 已付代价")
    if persona.cost_ledger:
        for item in persona.cost_ledger:
            lines.append(f"- {item}")
    else:
        lines.append("- （尚无）")
    lines.append("")
    return "\n".join(lines)


def write_dossier(project_id: str, repo: Repository, agent_id: str, chapter: int | None = None) -> Path | None:
    markdown = build_markdown(repo, agent_id, chapter)
    if not markdown:
        return None
    target_dir = chars_dir(project_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{agent_id}.md"
    path.write_text(markdown, encoding="utf-8")
    return path


def write_all(project_id: str, repo: Repository, chapter: int | None = None) -> list[str]:
    written: list[str] = []
    for persona in repo.list_personas():
        if write_dossier(project_id, repo, persona.agent_id, chapter):
            written.append(persona.agent_id)
    return written


def read_dossier(project_id: str, repo: Repository, agent_id: str) -> str:
    path = chars_dir(project_id) / f"{agent_id}.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return build_markdown(repo, agent_id)
