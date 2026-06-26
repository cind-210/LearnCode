# MCP

MCP support lives in `src/mcp.py`.

## Current Capability

LearnCode can launch configured stdio MCP servers and expose their tools through `ToolRegistry`.

Flow:

1. `load_runtime_config()` reads `mcp_servers`.
2. `run_agent_loop()` calls `build_mcp_registry()`.
3. Each configured server is started with `asyncio.create_subprocess_exec`.
4. The client sends `initialize`.
5. The client calls `tools/list`.
6. MCP tools become `ToolDefinition` objects.
7. The built-in registry is merged with MCP registry.

## Protocol Shape

The current Python MCP client uses newline-delimited JSON:

```json
{"jsonrpc":"2.0","id":0,"method":"initialize","params":{...}}
```

Requests are written to stdin with a trailing newline, and stdout is read line by line.

## Tool Calls

An MCP tool is wrapped as:

```python
ToolDefinition(
    name=mcp_tool["name"],
    description=mcp_tool.get("description", ""),
    input_schema=mcp_tool.get("inputSchema", {}),
    run=_run,
)
```

Calling the tool sends:

```json
{"method":"tools/call","params":{"name":"tool_name","arguments":{...}}}
```

Text blocks from MCP results are joined into the tool output.

## Config

MCP server config is represented by `McpServerConfig` in `src/config.py`.

Fields:

- `command`
- `args`
- `env`
- `url`
- `headers`
- `cwd`
- `enabled`
- `protocol`

Only local stdio newline-json behavior is currently implemented by `src/mcp.py`.

## Current Limitations Compared With MiniCode

- No `Content-Length` stdio framing.
- No streamable HTTP MCP client.
- No protocol auto-negotiation/cache.
- No server status summary in the web UI.
- No `mcp__server__tool` name prefixing yet; tools use raw MCP tool names.
- No MCP resource helper tools: `list_mcp_resources`, `read_mcp_resource`.
- No MCP prompt helper tools: `list_mcp_prompts`, `get_mcp_prompt`.
- Connection errors are mostly swallowed and result in no MCP tools.
