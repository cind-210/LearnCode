# 项目结构和消息路径

LearnCode 的浏览器页面在 `static/index.html`，后端入口在 `src/web/main.py`。用户发出一条消息后，代码会经过 WebSocket、session、agent loop、模型 adapter、工具注册表，最后把结果保存回 session。

## 目录和代码对应关系

- `static/index.html`：页面、样式和前端 JavaScript。
- `src/main.py`：启动入口。
- `src/web/main.py`：HTTP 接口和 WebSocket action 分发。
- `src/loop/runner.py`：把用户输入加入消息列表、请求模型、执行工具、再次请求模型。
- `src/loop/messages.py`：后端内部的消息、工具调用、模型返回结果结构。
- `src/models/anthropic.py`：Anthropic 请求和响应解析。
- `src/models/openai.py`：OpenAI-compatible 请求和响应解析。
- `src/tools/builtin.py`：内置工具实现。
- `src/tools/registry.py`：工具定义和执行入口。
- `src/sessions/store.py`：session JSONL 读写。
- `src/context/*`：估算消息长度、限制工具结果长度、缩短发给模型的消息列表。
- `src/mcp/client.py`：启动 MCP server，并把 MCP tools 接入工具列表。
- `src/skills/registry.py`：读取 skill 文件，把名称和描述放进 system message。
- `src/config/runtime.py`：读取模型、key、base url、MCP 等配置。

## 一条消息的执行路径

1. 前端从输入框读出文本，通过 WebSocket 发送 `chat`。
2. 后端收到后检查当前是否已有消息在处理。
3. 没有 `session_id` 时，后端先创建 `New Session` 并通知前端显示。
4. 有 `session_id` 时，后端从 `.sessions` 读取历史消息。
5. 后端根据配置创建 Anthropic 或 OpenAI adapter。
6. 后端把用户输入、历史消息、workspace、权限模式交给 agent loop。
7. agent loop 准备发给模型的消息列表。
8. 模型返回普通回答时，后端保存消息并结束。
9. 模型返回工具调用时，后端执行工具，把结果加入消息列表，再次请求模型。
10. 模型给出最终回答或循环停止后，后端追加保存 session，并通过 WebSocket 发 `done`。

## 后续阅读入口

- 浏览器和后端传什么 JSON：看 [api.md](api.md)。
- 前端收到事件后怎么改页面：看 [frontend.md](frontend.md)。
- 模型和工具怎么循环：看 [agent-loop.md](agent-loop.md)。
- 工具定义和每个内置工具输入：看 [tools.md](tools.md)。
- session 文件怎么写：看 [sessions.md](sessions.md)。
- 消息太长时怎么压缩：看 [context.md](context.md)。
- 模型配置和请求体：看 [models-config.md](models-config.md)。
- MCP 工具怎么接入：看 [mcp.md](mcp.md)。
- skills 怎么进入提示词：看 [skills.md](skills.md)。

## 当前边界

- 前端是单文件 HTML，没有构建工具。
- 没有登录和账户权限。
- 工具权限检查在后端，前端没有完整审批弹窗。
- 环境变量名使用 `LEARN_CODE_*`。
