# MCP 工具接入流程

MCP 相关代码在 `src/mcp/client.py`。它会启动本地 MCP server，询问 server 有哪些工具，再把这些工具包装成 LearnCode 的工具。

## 配置来源

MCP server 配置会被读进 `RuntimeConfig.mcp_servers`。每个 server 的配置大致是：

```json
{
  "command": "node",
  "args": ["server.js"],
  "env": {"KEY": "value"},
  "url": null,
  "headers": null,
  "cwd": "C:/path/to/server",
  "enabled": true,
  "protocol": "newline-json"
}
```

字段里有 `url`、`headers`、`protocol`，但当前 `src/mcp/client.py` 只实现了本地 stdio，一行一个 JSON。

## 启动 MCP server

agent loop 开始时会读取运行配置。如果配置里有 MCP server，会调用 MCP 构建函数。

当前启动方式是：

```text
command + args
```

代码用 `asyncio.create_subprocess_exec()` 启动子进程，并连接它的 stdin 和 stdout。`cwd` 会作为子进程工作目录，`env` 会合并到当前环境变量里。

启动失败的 server 会被跳过。

这一步只影响本次 agent loop 的工具列表，不会把 MCP 工具写进 session 文件。

## 初始化连接

连接建立后，LearnCode 先发送 `initialize`：

下面是当前代码实际发送的请求体。`clientInfo.name` 仍是旧值 `minicode-py`；如果要彻底改名，需要同步修改 `src/mcp/client.py`。

```json
{
  "jsonrpc": "2.0",
  "id": 0,
  "method": "initialize",
  "params": {
    "protocolVersion": "2024-11-05",
    "capabilities": {},
    "clientInfo": {
      "name": "minicode-py",
      "version": "1.0.0"
    }
  }
}
```

然后发送 initialized 通知：

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "notifications/initialized",
  "params": {}
}
```

这条通知在当前代码里也带 `id`，因为底层发送函数按请求格式统一发送。

## 读取 MCP 工具列表

初始化后，LearnCode 发送：

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "tools/list",
  "params": {}
}
```

MCP server 返回的工具大致是：

```json
{
  "tools": [
    {
      "name": "search_docs",
      "description": "Search documents",
      "inputSchema": {
        "type": "object",
        "properties": {
          "query": {"type": "string"}
        },
        "required": ["query"]
      }
    }
  ]
}
```

LearnCode 会把每个工具包装成：

```json
{
  "name": "search_docs",
  "description": "Search documents",
  "input_schema": {
    "type": "object",
    "properties": {
      "query": {"type": "string"}
    },
    "required": ["query"]
  }
}
```

包装后，它和内置工具一样进入工具注册表。

MCP 工具只是额外加入工具注册表；内置工具仍然由 `src/tools/builtin.py` 提供，不依赖 MCP。

## 模型调用 MCP 工具

模型看到 MCP 工具后，调用方式和内置工具一样：

```json
{
  "id": "call_xxx",
  "tool_name": "search_docs",
  "input": {"query": "LearnCode"}
}
```

执行时，LearnCode 给 MCP server 发送：

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "method": "tools/call",
  "params": {
    "name": "search_docs",
    "arguments": {"query": "LearnCode"}
  }
}
```

MCP 返回结果后，LearnCode 只取 `content` 里 `type` 为 `text` 的 block，并把它们拼成工具输出。

MCP 返回大致是：

```json
{
  "content": [
    {"type": "text", "text": "搜索结果"}
  ]
}
```

之后它会按普通工具结果交回模型。工具结果结构看 [tools.md](tools.md)。

## 当前边界

- 只支持本地 stdio newline-json。
- 不支持 `Content-Length` framing。
- 不支持 HTTP MCP。
- 不支持 MCP resources 和 prompts。
- MCP 工具名直接使用原始工具名，没有加 server 前缀。
- server 启动或列工具失败时，这个 server 的工具不会进入工具列表。
