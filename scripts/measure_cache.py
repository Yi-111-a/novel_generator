"""量测 DeepSeek 前缀缓存命中：真实跑同一章的多场生成，看 system 稳定前缀是否被缓存。

用法:  python scripts/measure_cache.py [项目db] [场数]
读 .env 里的 DEEPSEEK_API_KEY。会产生真实(少量)API 调用。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def _load_config() -> dict:
    """优先读 server/.data/config.json（服务端真实配置），回退到 .env。"""
    import json
    cfg = ROOT / "server" / ".data" / "config.json"
    if cfg.exists():
        d = json.loads(cfg.read_text(encoding="utf-8"))
        return {
            "key": d.get("llmApiKey") or (d.get("llmApiKeys") or [None])[0],
            "model": d.get("modelName", "deepseek-chat"),
            "base_url": d.get("baseUrl", "https://api.deepseek.com"),
        }
    env = {}
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return {"key": env.get("DEEPSEEK_API_KEY"),
            "model": env.get("LLM_MODEL", "deepseek-chat"),
            "base_url": env.get("LLM_BASE_URL", "https://api.deepseek.com")}


def main() -> None:
    db_name = sys.argv[1] if len(sys.argv) > 1 else "proj_31157567.db"
    n_beats = int(sys.argv[2]) if len(sys.argv) > 2 else 2

    cfg = _load_config()
    key = cfg["key"] or os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        print("缺 API key（server/.data/config.json 或 .env）"); sys.exit(1)

    from novel_engine import db
    from novel_engine.repository import Repository
    from novel_engine.llm.deepseek import DeepSeekClient, CACHE_STATS
    from novel_engine.narration.scene_writer import SceneSpec, SceneWriter

    db_path = ROOT / "server" / ".data" / "projects" / db_name
    repo = Repository(db.connect(str(db_path)))
    llm = DeepSeekClient(api_key=key, model=cfg["model"], base_url=cfg["base_url"])

    ch = next((c for c in repo.list_chapter_plans() if (c.cast or [])), None)
    if ch is None:
        print("没有带 cast 的章节"); sys.exit(1)
    pov = ch.pov_agent or ch.cast[0]
    beats = [b for b in (ch.beat_goals or []) if b][:n_beats] or [ch.summary or "推进本章"]
    while len(beats) < n_beats:
        beats.append(beats[-1] + "（续）")

    writer = SceneWriter(repo, llm)
    print(f"项目={db_name} 第{ch.sequence_order}章 POV={pov} 场数={len(beats)}")
    for i, beat in enumerate(beats):
        writer.write(SceneSpec(pov=pov, beat=beat, chapter=ch, scene_pos=i))
        s = CACHE_STATS.summary()
        print(f"  场{i + 1}后累计: calls={s['calls']} "
              f"hit={s['cache_hit_tokens']} miss={s['cache_miss_tokens']} "
              f"hit_ratio={s['hit_ratio']:.0%}")

    print("\n汇总:", json.dumps(CACHE_STATS.summary(), ensure_ascii=False))


if __name__ == "__main__":
    main()
