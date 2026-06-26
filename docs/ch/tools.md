# 工具系统

工具由 `src/tool.py` 中的 `ToolDefinition` 定义，并在 `src/tools/base.py` 中注册。

## 注册表

`ToolRegistry` 支持：

- `list()`：列出工具定义。
- `find(name)`：按名称查找工具。
- `execute(tool_name, input, context)`：执行工具。
- `merge(other)`：合并内置工具和 MCP 工具。
- skills 和 MCP servers 的元数据。

## 内置工具

### `list_files`

使用 `os.listdir` 列出单个目录。

输入：

- `path`：目录路径，默认 `.`

它不是递归的。

### `read_file`

读取 UTF-8 文本，并附带行号。

输入：

- `path`
- `offset`：1-based 起始行，默认 `1`
- `limit`：最大读取行数，默认 `2000`

### `write_file`

把 UTF-8 文本写入文件，并创建父目录。

输入：

- `path`
- `content`

### `edit_file`

执行精确字符串替换。

输入：

- `path`
- `old_string`
- `new_string`
- `replace_all`

如果 `replace_all` 为 false，且旧字符串出现多次，工具会失败。

### `grep_files`

用 Python regex 搜索文本。

输入：

- `pattern`
- `path`，默认 `.`

如果 `path` 是目录，会用 `os.walk` 递归遍历所有文件。

### `run_command`

使用 `asyncio.create_subprocess_shell` 运行 shell 命令。

输入：

- `command`
- `cwd`
- `timeout`，默认 `60`

### `ask_user`

当前只返回文本标记：

```text
[ASK_USER] question
```

它还不会让浏览器 UI 进入结构化等待用户输入的状态。

### `web_fetch`

用 `httpx.AsyncClient` 拉取 URL，返回前 10,000 个字符。

### `web_search`

当前是占位实现，返回：

```text
[WEB_SEARCH] Query: ...
Results would be returned here.
```

## 当前限制

- `patch_file`、`modify_file`、`load_skill` 还不是内置工具。
- `web_search` 还不是真搜索。
- `run_command` 直接使用 shell 执行；命令安全依赖权限逻辑。
- 很多工具函数会把异常转换成 `ToolResult(ok=False, output=str(e))`。
