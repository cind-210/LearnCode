# 工具调用流程

工具定义在 `src/tools/builtin.py`，注册和执行入口在 `src/tools/registry.py`。模型不会直接调用 Python 函数，它只返回工具名和输入 JSON。

## 工具定义

每个工具注册成一个 `ToolDefinition`。结构大致是：

```json
{
  "name": "read_file",
  "description": "Read a file from the local filesystem.",
  "input_schema": {
    "type": "object",
    "properties": {
      "path": {"type": "string"},
      "offset": {"type": "integer"},
      "limit": {"type": "integer"}
    },
    "required": ["path"]
  }
}
```

模型请求前，adapter 会读取工具注册表，把这些工具定义转换成 Anthropic 或 OpenAI-compatible API 需要的格式。

## 模型返回工具调用

模型要用工具时，返回结果会被整理成：

```json
{
  "type": "tool_calls",
  "content": "我先读取文件。",
  "calls": [
    {
      "id": "call_xxx",
      "tool_name": "read_file",
      "input": {"path": "README.md"}
    }
  ]
}
```

`content` 是模型在调用工具前写出的文字。`calls` 里每一项对应一次工具调用。

## 后端执行工具

agent loop 收到工具调用后，会把工具名、输入和工具上下文交给工具注册表：

```json
{
  "tool_name": "read_file",
  "input": {"path": "README.md"},
  "context": {
    "workspace": "C:/path/to/project"
  }
}
```

工具注册表按 `tool_name` 找到工具函数并执行。找不到工具时，返回失败结果。

工具执行会影响运行时消息列表。只有本次聊天结束并保存 session 后，相关工具调用和工具结果才会进入 `.sessions/*.jsonl`。

工具函数返回 `ToolResult`：

```json
{
  "ok": true,
  "output": "工具输出",
  "background_task": null,
  "await_user": false
}
```

然后后端把结果变成 `tool_result` 消息，再次发给模型。

```json
{
  "role": "tool_result",
  "tool_use_id": "call_xxx",
  "tool_name": "read_file",
  "content": "工具输出",
  "is_error": false
}
```

如果 `ok` 是 false，`is_error` 会是 true，模型仍然能看到错误文本并决定下一步怎么做。

## 路径解析规则

文件工具会把相对路径拼到 workspace 下面。绝对路径会直接解析成绝对路径。

当前实现没有阻止 `..` 访问 workspace 外部路径。

这个规则只影响文件类工具解析路径，不影响 session 保存目录和模型配置目录。

## `list_files`

列出一个目录的直接子项，不递归。

输入：

```json
{
  "path": "."
}
```

输出是按名称排序后的文件和目录列表，每行一个。

## `read_file`

读取 UTF-8 文本文件，并给每行加行号。

输入：

```json
{
  "path": "README.md",
  "offset": 1,
  "limit": 2000
}
```

`offset` 默认是 1，`limit` 默认是 2000。如果文件还有更多行，输出末尾会加剩余行数提示。

## `write_file`

把文本写入文件。如果父目录不存在，会创建父目录。

输入：

```json
{
  "path": "notes/todo.md",
  "content": "文件内容"
}
```

成功时输出写入后的绝对路径。

## `edit_file`

对文件做精确字符串替换。

输入：

```json
{
  "path": "app.py",
  "old_string": "旧文本",
  "new_string": "新文本",
  "replace_all": false
}
```

`replace_all` 是 false 时，旧文本必须只出现一次。出现 0 次或多次都会失败，避免误改。

## `grep_files`

用 Python 正则搜索文件内容。

输入：

```json
{
  "pattern": "TODO",
  "path": "."
}
```

`path` 是文件时只搜这个文件；是目录时递归遍历。输出最多返回前 200 条匹配。

## `run_command`

用 shell 执行命令。

输入：

```json
{
  "command": "python -m pytest",
  "cwd": "C:/path/to/project",
  "timeout": 60
}
```

`cwd` 没有传时使用 workspace。`timeout` 默认 60 秒。返回里会合并 stdout 和 stderr。

## `ask_user`

当前只是返回一段文本：

```text
[ASK_USER] 问题
```

它不会让浏览器进入等待用户回答的交互状态。

## `web_fetch`

用 `httpx.AsyncClient` 请求 URL，返回响应正文前 10000 个字符。

输入：

```json
{
  "url": "https://example.com"
}
```

## `web_search`

当前是占位工具，不会联网搜索。

输入：

```json
{
  "query": "搜索关键词"
}
```

返回的是说明文本，不是真实搜索结果。

## MCP 工具

如果配置了 MCP server，`src/mcp/client.py` 会把 MCP 返回的工具也包装成 `ToolDefinition`，再和内置工具合并。

合并后模型看到的仍然是普通工具定义，调用方式不变。

## 当前边界

- 没有 `patch_file`、`modify_file`、`load_skill` 这些工具。
- `web_search` 不是真搜索。
- `ask_user` 没有真实前端等待输入流程。
- `run_command` 使用 shell，执行能力取决于当前进程权限。
- 工具函数抛出的异常会变成 `ToolResult(ok=False, output="错误文本")`。
