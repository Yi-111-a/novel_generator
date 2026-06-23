# DeepSeek 前缀缓存优化 — 实测

## 改动
scene-writer 的 system 提示原本把「每场都变」的 RAG 世界观检索夹在中间，导致
DeepSeek 自动前缀缓存每次都从该处失效、后面的固定规则反复重算。改为：

- `system` = 项目级 tone/style + 固定叙述规则（视角人名也移出）→ **全项目逐字节一致**。
- 动态世界观检索 + 本场状态 → 移入 `user` 变量后缀；本章级块在前、本场级块在后；
  重写 feedback 永远在最后（只往后缀追加）。

详见 commit `perf(prompt): stabilize scene-writer system prefix for DeepSeek KV-cache`。

## 实测（proj_31157567，第 1 章，连续 3 场，deepseek-v4-flash）

`python scripts/measure_cache.py proj_31157567.db 3`

| | 单次新场景调用的 system 命中 | 机制 |
|---|---|---|
| **改后** | **~2688 token 命中**（约半个 prompt 来自缓存） | system 前缀整本书一致，第 1 场后每场都命中 |
| **改前** | **0**（system 每场都是 miss） | RAG 夹在 system 中，每场字节不同，跨场不缓存 |

改后实跑累计命中率随场数上升：场1 `0%`（冷启动）→ 场2 `48%` → 场3 `72%`。

## 诚实说明
DeepSeek 前缀缓存是**账号级、跨请求持续数小时**，所以两次对照跑的「累计命中率」
会互相污染，不能直接对比。干净、无污染的信号是**单个新场景调用里 system 前缀的命中**：
改后一次新调用即命中 ~2688 token，改前为 0。命中部分计费约 1/10、并降低首字延迟（TTFT）。

## 推广到其它调用点
同一手法（system 只放稳定内容，变量进 user）扫了全部 LLM 调用点：

| 调用点 | 状态 | 处理 |
|---|---|---|
| `narration/scene_writer`（正文，最热） | 旧版 system 夹动态 RAG → 已修 | 见上，实测 |
| `casting.enrich_character_cards`（逐角色加厚） | 旧版 system 含角色名 → 已修 | 角色名移入 user；同 tier 角色共享前缀。回归测试 `test_enrich_card_system_prefix_stable_across_characters` |
| `narration/audit._llm_audit`（逐章审计） | system 本就是常量 | 已是最优，无需改 |
| `narration/controller`、`narration/fact_delta`、`narration/batch_audit` | system 本就是常量 | 已是最优，无需改 |

结论：热点路径只有 scene_writer 与 casting 把变量塞进了 system；两处已修，其余本就正确。
每处都用「system 跨调用逐字节一致」的回归测试钉死，防止以后再把变量写回 system。
