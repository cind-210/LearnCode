# 模型请求和配置

模型相关代码分三处：

- `src/config/runtime.py`：读取模型名、base url、key、provider 和 MCP 配置。
- `src/models/anthropic.py`：发送 Anthropic Messages API 请求。
- `src/models/openai.py`：发送 OpenAI-compatible chat completions 请求。

## 配置读取时机

WebSocket 收到 `chat` 后，`src/web/main.py` 会创建模型 adapter。

读取配置后，后端只根据 `runtime.provider` 选择 adapter：

```json
{
  "provider": "anthropic"
}
```

`provider` 是 `openai` 时使用 OpenAI adapter，否则使用 Anthropic adapter。

这一步只决定本次聊天请求使用哪个 adapter，不会修改配置文件。

## 配置来源

`load_runtime_config()` 会先合并配置文件，再合并环境变量。当前会读取这些位置：

```text
~/.claude/settings.json
~/.learncode/mcp.json
项目目录/.mcp.json
~/.learncode/settings.json
进程环境变量
```

如果项目里有 `.env`，`python-dotenv` 可用时会先加载 `.env` 到进程环境变量。

配置读取只发生在运行时。文档下面列出的环境变量会覆盖配置文件里的同名含义。

配置文件里的 settings 大致是：

```json
{
  "env": {
    "ANTHROPIC_MODEL": "claude-sonnet-4"
  },
  "model": "claude-sonnet-4",
  "maxOutputTokens": 4096,
  "mcpServers": {
    "local": {
      "command": "node",
      "args": ["server.js"],
      "env": {},
      "cwd": "C:/path/to/server",
      "enabled": true
    }
  }
}
```

MCP 配置文件只读取 `mcpServers`：

```json
{
  "mcpServers": {
    "local": {
      "command": "node",
      "args": ["server.js"]
    }
  }
}
```

## RuntimeConfig

最终运行配置大致是：

```json
{
  "model": "claude-sonnet-4",
  "base_url": "https://api.anthropic.com/v1/messages",
  "provider": "anthropic",
  "auth_token": null,
  "api_key": "key",
  "max_output_tokens": 4096,
  "mcp_servers": {},
  "source_summary": "config: ..."
}
```

如果没有模型名，代码会报错。没有 auth token 和 api key，也会报错。

## 常用环境变量

- `LEARN_CODE_HOME`：LearnCode 全局配置目录，默认是 `~/.learncode`。
- `LEARN_CODE_MODEL`：模型名，优先级高于 settings 里的 model。
- `ANTHROPIC_MODEL`：没有 `LEARN_CODE_MODEL` 和 settings model 时使用。
- `LEARN_CODE_ANTHROPIC_BASE_URL`：Anthropic 请求地址。
- `LEARN_CODE_OPENAI_BASE_URL`：OpenAI-compatible 请求地址。
- `ANTHROPIC_BASE_URL`：没有上面两个 base url 时使用。
- `LEARN_CODE_AUTH_TOKEN`：Bearer token。
- `ANTHROPIC_AUTH_TOKEN`：没有 `LEARN_CODE_AUTH_TOKEN` 时使用。
- `LEARN_CODE_API_KEY`：API key。
- `ANTHROPIC_API_KEY`：没有 `LEARN_CODE_API_KEY` 时使用。
- `LEARN_CODE_MAX_OUTPUT_TOKENS`：限制模型最多输出多少 token。
- `LEARN_CODE_MAX_RETRIES`：模型请求失败时最多重试多少次。
- `LEARN_CODE_DEBUG_AUTOCOMPACT`：设为 `1` 时输出 auto compact 调试日志。

## provider 选择规则

代码默认 provider 是：

```json
{"provider": "anthropic"}
```

只有满足下面条件时才改成 `openai`：

```text
设置了 LEARN_CODE_OPENAI_BASE_URL
并且没有设置 LEARN_CODE_ANTHROPIC_BASE_URL
```

所以当前 OpenAI-compatible 模型依赖 `LEARN_CODE_OPENAI_BASE_URL` 触发。

## Anthropic adapter 请求

Anthropic adapter 会把内部消息转成 Anthropic Messages API 需要的结构。

请求体大致是：

```json
{
  "model": "claude-sonnet-4",
  "max_tokens": 4096,
  "system": "system message",
  "messages": [
    {
      "role": "user",
      "content": [{"type": "text", "text": "用户输入"}]
    }
  ],
  "tools": [
    {
      "name": "read_file",
      "description": "Read a file from the local filesystem.",
      "input_schema": {"type": "object"}
    }
  ]
}
```

返回后，adapter 会解析文本、thinking block、tool use 和 usage，再整理成 agent loop 能处理的内部结果。内部结果的分支结构看 [agent-loop.md](agent-loop.md)。

## OpenAI adapter 请求

OpenAI adapter 会把内部消息转成 chat completions 格式。

请求体大致是：

```json
{
  "model": "gpt-4.1",
  "messages": [
    {"role": "system", "content": "system message"},
    {"role": "user", "content": "用户输入"}
  ],
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "read_file",
        "description": "Read a file from the local filesystem.",
        "parameters": {"type": "object"}
      }
    }
  ]
}
```

返回后，adapter 会读取 `message.content` 和 `message.tool_calls`。如果模型在工具调用前写了文本，这段文本会保留下来，agent loop 会把它发给前端显示。

## Mock adapter

`src/models/mock.py` 不请求外部模型。它根据用户输入里的命令返回固定结果，适合离线测试。

支持的输入包括：

```text
/tools
/ls
/grep
/read
/cmd
/write
/edit
```

## 当前边界

- 环境变量名使用 `LEARN_CODE_*`。
- provider 选择依赖 base url，比较隐式。
- 没有模型配置向导。
- OpenAI-compatible 是否可用，取决于网关返回格式是否接近 OpenAI chat completions。
