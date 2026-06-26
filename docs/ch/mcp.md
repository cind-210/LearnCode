# MCP

MCP 支持位于 `src/mcp.py`。

## 当前能力

LearnCode 可以启动已配置的 stdio MCP server，并通过 `ToolRegistry` 暴露其工具。

流程：

1. `load_runtime_config()` 读取 `mcp_servers`。
2. `run_agent_loop()` 调用 `build_mcp_registry()`。
3. 每个配置的 server 通过 `asyncio.create_subprocess_exec` 启动。
4. 客户端发送 `initialize`。
5. 客户端调用 `tools/list`。
6. MCP 工具变成 `ToolDefinition` 对象。
7. 内置 registry 与 MCP registry 合并。

## 协议形态

当前 Python MCP client 使用 newline-delimited JSON：

```json
{"jsonrpc":"2.0","id":0,"method":"initialize","params":{...}}
```

请求会写入 stdin，并追加换行；stdout 按行读取。

## 工具调用

一个 MCP 工具会被包装成：

```python
ToolDefinition(
    name=mcp_tool["name"],
    description=mcp_tool.get("description", ""),
    input_schema=mcp_tool.get("inputSchema", {}),
    run=_run,
)
```

调用工具时发送：

```json
{"method":"tools/call","params":{"name":"tool_name","arguments":{...}}}
```

MCP 结果中的 text blocks 会拼接成工具输出。

## 配置

MCP server 配置由 `src/config.py` 中的 `McpServerConfig` 表示。

字段：

- `command`
- `args`
- `env`
- `url`
- `headers`
- `cwd`
- `enabled`
- `protocol`

当前 `src/mcp.py` 只实现了本地 stdio newline-json 行为。

## 与 MiniCode 相比的当前限制

- 不支持 `Content-Length` stdio framing。
- 不支持 streamable HTTP MCP client。
- 不支持协议自动协商/缓存。
- Web UI 没有 server status summary。
- 还没有 `mcp__server__tool` 名称前缀；工具使用原始 MCP tool name。
- 没有 MCP resource helper tools：`list_mcp_resources`、`read_mcp_resource`。
- 没有 MCP prompt helper tools：`list_mcp_prompts`、`get_mcp_prompt`。
- 连接错误大多会被吞掉，结果就是没有 MCP 工具可用。
