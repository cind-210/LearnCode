# 模型与配置

运行时配置实现在 `src/config.py`。

## 配置来源

`load_effective_settings()` 会合并：

1. `~/.claude/settings.json`
2. `~/.mini-code/mcp.json`
3. 项目 `.mcp.json`
4. `~/.mini-code/settings.json`
5. 进程环境变量

最终运行时配置由 `load_runtime_config()` 生成。

## 重要环境变量

- `MINI_CODE_MODEL`
- `ANTHROPIC_MODEL`
- `MINI_CODE_ANTHROPIC_BASE_URL`
- `MINI_CODE_OPENAI_BASE_URL`
- `ANTHROPIC_BASE_URL`
- `MINI_CODE_AUTH_TOKEN`
- `ANTHROPIC_AUTH_TOKEN`
- `MINI_CODE_API_KEY`
- `ANTHROPIC_API_KEY`
- `MINI_CODE_MAX_OUTPUT_TOKENS`

## Provider 选择

默认 provider 是 `anthropic`。

当设置了 `MINI_CODE_OPENAI_BASE_URL` 且没有设置 `MINI_CODE_ANTHROPIC_BASE_URL` 时，provider 切换为 `openai`。

## Anthropic Adapter

`src/anthropic_adapter.py`：

- 把内部 `ChatMessage` 转换成 Anthropic message blocks
- 把工具作为 Anthropic tool definitions 发送
- 解析文本、thinking blocks、tool use 和 usage
- 根据 retry 环境变量/配置支持重试

## OpenAI Adapter

`src/openai_adapter.py`：

- 把内部消息转换成 OpenAI-compatible chat messages
- 发送内置工具 schema
- 解析 assistant content 和 tool calls
- 可用时记录 usage

## Mock Adapter

`src/mock_model.py` 是离线/测试模型。

支持的 mock 命令：

- `/tools`
- `/ls`
- `/grep`
- `/read`
- `/cmd`
- `/write`
- `/edit`

普通自由文本会返回静态帮助消息。

## 当前限制

- 配置名仍使用 `MINI_CODE_*`。
- 这个 Python Web app 没有交互式安装器。
- OpenAI/Anthropic 兼容性取决于配置的网关是否符合预期响应结构。
