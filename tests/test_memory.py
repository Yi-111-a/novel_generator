from novel_engine.memory import KeywordEmbedder, MemoryStore
from novel_engine.seed import PROTAGONIST_ID, seed_m2


def test_retrieve_ranks_relevant_first():
    repo = seed_m2()
    mem = MemoryStore(repo)
    # 林晚账本里有"玉佩""师父暴毙"等记忆；查询玉佩应让玉佩相关排前
    ranked = mem.score_debug(PROTAGONIST_ID, "那半枚玉佩在哪里")
    assert ranked
    top = ranked[0].item
    assert "玉佩" in top.version_content


def test_retrieve_returns_all_when_small():
    repo = seed_m2()
    mem = MemoryStore(repo)
    led = repo.get_agent_ledger(PROTAGONIST_ID)
    got = mem.retrieve(PROTAGONIST_ID, "任意查询", k=10)
    assert len(got) == len(led)  # 账本不大时全给


def test_retrieve_top_k_limits():
    repo = seed_m2()
    mem = MemoryStore(repo)
    # 灌入足够多记忆，再检索 top-k
    from novel_engine.models import KnowledgeItem

    for i in range(12):
        repo.upsert_knowledge(KnowledgeItem(PROTAGONIST_ID, f"f_pad_{i}", f"无关记忆{i}", 0.5, i + 1))
    got = mem.retrieve(PROTAGONIST_ID, "玉佩 师父 真相", k=4)
    assert len(got) == 4


def test_consolidate_add_then_noop():
    repo = seed_m2()
    mem = MemoryStore(repo)
    assert mem.consolidate("char_senior", "f_new", "一条全新的记忆。", 1.0, 5) == "ADD"
    assert repo.get_knowledge_entry("char_senior", "f_new") is not None
    # 同一 fact 同内容 → NOOP
    assert mem.consolidate("char_senior", "f_new", "一条全新的记忆。", 1.0, 6) == "NOOP"


def test_consolidate_update_when_more_credible():
    repo = seed_m2()
    mem = MemoryStore(repo)
    mem.consolidate("char_senior", "f_belief", "据传是甲干的。", 0.4, 1)
    # 更可信的新版本 → UPDATE 覆盖
    op = mem.consolidate("char_senior", "f_belief", "确凿是乙干的。", 0.9, 2)
    assert op == "UPDATE"
    entry = repo.get_knowledge_entry("char_senior", "f_belief")
    assert entry.version_content == "确凿是乙干的。" and entry.confidence == 0.9


def test_consolidate_keeps_stronger_existing():
    repo = seed_m2()
    mem = MemoryStore(repo)
    mem.consolidate("char_senior", "f_b2", "确信的旧信念。", 0.9, 1)
    op = mem.consolidate("char_senior", "f_b2", "动摇的新说法。", 0.5, 2)
    assert op == "NOOP"
    assert repo.get_knowledge_entry("char_senior", "f_b2").version_content == "确信的旧信念。"


def test_consolidate_dedup_near_duplicate():
    repo = seed_m2()
    mem = MemoryStore(repo)
    mem.consolidate("char_senior", "f_x1", "甲在后山杀了人。", 1.0, 1)
    # 不同 fact_id 但内容近重复 → NOOP（去重）
    op = mem.consolidate("char_senior", "f_x2", "甲在后山杀了人。", 1.0, 2)
    assert op == "NOOP"
    assert repo.get_knowledge_entry("char_senior", "f_x2") is None


def test_consolidate_delete_on_zero_confidence():
    repo = seed_m2()
    mem = MemoryStore(repo)
    mem.consolidate("char_senior", "f_del", "将被推翻的记忆。", 0.8, 1)
    assert mem.consolidate("char_senior", "f_del", "", 0.0, 2) == "DELETE"
    assert repo.get_knowledge_entry("char_senior", "f_del") is None


def test_keyword_embedder_similarity():
    e = KeywordEmbedder()
    assert e.similarity("玉佩在剑匣里", "玉佩在剑匣里") == 1.0
    assert e.similarity("完全无关甲乙", "另一个话题丙丁") < 0.2
