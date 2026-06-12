# -*- coding: utf-8 -*-
"""W2+W3 重锁后的 SQL 验收。"""
import sys
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "server" / ".data" / "projects" / "proj_4d65cf87.db"

c = sqlite3.connect(str(DB))
c.row_factory = sqlite3.Row

def q(sql, *args):
    return c.execute(sql, args).fetchall()

print("=" * 60)
print("W0 canon 地点")
print("=" * 60)
canon = q("SELECT entity_id, name, attributes FROM entities WHERE type='location'")
canon_ids = []
for r in canon:
    attrs = json.loads(r["attributes"] or "{}")
    if attrs.get("canon"):
        canon_ids.append(r["entity_id"])
        print(f"  {r['entity_id']}  {r['name']}")
print(f"共 {len(canon_ids)} 个 canon")

print("\n" + "=" * 60)
print("W2 地理：canon 地点两级 + 风土人情 + 层级")
print("=" * 60)
locs = q("SELECT loc_id, name, level, parent, summary, detail, culture_local, geo_full FROM locations")
for r in locs:
    if r["loc_id"] not in canon_ids:
        continue
    print(f"\n【{r['name']}】  level={r['level']!r} parent={r['parent']!r}")
    print(f"  summary({len(r['summary'])}字): {r['summary']}")
    print(f"  detail({len(r['detail'])}字): {r['detail'][:140]}{'...' if len(r['detail'])>140 else ''}")
    print(f"  culture_local({len(r['culture_local'])}字): {r['culture_local'][:140]}")

print("\n" + "=" * 60)
print("W3 势力：一等实体 + 关系图 + 核心成员")
print("=" * 60)
facs = q("SELECT * FROM factions")
print(f"共 {len(facs)} 个势力\n")
for r in facs:
    members = json.loads(r["key_members"])
    rels = json.loads(r["relations"])
    territory = json.loads(r["territory"])
    territory_names = []
    for tid in territory:
        nm = c.execute("SELECT name FROM entities WHERE entity_id=?", (tid,)).fetchone()
        territory_names.append(nm["name"] if nm else f"<未知:{tid}>")
        if tid not in canon_ids:
            print(f"  !!! territory {tid} 不是 canon，泄漏 !!!")
    print(f"【{r['name']}】 (id={r['faction_id']})")
    print(f"  ideology: {r['ideology']}")
    print(f"  summary({len(r['summary'])}字): {r['summary']}")
    print(f"  detail({len(r['detail'])}字): {r['detail'][:120]}...")
    print(f"  goals: {r['goals'][:80]}")
    print(f"  methods: {r['methods'][:80]}")
    print(f"  territory: {territory_names}")
    print(f"  secret: {r['secret'][:80]}")
    print(f"  核心成员({len(members)}):")
    for m in members:
        aid = m.get('agent_id', '')
        print(f"    - {m['name']:<10} 角色={m.get('role',''):<10} agent_id={aid}")
    print(f"  关系({len(rels)}):")
    for rel in rels:
        tgt = c.execute("SELECT name FROM factions WHERE faction_id=?", (rel['target_faction_id'],)).fetchone()
        tgt_name = tgt["name"] if tgt else "<未知>"
        print(f"    → {tgt_name} kind={rel['kind']} intensity={rel['intensity']} note={rel['note'][:40]}")
    print()

print("=" * 60)
print("W3 核心成员→character_cards（supporting tier）")
print("=" * 60)
cards = q("SELECT tier, slot_key, name, one_liner, agent_id FROM character_cards "
          "WHERE slot_key LIKE 'faction:%'")
print(f"势力成员卡共 {len(cards)} 张")
for r in cards[:8]:
    print(f"  [{r['tier']}] {r['slot_key']} → {r['name']} | {r['one_liner']}")
if len(cards) > 8:
    print(f"  ... 共 {len(cards)} 张")

print("\n" + "=" * 60)
print("W1 World Skill：source='w1' 行（两级 summary+detail）")
print("=" * 60)
w1 = q("SELECT section, title, summary, length(body_full) as L FROM world_bible_sections "
       "WHERE source='w1' ORDER BY section")
print(f"W1 节共 {len(w1)} 节")
for r in w1:
    print(f"  {r['section']:<15} 「{r['title']}」 sum={r['summary'][:30]!r}  detail={r['L']}字")

print("\n" + "=" * 60)
print("正文：scripted 写的 2 章")
print("=" * 60)
chs = q("SELECT chapter_id, sequence_order, title, status FROM chapter_plans ORDER BY sequence_order")
for r in chs[:8]:
    if r["status"] == "done":
        print(f"  第{r['sequence_order']}章 [{r['status']}] {r['title']}")

scenes = q("SELECT scene_id, pov, length(prose_text) as L, prose_text FROM scenes "
           "ORDER BY discourse_order LIMIT 2")
for s in scenes:
    pov_name = c.execute("SELECT name FROM entities WHERE entity_id=?", (s["pov"],)).fetchone()
    pov_name = pov_name["name"] if pov_name else s["pov"]
    print(f"\n  场 POV={pov_name} 字数={s['L']}")
    print(f"  前 200 字：{s['prose_text'][:200]}")
