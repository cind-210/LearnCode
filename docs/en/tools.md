# Tools

Tools are defined by `ToolDefinition` in `src/tool.py` and registered in `src/tools/base.py`.

## Registry

`ToolRegistry` supports:

- `list()`: list definitions.
- `find(name)`: find by name.
- `execute(tool_name, input, context)`: run a tool.
- `merge(other)`: merge built-in and MCP tools.
- metadata for skills and MCP servers.

## Built-In Tools

### `list_files`

Lists one directory with `os.listdir`.

Input:

- `path`: directory path, default `.`

It is not recursive.

### `read_file`

Reads UTF-8 text with line numbering.

Input:

- `path`
- `offset`: 1-based line offset, default `1`
- `limit`: max lines, default `2000`

### `write_file`

Writes UTF-8 text to a file, creating parent directories.

Input:

- `path`
- `content`

### `edit_file`

Performs exact string replacement.

Input:

- `path`
- `old_string`
- `new_string`
- `replace_all`

If `replace_all` is false and the old string appears more than once, the tool fails.

### `grep_files`

Searches text with Python regex.

Input:

- `pattern`
- `path`, default `.`

If `path` is a directory, it recursively walks all files with `os.walk`.

### `run_command`

Runs a shell command with `asyncio.create_subprocess_shell`.

Input:

- `command`
- `cwd`
- `timeout`, default `60`

### `ask_user`

Currently returns a textual marker:

```text
[ASK_USER] question
```

It does not yet pause the browser UI for structured user input.

### `web_fetch`

Fetches a URL with `httpx.AsyncClient` and returns the first 10,000 chars.

### `web_search`

Currently a placeholder. It returns:

```text
[WEB_SEARCH] Query: ...
Results would be returned here.
```

## Current Limitations

- `patch_file`, `modify_file`, and `load_skill` are not implemented as built-in tools.
- `web_search` is not a real search integration.
- `run_command` uses shell execution directly; command safety depends on permission logic.
- Many tool functions convert exceptions into `ToolResult(ok=False, output=str(e))`.
