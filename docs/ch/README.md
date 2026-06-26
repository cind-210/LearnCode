# LearnCode 文档

LearnCode 是一个基于 Python/FastAPI 的 Web 代码助手。核心运行时拆成 Web 接口、Agent 循环、模型适配器、工具、会话、上下文压缩、MCP 和 Skills。

## 阅读顺序

建议先建立全局地图，再看请求如何流动，最后进入工具、会话、上下文、MCP 和 Skills 等细节。

1. [architecture.md](architecture.md)  
   建立整体架构：Web 入口、Agent runtime、工具/模型/会话基础设施、浏览器 UI。

2. [api.md](api.md)  
   理解浏览器和后端怎么通信，包括 HTTP 接口、WebSocket actions、server events。

3. [frontend.md](frontend.md)  
   对照 `static/index.html`，看前端如何发消息、渲染消息、操作会话。

4. [agent-loop.md](agent-loop.md)  
   理解核心执行链路：用户输入如何进入模型、模型如何触发工具、工具结果如何回到上下文。

5. [tools.md](tools.md)  
   看当前内置工具的能力边界：文件读取/写入、搜索、命令执行、ask_user、web_fetch、web_search。

6. [sessions.md](sessions.md)  
   理解会话如何保存、恢复、重命名、fork，以及 compact boundary 如何影响 active context。

7. [context.md](context.md)  
   理解长上下文处理：token 估算、大工具结果落盘、microcompact、manual compact、auto compact、snip compact、context collapse。

8. [models-config.md](models-config.md)  
   看模型和配置如何加载：Anthropic/OpenAI adapter、环境变量、配置合并规则。

9.  [mcp.md](mcp.md)  
    了解当前 MCP 接入方式，以及它和 MiniCode 完整实现相比还缺什么。

10. [skills.md](skills.md)  
    了解当前 Skills 的数据结构、解析方式和限制。

## 按目标阅读

如果你想改 Web 交互，优先读：

- [api.md](api.md)
- [frontend.md](frontend.md)
- [sessions.md](sessions.md)

如果你想改 Agent 行为，优先读：

- [agent-loop.md](agent-loop.md)
- [tools.md](tools.md)
- [models-config.md](models-config.md)

如果你想补齐 MiniCode 能力，优先读：

- [sessions.md](sessions.md)
- [context.md](context.md)
- [mcp.md](mcp.md)
- [skills.md](skills.md)

如果你想排查“为什么模型调用了工具”，优先读：

- [agent-loop.md](agent-loop.md)
- [tools.md](tools.md)
- [models-config.md](models-config.md)

## 代码对照

- `src/main.py` 对照 [api.md](api.md)
- `static/index.html` 对照 [frontend.md](frontend.md)
- `src/agent_loop.py` 对照 [agent-loop.md](agent-loop.md)
- `src/tools/base.py` 对照 [tools.md](tools.md)
- `src/session.py` 对照 [sessions.md](sessions.md)
- `src/compact/*` 和 `src/utils/*` 对照 [context.md](context.md)
- `src/config.py`、`src/anthropic_adapter.py`、`src/openai_adapter.py` 对照 [models-config.md](models-config.md)
- `src/mcp.py` 对照 [mcp.md](mcp.md)
- `src/skills.py` 对照 [skills.md](skills.md)

## 当前实现边界

这些文档描述的是当前 `LearnCode/src` 和 `LearnCode/static` 里的代码实现。部分模块仍沿用 MiniCode 的命名或结构，但并不是所有 MiniCode 功能都已经完整实现。每个模块文档中都列出了已知限制。
