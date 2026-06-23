from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from novel_engine.chapter_scope_validator import contains_plotting_content


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "server" / ".data" / "projects" / "proj_53ab5edd.db"


def _clean_world_text(text: str) -> tuple[str, str]:
    import re

    safe: list[str] = []
    locked: list[str] = []
    for sentence in re.split(r"(?<=[。！？；\n])", text or ""):
        sentence = sentence.strip()
        if not sentence:
            continue
        (locked if contains_plotting_content(sentence) else safe).append(sentence)
    return "".join(safe), "\n".join(locked)


def main() -> None:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = DB_PATH.with_name(f"{DB_PATH.stem}.before_scope_cleanup_{stamp}.db")
    shutil.copy2(DB_PATH, backup)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE")

        # Remove everything derived from the previously accepted chapter while
        # preserving seed/canon facts at story_time=0.
        event_ids = [
            row["event_id"] for row in conn.execute(
                "SELECT event_id FROM events WHERE beat_id IN "
                "(SELECT chapter_id FROM chapter_plans WHERE sequence_order=1)"
            )
        ]
        fact_ids = [
            row["fact_id"] for row in conn.execute(
                "SELECT fact_id FROM facts WHERE story_time>0 OR source_event_id IN "
                f"({','.join('?' for _ in event_ids)})" if event_ids
                else "SELECT fact_id FROM facts WHERE story_time>0",
                event_ids,
            )
        ]
        if fact_ids:
            marks = ",".join("?" for _ in fact_ids)
            conn.execute(f"DELETE FROM agent_knowledge WHERE fact_id IN ({marks})", fact_ids)
            conn.execute(f"DELETE FROM reader_knowledge WHERE fact_id IN ({marks})", fact_ids)
            conn.execute(f"DELETE FROM facts WHERE fact_id IN ({marks})", fact_ids)
        conn.execute("DELETE FROM scenes")
        if event_ids:
            marks = ",".join("?" for _ in event_ids)
            conn.execute(f"DELETE FROM events WHERE event_id IN ({marks})", event_ids)
        conn.execute("DELETE FROM character_chapter_logs WHERE chapter_seq=1")
        conn.execute("DELETE FROM accepted_chapters WHERE chapter_no=1")

        conn.execute(
            "UPDATE chapter_drafts SET status='rejected_invalid_scope', accepted_at='' "
            "WHERE id IN (2,3)"
        )
        conn.execute(
            """UPDATE chapter_plans
               SET title='', summary='', scene_ids='[]', status='planned', audited=0
               WHERE sequence_order=1"""
        )

        # Canonical names and aliases.
        husband = conn.execute(
            "SELECT entity_id, attributes FROM entities WHERE name='程行' LIMIT 1"
        ).fetchone()
        if husband:
            attrs = json.loads(husband["attributes"] or "{}")
            attrs.update({
                "canonical_role": "丈夫",
                "forbidden_variants": ["林浩", "徐明城", "陈岳"],
            })
            conn.execute(
                "UPDATE entities SET attributes=? WHERE entity_id=?",
                (json.dumps(attrs, ensure_ascii=False), husband["entity_id"]),
            )
        substitute = conn.execute(
            "SELECT entity_id, attributes FROM entities WHERE name='假林晚' LIMIT 1"
        ).fetchone()
        if substitute:
            attrs = json.loads(substitute["attributes"] or "{}")
            attrs.update({"canonical_role": "替身", "forbidden_variants": ["楚瑶"]})
            conn.execute(
                "UPDATE entities SET attributes=? WHERE entity_id=?",
                (json.dumps(attrs, ensure_ascii=False), substitute["entity_id"]),
            )

        # Canonical locations and chapter location permissions.
        location_rows = {
            row["name"]: row["loc_id"]
            for row in conn.execute("SELECT loc_id,name FROM locations")
        }
        shop_id = location_rows.get("无忧售后服务有限公司")
        villa_id = location_rows.get("锦澜湾别墅区")
        police_id = location_rows.get("江州市刑警支队")
        if villa_id:
            row = conn.execute(
                "SELECT attributes FROM entities WHERE entity_id=?", (villa_id,)
            ).fetchone()
            attrs = json.loads(row["attributes"] or "{}") if row else {}
            attrs.update({
                "canonical_address": "锦澜湾18号",
                "forbidden_addresses": ["锦澜湾8号"],
            })
            conn.execute(
                "UPDATE entities SET attributes=? WHERE entity_id=?",
                (json.dumps(attrs, ensure_ascii=False), villa_id),
            )
            conn.execute(
                """UPDATE locations SET
                   geo_full='江州市北郊住宅区，18号为独栋别墅；外墙米黄色，地下层为普通储藏空间。',
                   summary='江州市北郊的封闭式住宅区，林晚名下地址固定为锦澜湾18号。',
                   detail='林木和围墙隔开街道噪声，夜间只剩物业巡逻车与喷泉水声。'
                   WHERE loc_id=?""",
                (villa_id,),
            )
        for seqs, loc_id in [
            ((1, 2), shop_id),
            ((3, 4, 5), villa_id),
            ((6, 7), police_id),
            ((8,), shop_id),
        ]:
            if loc_id:
                marks = ",".join("?" for _ in seqs)
                conn.execute(
                    f"UPDATE chapter_plans SET location_ids=? WHERE sequence_order IN ({marks})",
                    [json.dumps([loc_id], ensure_ascii=False), *seqs],
                )

        # A memory image is not the physical wedding ring.
        ring_image_id = "obj_ring_image"
        conn.execute(
            """INSERT OR REPLACE INTO entities(entity_id,type,name,attributes,created_tick)
               VALUES(?,?,?,?,0)""",
            (
                ring_image_id,
                "object",
                "断裂婚戒影像",
                json.dumps({
                    "non_physical": True,
                    "source": "林晚最后三分钟记忆碎片",
                    "available_from_chapter": 2,
                }, ensure_ascii=False),
            ),
        )
        object_ids = {
            row["name"]: row["entity_id"]
            for row in conn.execute("SELECT entity_id,name FROM entities WHERE type='object'")
        }
        chapter2_items = [
            object_ids[name]
            for name in ("二叔留下的黑色手机", "无忧售后账本", "断裂婚戒影像")
            if name in object_ids
        ]
        conn.execute(
            """UPDATE chapter_plans
               SET items_present=?, available_items=?, items_introduced=?, items_consumed='[]'
               WHERE sequence_order=2""",
            (
                json.dumps(chapter2_items, ensure_ascii=False),
                json.dumps(chapter2_items, ensure_ascii=False),
                json.dumps([ring_image_id], ensure_ascii=False),
            ),
        )
        chapter1_items = [
            object_ids[name]
            for name in ("褪色键盘",)
            if name in object_ids
        ]
        conn.execute(
            """UPDATE chapter_plans
               SET items_present=?, available_items=?, items_introduced='[]', items_consumed='[]'
               WHERE sequence_order=1""",
            (
                json.dumps(chapter1_items, ensure_ascii=False),
                json.dumps(chapter1_items, ensure_ascii=False),
            ),
        )

        # Replace the legacy husband name throughout outline fields.
        for column in ("beat_goals", "dramatic_question", "exit_state", "ending_hook", "summary"):
            conn.execute(
                f"UPDATE chapter_plans SET {column}=replace({column}, '陈岳', '程行') "
                f"WHERE {column} LIKE '%陈岳%'"
            )

        # Clean prose-level examples out of world-bible RAG sections.
        locked_notes: list[str] = []
        for row in conn.execute(
            "SELECT id,section,body_full,summary FROM world_bible_sections "
            "WHERE section NOT IN ('planning_notes','outline_contract')"
        ).fetchall():
            body, locked_body = _clean_world_text(row["body_full"] or "")
            summary, locked_summary = _clean_world_text(row["summary"] or "")
            if locked_body:
                locked_notes.append(f"[{row['section']}#{row['id']}]\n{locked_body}")
            if locked_summary:
                locked_notes.append(f"[{row['section']}#{row['id']} summary]\n{locked_summary}")
            conn.execute(
                "UPDATE world_bible_sections SET body_full=?, summary=? WHERE id=?",
                (body, summary, row["id"]),
            )
        if locked_notes:
            conn.execute(
                """INSERT INTO world_bible_sections
                   (section,title,body_full,summary,source,created_at)
                   VALUES('planning_notes','清洗出的剧情化世界配置',?,'','scope_cleanup',0)""",
                ("\n\n".join(locked_notes),),
            )

        safe_setting = (
            "现代都市里，无忧售后服务有限公司是阴阳售后局留在人间的工位。"
            "亡者因果未了会留下差评；售后员只能按接单、核验、回执和结算权限处理，"
            "不能复活死者，也不能绕过阳间证据秩序。"
        )
        conn.execute(
            """UPDATE world_bible SET setting_core=?, geography=?, culture=?
               WHERE id=1""",
            (
                safe_setting,
                json.dumps({"note": "江州市包含老城区、锦澜湾住宅区、刑警支队、医院与学校等日常空间。"}, ensure_ascii=False),
                json.dumps({"note": "阳间遵循警务、物业、医疗与家庭秩序；阴间以工单、回执、评分和仲裁运行。"}, ensure_ascii=False),
            ),
        )

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    print(f"backup={backup}")
    print(f"repaired={DB_PATH}")


if __name__ == "__main__":
    main()
