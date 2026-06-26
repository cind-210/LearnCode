# 架构

LearnCode 主要分为四层：

1. Web 入口：`src/main.py`
2. Agent 运行时：`src/agent_loop.py`
3. 工具、模型、会话基础设施：`src/tools`、`src/tool.py`、`src/*_adapter.py`、`src/session.py`
4. 浏览器 UI：`static/index.html`

## 请求流程

1. 浏览器连接 `/ws`。
2. 用户发送 `chat` action，附带文本和可选 `session_id`。
3. `main.py` 从 `.sessions` 加载或创建会话。
4. `_get_model_adapter()` 根据运行时配置创建模型 adapter。
5. `run_agent_loop()` 追加用户消息，调用模型，按需执行工具，必要时压缩上下文，并返回更新后的消息列表。
6. `save_session()` 把新消息事件追加到 JSONL 事件日志。
7. WebSocket 把 `step`、`done`、`error` 或会话事件发回浏览器。

## 重要文件

- `src/main.py`：FastAPI app、REST 接口、WebSocket action 分发。
- `src/agent_loop.py`：多步 `model -> tools -> model` 循环。
- `src/prompt.py`：系统提示词构造。
- `src/tool.py`：工具注册表、元数据、执行框架。
- `src/tools/base.py`：内置文件、命令、ask-user 和 web 工具。
- `src/session.py`：append-only 会话持久化。
- `src/config.py`：设置、环境变量、MCP 配置、provider 选择。
- `src/anthropic_adapter.py`：Anthropic 风格 API adapter。
- `src/openai_adapter.py`：OpenAI 风格 API adapter。
- `src/mcp.py`：stdio newline-json MCP 工具加载。
- `src/skills.py`：简单本地 skill registry。
- `src/compact/*`：上下文压缩策略。
- `static/index.html`：单文件 Web UI。

## 运行时状态

- 会话存储在 `LearnCode/.sessions`。
- 大型工具输出由 `utils/tool_result_storage.py` 存到 `~/.mini-code/tool-results`。
- 全局设置从 `~/.mini-code/settings.json`、`~/.mini-code/mcp.json` 和环境变量读取。

## 当前限制

- Web UI 是静态 HTML，没有构建系统。
- 没有 MiniCode 终端 UI 对应的 CLI/TUI 入口。
- 部分模块仍使用 MiniCode 兼容路径或环境变量名，例如 `MINI_CODE_HOME`。
