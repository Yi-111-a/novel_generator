"""人物档案文件（P5，设计要点⑤）。

DB 是真相源，本模块把每个角色的结构化设定 + 当前状态镜像成一份 `.md` 档案卡：
  projects/<project_id>/characters/<agent_id>.md

档案随情节更新（锁定时初版；每章收束时刷新本章出场人物）。它既是"给 LLM 的角色卡"，
也是前端可展示的人物信息文件——内容全部来自 Repository，不引入第二份真相。
"""
from __future__ import annotations

from pathlib import Path

from novel_engine.repository import Repository
# §1 选角逻辑在引擎层 novel_engine.casting；这里 re-export 方便 server 调用 + 写 .md 镜像
from novel_engine.casting import cast_or_get, ensure_cards_for_personas  # noqa: F401

from . import config_store

CHAR_DIRNAME = "characters"


def chars_dir(project_id: str) -> Path:
    return Path(config_store.DATA_DIR) / "projects" / project_id / CHAR_DIRNAME


def _entity_name(repo: Repository, eid: str) -> str:
    for e in repo.list_entities():
        if e.entity_id == eid:
            return e.name
    return eid


def build_markdown(repo: Repository, agent_id: str, chapter: int | None = None) -> str:
    p = repo.get_persona(agent_id)
    if p is None:
        return ""
    held = repo.items_held_by(agent_id)
    known = repo.get_agent_ledger(agent_id)
    arc = p.arc_state or {}

    card = repo.get_card_for_agent(agent_id)
    lines: list[str] = []
    lines.append(f"# {p.name}")
    stamp = f"（更新于 第{chapter}章）" if chapter else "（初始档案）"
    tier_cn = {"lead": "主角", "supporting": "配角", "extra": "龙套"}
    tier_txt = f" · {tier_cn.get(card.tier, card.tier)}" if card else ""
    lines.append(f"> 角色档案 {stamp}{tier_txt} · 由世界状态库镜像生成，随情节演进")
    lines.append("")
    if card:  # §1 选角层身份
        lines.append("## 身份（选角卡）")
        if card.one_liner:
            lines.append(f"- **职能**：{card.one_liner}")
        if card.defining_trait:
            lines.append(f"- **定义性特征**：{card.defining_trait}")
        if card.key_relation:
            lines.append(f"- **关键关系**：{card.key_relation}")
        # W4 分层人物卡：三维度
        if getattr(card, "appearance", ""):
            lines.append(f"- **生理（外貌/身材/标志）**：{card.appearance}")
        if getattr(card, "social_role", ""):
            lines.append(f"- **社会（出身/家庭/阶层/隶属）**：{card.social_role}")
        if getattr(card, "psychology", ""):
            lines.append(f"- **心理（性格/三观/恐惧）**：{card.psychology}")
        if card.backstory:
            lines.append(f"- **小传**：{card.backstory}")
        if card.arc:
            lines.append(f"- **角色弧线**：{card.arc}")
        lines.append("")
    lines.append("## 设定核心")
    lines.append(f"- **欲望**：{p.want or '（未定）'}")
    if p.values:
        vals = "、".join(f"{v.get('name')}({v.get('weight')})" for v in p.values)
        lines.append(f"- **珍视**：{vals}")
    lines.append(f"- **致命弱点**：{p.fatal_flaw or '（未定）'}")
    if p.obstacles:
        lines.append(f"- **阻碍**：{'、'.join(p.obstacles)}")
    lines.append("")
    lines.append("## 表达层")
    lines.append(f"- **说话方式**：{p.voice or '（未定）'}")
    if p.mannerisms:
        lines.append(f"- **习惯动作**：{'、'.join(p.mannerisms)}")
    if p.motif_objects:
        lines.append(f"- **关联意象**：{'、'.join(_entity_name(repo, o) for o in p.motif_objects)}")
    lines.append("")
    lines.append("## 当前状态")
    lines.append(f"- **弧线**：{'已被改变' if arc.get('changed') else '尚未转变'}"
                 + (f"（最近变化于第 {arc.get('last_change_tick')} 拍，守住「{arc.get('last_chosen_value')}」）"
                    if arc.get("changed") else ""))
    lines.append(f"- **持有物品**：{('、'.join(_entity_name(repo, i.object_id) for i in held)) if held else '（无）'}")
    lines.append(f"- **已知线索/真相**：{len(known)} 条")
    lines.append("")
    get_logs = getattr(repo, "get_character_logs", None)
    logs = get_logs(agent_id, last_n=5) if get_logs else []
    lines.append("## 近期轨迹")
    if logs:
        for log in logs:
            bits = []
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
    if p.cost_ledger:
        for c in p.cost_ledger:
            lines.append(f"- {c}")
    else:
        lines.append("- （尚无）")
    lines.append("")
    return "\n".join(lines)


def write_dossier(project_id: str, repo: Repository, agent_id: str, chapter: int | None = None) -> Path | None:
    md = build_markdown(repo, agent_id, chapter)
    if not md:
        return None
    d = chars_dir(project_id)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{agent_id}.md"
    path.write_text(md, encoding="utf-8")
    return path


def write_all(project_id: str, repo: Repository, chapter: int | None = None) -> list[str]:
    written: list[str] = []
    for p in repo.list_personas():
        if write_dossier(project_id, repo, p.agent_id, chapter):
            written.append(p.agent_id)
    return written


def read_dossier(project_id: str, repo: Repository, agent_id: str) -> str:
    """读档案；文件缺失则按当前 DB 现状即时构建（保证前端总能拿到内容）。"""
    path = chars_dir(project_id) / f"{agent_id}.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return build_markdown(repo, agent_id)
