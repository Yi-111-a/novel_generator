"""把指定项目落库的 style_skill 全文 + style_skill_prompt() 拼出来的注入块导出。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from novel_engine import db
from novel_engine.repository import Repository


def main(project_id: str) -> None:
    db_path = ROOT / "server" / ".data" / "projects" / f"{project_id}.db"
    conn = db.connect(str(db_path))
    repo = Repository(conn)

    sk = repo.get_style_skill()
    tone = repo.get_tone_profile()

    out = ROOT / "outputs" / "longzu_real_distill_run" / "style_dump.md"
    lines = []
    lines.append("# style_skill 原始字段")
    lines.append(f"- name: {sk.name}")
    lines.append(f"- source: {sk.source}")
    lines.append(f"- enabled: {sk.enabled}")
    lines.append(f"- register: {sk.register}")
    lines.append(f"- rhythm: {sk.rhythm}")
    lines.append(f"- devices: {sk.devices}")
    lines.append(f"- diction_do: {sk.diction_do}")
    lines.append(f"- diction_dont: {sk.diction_dont}")
    lines.append(f"- motifs: {sk.motifs}")
    lines.append(f"- samples ({len(sk.samples)} 条):")
    for i, s in enumerate(sk.samples, 1):
        lines.append(f"  [{i}] {s[:300]}{'…' if len(s) > 300 else ''}")
    lines.append("")
    lines.append("## persona_md（AWS 派生的画像，最重要）")
    lines.append("```")
    lines.append(sk.persona_md or "（空）")
    lines.append("```")
    lines.append("")
    lines.append("## 拼成的 style_skill_prompt()（每次生成时注入到 system prompt）")
    lines.append("```")
    lines.append(repo.style_skill_prompt() or "（空）")
    lines.append("```")
    lines.append("")
    lines.append("## tone_profile_prompt()（前置文风契约）")
    lines.append("```")
    lines.append(repo.tone_profile_prompt() or "（空）")
    lines.append("```")

    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "proj_aa7cc7d5")
