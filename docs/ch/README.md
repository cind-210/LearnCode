# LearnCode 中文文档

这些文档只解释当前 `LearnCode` 代码怎么运行，不解释不存在的设计，也不提前引入一堆概念。

读文档时可以按这个顺序：先看浏览器发消息后代码怎么走，再看模型、工具、会话、压缩这些细节。

## 推荐阅读顺序

1. [architecture.md](architecture.md)
   先看目录分别对应什么代码，以及一条消息从浏览器到模型、工具、session 的路径。

2. [api.md](api.md)
   看浏览器和后端之间通过 HTTP 和 WebSocket 传什么 JSON，后端会返回什么事件。

3. [frontend.md](frontend.md)
   看 `static/index.html` 怎么连接 WebSocket、发送消息、渲染回复和工具调用。

4. [agent-loop.md](agent-loop.md)
   看 `src/loop/runner.py` 里模型和工具怎么来回执行，直到模型给出最终回答。

5. [tools.md](tools.md)
   看工具定义是什么结构，模型怎么返回工具调用，工具结果怎么回到模型。

6. [sessions.md](sessions.md)
   看聊天记录怎么追加进 `.sessions/*.jsonl`，怎么加载、重命名、复制和删除。

7. [context.md](context.md)
   看消息太长或工具结果太大时，代码怎么缩短发给模型的内容。

8. [models-config.md](models-config.md)
   看模型配置从哪里来，Anthropic 和 OpenAI adapter 怎么发请求。

9. [mcp.md](mcp.md)
   看外部 MCP server 怎么启动，MCP tools 怎么包装成 LearnCode 工具。

10. [skills.md](skills.md)
    看本地 skill 文件怎么解析，名称和描述怎么进入 system message。

## 按问题阅读

想知道“点 Send 后发生什么”：读 [frontend.md](frontend.md)、[api.md](api.md)、[agent-loop.md](agent-loop.md)。

想知道“模型为什么会调用工具”：读 [agent-loop.md](agent-loop.md)、[tools.md](tools.md)。

想知道“会话为什么能恢复”：读 [sessions.md](sessions.md)。

想知道“上下文太长怎么办”：读 [context.md](context.md)。

想知道“模型配置怎么选”：读 [models-config.md](models-config.md)。

## 代码对照

- `static/index.html`：浏览器页面和前端逻辑。
- `src/web/main.py`：FastAPI、HTTP 接口、WebSocket action 分发。
- `src/loop/runner.py`：用户消息进入模型、工具执行、再次调用模型。
- `src/loop/messages.py`：聊天消息和模型返回值的数据结构。
- `src/loop/prompt.py`：给模型看的 system message 内容。
- `src/tools/builtin.py`：内置工具。
- `src/tools/registry.py`：工具注册和执行入口。
- `src/sessions/store.py`：会话 JSONL 文件读写。
- `src/context/*`：token 估算、大工具结果保存、压缩。
- `src/models/anthropic.py`：Anthropic 请求和响应解析。
- `src/models/openai.py`：OpenAI-compatible 请求和响应解析。
- `src/config/runtime.py`：模型、key、base url、MCP 配置读取。
- `src/mcp/client.py`：启动 MCP server 并调用 MCP 工具。
- `src/skills/registry.py`：读取 skill 文件并生成提示词片段。

## 写作规则

中文文档统一按 [文档写作要求.md](../../../文档写作要求.md) 写。这里不重复维护规则，避免 README 和规则文件说法不一致。
