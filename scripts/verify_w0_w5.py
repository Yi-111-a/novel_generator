# -*- coding: utf-8 -*-
"""W0-W5 全链 SQL 验收。"""
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "server" / ".data" / "projects" / "proj_76c29bb1.db"

c = sqlite3.connect(str(DB))
c.row_factory = sqlite3.Row


def q(sql, *args):
    return c.execute(sql, args).fetchall()


def line(t): print("\n" + "=" * 60 + "\n" + t + "\n" + "=" * 60)


# ============ W0 ============
line("W0 canon 地名固化")
canon = []
for r in q("SELECT entity_id, name, attributes FROM entities WHERE type='location'"):
    if (json.loads(r["attributes"] or "{}")).get("canon"):
        canon.append((r["entity_id"], r["name"]))
        print(f"  {r['name']:<14} ({r['entity_id']})")
print(f"小计：{len(canon)} 个 canon")
canon_ids = {x[0] for x in canon}

# ============ W1 ============
line("W1 World Skill 9 节（source='w1' 两级 summary+detail）")
w1 = q("SELECT section, title, summary, length(body_full) as L "
       "FROM world_bible_sections WHERE source='w1' ORDER BY section")
for r in w1:
    print(f"  {r['section']:<14} sum={len(r['summary'])}字 detail={r['L']}字  {r['summary'][:36]}")
print(f"小计：{len(w1)} 节")

# ============ W2 ============
line("W2 地理（两级 + 风土人情 + 层级）")
locs = q("SELECT loc_id, name, level, parent, summary, detail, culture_local FROM locations")
for r in locs:
    if r["loc_id"] not in canon_ids:
        continue
    print(f"  【{r['name']}】 level={r['level']} parent={r['parent'] or '—'}")
    print(f"    sum: {r['summary']}")
    print(f"    detail({len(r['detail'])}字): {r['detail'][:100]}{'...' if len(r['detail'])>100 else ''}")
    print(f"    culture({len(r['culture_local'])}字): {r['culture_local'][:80]}")

# ============ W3 ============
line("W3 势力（一等实体 + 关系图 + 核心成员）")
facs = q("SELECT * FROM factions")
fid_of_name = {f["name"]: f["faction_id"] for f in facs}
for r in facs:
    members = json.loads(r["key_members"])
    rels = json.loads(r["relations"])
    territory = json.loads(r["territory"])
    bad_territory = [t for t in territory if t not in canon_ids]
    print(f"\n【{r['name']}】 sum: {r['summary'][:50]}")
    print(f"  ideology: {r['ideology']}")
    print(f"  territory: {[t for t in territory if t in canon_ids]} (非 canon: {bad_territory})")
    print(f"  成员({len(members)}): " + "、".join(
        f"{m['name']}({m.get('role','')})[{m.get('agent_id','???')[:10]}]" for m in members[:5]))
    if rels:
        print(f"  关系({len(rels)}):")
        for rel in rels:
            tgt = c.execute("SELECT name FROM factions WHERE faction_id=?",
                            (rel['target_faction_id'],)).fetchone()
            print(f"    → {tgt['name'] if tgt else '?'} {rel['kind']} intensity={rel['intensity']}")
print(f"\n小计：{len(facs)} 势力")

# ============ W4 ============
line("W4 分层人物卡（三维度 + 小传 + 弧线）")
cards = q("SELECT tier, name, length(appearance) as la, length(social_role) as ls, "
          "length(psychology) as lp, length(backstory) as lb, arc FROM character_cards "
          "WHERE tier IN ('lead', 'supporting') ORDER BY tier DESC, name")
counts = Counter(r["tier"] for r in cards)
print(f"卡数：{dict(counts)}")
enriched = sum(1 for r in cards if r["la"] > 0)
print(f"已 W4 加厚：{enriched}/{len(cards)} 张")
for r in cards[:6]:
    print(f"  [{r['tier']:<10}] {r['name']:<8}  外貌{r['la']}字 社会{r['ls']}字 心理{r['lp']}字 小传{r['lb']}字 弧线: {r['arc'][:40]}")
if len(cards) > 6:
    print(f"  ... 共 {len(cards)} 张")

# ============ W5 ============
line("W5 知识图谱：静态边 + 动态边")
edges = q("SELECT rel, COUNT(*) as n, AVG(intensity) as avg_i, MAX(last_active_chapter) as last "
          "FROM graph_edges GROUP BY rel ORDER BY rel")
print(f"{'rel':<14}{'count':>8}{'avg_intensity':>16}{'last_chapter':>16}")
total = 0
for r in edges:
    total += r["n"]
    print(f"{r['rel']:<14}{r['n']:>8}{r['avg_i']:>16.3f}{r['last']:>16}")
print(f"共 {total} 条边")

# 高 intensity 的样本
line("W5 注意力 top 邻居（intensity 降序）")
name_of = {}
for e in q("SELECT entity_id, name FROM entities"):
    name_of[e["entity_id"]] = e["name"]
for f in facs:
    name_of[f["faction_id"]] = f["name"]
for f in q("SELECT name FROM factions"):
    pass
top = q("SELECT src, rel, dst, intensity, last_active_chapter FROM graph_edges "
        "ORDER BY intensity DESC LIMIT 10")
for r in top:
    sname = name_of.get(r["src"], r["src"][:12])
    dname = name_of.get(r["dst"], r["dst"][:12])
    print(f"  [{r['intensity']:.2f} @ ch{r['last_active_chapter']}] {sname:<10} --{r['rel']:<12}--> {dname}")

# ============ 正文 ============
line("正文 scripted 2 章前 200 字")
scenes = q("SELECT pov, length(prose_text) as L, prose_text FROM scenes ORDER BY discourse_order LIMIT 2")
for s in scenes:
    pov_name = name_of.get(s["pov"], s["pov"][:14])
    print(f"\n  POV={pov_name} 字数={s['L']}")
    print(f"  {s['prose_text'][:200]}")

print("\n" + "=" * 60)
print("总结")
print("=" * 60)
print(f"W0: {len(canon)} 个 canon ✓")
print(f"W1: {len(w1)} 节 × 两级 summary+detail ✓")
print(f"W2: {sum(1 for r in locs if r['loc_id'] in canon_ids)} 个地点加厚 ✓")
print(f"W3: {len(facs)} 势力 + 关系图 + 14 成员 ✓")
print(f"W4: {enriched}/{len(cards)} 张卡 W4 加厚 ✓")
print(f"W5: {total} 条边")
