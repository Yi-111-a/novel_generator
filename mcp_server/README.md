# Novel World MCP Server

把 Novel World 写作引擎(Harness)暴露成 **MCP 工具**,让任何 MCP 客户端
(Claude Desktop / IDE Agent 等)都能直接调用:检视项目世界状态、查询知识图谱、
跑确定性连续性护栏。全部**只读、零 LLM 调用**,直接读 `server/.data/projects/` 下的项目库。

## 工具

| 工具 | 作用 |
|---|---|
| `novel_list_projects` | 列出所有项目(id/标题/类型/状态) |
| `novel_get_world_bible` | 读项目世界观设定(Story Bible 分节文本) |
| `novel_query_knowledge_graph` | 查 W5 知识图谱:实体关系边 + 注意力强度 + 激活章,支持按实体/关系过滤与分页 |
| `novel_list_chapters` | 列可读章节(按高潮断章分组):标题/场数/正文字数/在场角色/节拍 |
| `novel_audit_chapter` | 对某章正文跑确定性护栏:时间倒流(硬信号)+ 范围/道具越权(建议项) |

全部工具 `readOnlyHint=true`,用 Pydantic 校验入参,返回 JSON,错误可操作(会提示可用 id/章号)。

## 运行

```bash
pip install -r mcp_server/requirements.txt
python mcp_server/novelworld_mcp.py        # stdio 传输
```

## 接入 Claude Desktop

在 `claude_desktop_config.json` 加:

```json
{
  "mcpServers": {
    "novelworld": {
      "command": "python",
      "args": ["C:/Users/yiyin/Desktop/novel_world/mcp_server/novelworld_mcp.py"]
    }
  }
}
```

之后即可对 Claude 说:「列出我的小说项目」「查 proj_31157567 的知识图谱里跟主角敌对的势力」
「审一下第 2 章有没有时间倒流」。

## 设计说明

- **可读章节 vs 章节计划**:正文以场景(scenes)落库、按高潮张力分组成可读章节;
  规划侧(chapter_plans)按章序号与之配对,提供白名单等上下文。
- **审计的诚实边界**:`novel_audit_chapter` 的 verdict 只由**时间倒流**(跨章硬信号)决定;
  范围/道具越权是**生成期闸门**,对已存正文做事后重审会偏严,故按类型给计数+样例,
  标为 `scope_advisory` 仅供参考,不据此判定阻断。
