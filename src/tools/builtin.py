"""
Built-in tool implementations: file operations, commands, search, etc.

Mirrors src/tools/base.ts from the TypeScript version.
"""
from __future__ import annotations

import asyncio
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Optional

from ..skills.registry import get_default_skill_registry
from .command_permissions import powershell_args
from .registry import ToolDefinition, ToolRegistry, ToolRegistryMetadata, ToolResult, ToolContext


def _resolve_path(path: str, workspace: str) -> str:
    p = Path(path)
    if p.is_absolute():
        return str(p.resolve())
    return str((Path(workspace) / p).resolve())


def _error_text(error: Exception) -> str:
    return str(error) or repr(error) or error.__class__.__name__


async def _list_files(input: dict, context: ToolContext) -> ToolResult:
    path = input.get("path", ".")
    workspace = context.get("workspace", ".")
    resolved = _resolve_path(path, workspace)
    try:
        entries = os.listdir(resolved)
        entries.sort()
        return ToolResult(ok=True, output="\n".join(entries))
    except Exception as e:
        return ToolResult(ok=False, output=_error_text(e))


async def _read_file(input: dict, context: ToolContext) -> ToolResult:
    path = input.get("path", "")
    offset = input.get("offset", 1)
    limit = input.get("limit", 2000)
    workspace = context.get("workspace", ".")
    resolved = _resolve_path(path, workspace)
    try:
        with open(resolved, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        total = len(lines)
        start = max(1, offset) - 1
        end = min(total, start + limit)
        result_lines = lines[start:end]
        output = "".join(f"{(i+1):>6}\t{line}" for i, line in enumerate(result_lines, start))
        if end < total:
            output += f"\n... ({total - end} more lines)"
        return ToolResult(ok=True, output=output)
    except FileNotFoundError:
        return ToolResult(ok=False, output=f"File not found: {resolved}")
    except Exception as e:
        return ToolResult(ok=False, output=_error_text(e))


async def _write_file(input: dict, context: ToolContext) -> ToolResult:
    path = input.get("path", "")
    content = input.get("content", "")
    workspace = context.get("workspace", ".")
    resolved = _resolve_path(path, workspace)
    try:
        os.makedirs(os.path.dirname(resolved) or ".", exist_ok=True)
        with open(resolved, "w", encoding="utf-8") as f:
            f.write(content)
        return ToolResult(ok=True, output=f"File written: {resolved}")
    except Exception as e:
        return ToolResult(ok=False, output=_error_text(e))


async def _edit_file(input: dict, context: ToolContext) -> ToolResult:
    path = input.get("path", "")
    old_string = input.get("old_string", "")
    new_string = input.get("new_string", "")
    replace_all = input.get("replace_all", False)
    workspace = context.get("workspace", ".")
    resolved = _resolve_path(path, workspace)
    try:
        with open(resolved, "r", encoding="utf-8") as f:
            original = f.read()
    except FileNotFoundError:
        return ToolResult(ok=False, output=f"File not found: {resolved}")

    if replace_all:
        count = original.count(old_string)
        if count == 0:
            return ToolResult(ok=False, output=f"String not found in file: {path}")
        modified = original.replace(old_string, new_string)
    else:
        count = original.count(old_string)
        if count == 0:
            return ToolResult(ok=False, output=f"String not found in file: {path}")
        if count > 1:
            return ToolResult(ok=False, output=f"String found {count} times in file. Provide more context or use replace_all=true.")
        modified = original.replace(old_string, new_string, 1)

    try:
        with open(resolved, "w", encoding="utf-8") as f:
            f.write(modified)
        return ToolResult(ok=True, output=f"File edited: {resolved} ({count} occurrence(s) replaced)")
    except Exception as e:
        return ToolResult(ok=False, output=_error_text(e))


async def _grep_files(input: dict, context: ToolContext) -> ToolResult:
    pattern = input.get("pattern", "")
    path = input.get("path", ".")
    workspace = context.get("workspace", ".")
    resolved = _resolve_path(path, workspace)
    try:
        if os.path.isfile(resolved):
            files = [resolved]
        else:
            files = []
            for root, _, filenames in os.walk(resolved):
                for fname in filenames:
                    files.append(os.path.join(root, fname))
        results: list[str] = []
        for fpath in files:
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    for i, line in enumerate(f, 1):
                        if re.search(pattern, line):
                            results.append(f"{fpath}:{i}: {line.rstrip()}")
            except Exception:
                pass
        if not results:
            return ToolResult(ok=True, output=f"No matches for '{pattern}' in {resolved}")
        return ToolResult(ok=True, output="\n".join(results[:200]))
    except Exception as e:
        return ToolResult(ok=False, output=_error_text(e))


def _terminate_process_tree(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    if os.name == "nt":
        result = subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if result.returncode != 0:
            proc.kill()
    else:
        proc.terminate()
    transport = getattr(proc, "_transport", None)
    if transport:
        transport.close()


async def _run_command(input: dict, context: ToolContext) -> ToolResult:
    command = input.get("command", "")
    cwd = input.get("cwd") or context.get("workspace", ".")
    timeout = input.get("timeout", 60)
    try:
        if os.name == "nt":
            args = powershell_args(command)
            if not args:
                return ToolResult(ok=False, output="PowerShell is not available")
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=cwd,
            )
        else:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=cwd,
            )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            _terminate_process_tree(proc)
            return ToolResult(ok=False, output=f"Command timed out after {timeout}s")
        except asyncio.CancelledError:
            _terminate_process_tree(proc)
            raise
        output = stdout.decode("utf-8", errors="replace")
        if stderr:
            output += "\n[stderr]\n" + stderr.decode("utf-8", errors="replace")
        return ToolResult(ok=proc.returncode == 0, output=output.strip() or "(no output)")
    except Exception as e:
        return ToolResult(ok=False, output=_error_text(e))


async def _ask_user(input: dict, context: ToolContext) -> ToolResult:
    question = input.get("question", "")
    return ToolResult(ok=True, output=f"[ASK_USER] {question}")


async def _load_skill(input: dict, context: ToolContext) -> ToolResult:
    name = str(input.get("name", "")).strip()
    if not name:
        return ToolResult(ok=False, output="Skill name is required.")
    skill = get_default_skill_registry().get(name)
    if skill is None:
        available = ", ".join(get_default_skill_registry().names()) or "none"
        return ToolResult(ok=False, output=f"Skill not found: {name}. Available skills: {available}")
    source = f"\nSource: {skill.source}" if skill.source else ""
    return ToolResult(
        ok=True,
        output=f"<skill name=\"{skill.name}\">\n{skill.prompt}\n</skill>{source}",
    )


def _valid_todo_item(item: Any) -> bool:
    return (
        isinstance(item, dict)
        and isinstance(item.get("content"), str)
        and item.get("content").strip() != ""
        and item.get("status") in ("pending", "in_progress", "completed")
        and isinstance(item.get("activeForm"), str)
        and item.get("activeForm").strip() != ""
    )


async def _todo_write(input: dict, context: ToolContext) -> ToolResult:
    todos = input.get("todos", [])
    if not isinstance(todos, list):
        return ToolResult(ok=False, output="todos must be a list.")
    invalid = [index for index, item in enumerate(todos) if not _valid_todo_item(item)]
    if invalid:
        return ToolResult(ok=False, output=f"Invalid todo item at index {invalid[0]}.")
    return ToolResult(
        ok=True,
        output="Todos have been modified successfully. Continue to use the todo list to track progress.",
    )


async def _web_fetch(input: dict, context: ToolContext) -> ToolResult:
    url = input.get("url", "")
    try:
        import httpx
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, follow_redirects=True)
            return ToolResult(ok=True, output=resp.text[:10000])
    except Exception as e:
        return ToolResult(ok=False, output=_error_text(e))


async def _web_search(input: dict, context: ToolContext) -> ToolResult:
    query = input.get("query", "")
    return ToolResult(ok=True, output=f"[WEB_SEARCH] Query: {query}\nResults would be returned here.")


def build_builtin_registry() -> ToolRegistry:
    tools = [
        ToolDefinition(
            name="list_files",
            description="List files and directories in a given path.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "The path to list."},
                },
                "required": ["path"],
            },
            run=_list_files,
        ),
        ToolDefinition(
            name="read_file",
            description="Read a file from the local filesystem.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "The file path to read."},
                    "offset": {"type": "integer", "description": "Line offset to start reading from."},
                    "limit": {"type": "integer", "description": "Maximum number of lines to read."},
                },
                "required": ["path"],
            },
            run=_read_file,
        ),
        ToolDefinition(
            name="write_file",
            description="Write content to a file.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "The file path to write."},
                    "content": {"type": "string", "description": "The content to write."},
                },
                "required": ["path", "content"],
            },
            run=_write_file,
        ),
        ToolDefinition(
            name="edit_file",
            description="Edit a file with exact string replacement.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "The file path to edit."},
                    "old_string": {"type": "string", "description": "The text to find."},
                    "new_string": {"type": "string", "description": "The replacement text."},
                    "replace_all": {"type": "boolean", "description": "Replace all occurrences."},
                },
                "required": ["path", "old_string", "new_string"],
            },
            run=_edit_file,
        ),
        ToolDefinition(
            name="grep_files",
            description="Search for a pattern in files.",
            input_schema={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "The regex pattern to search for."},
                    "path": {"type": "string", "description": "The directory or file to search in."},
                },
                "required": ["pattern"],
            },
            run=_grep_files,
        ),
        ToolDefinition(
            name="run_command",
            description="Run a shell command.",
            input_schema={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The command to run."},
                    "cwd": {"type": "string", "description": "The working directory."},
                    "timeout": {"type": "integer", "description": "Timeout in seconds."},
                },
                "required": ["command"],
            },
            run=_run_command,
        ),
        ToolDefinition(
            name="ask_user",
            description="Ask the user a question.",
            input_schema={
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "The question to ask."},
                },
                "required": ["question"],
            },
            run=_ask_user,
        ),
        ToolDefinition(
            name="load_skill",
            description="Load the full prompt for an available skill by name before following that skill.",
            input_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "The skill name from the available_skills list."},
                },
                "required": ["name"],
            },
            run=_load_skill,
        ),
        ToolDefinition(
            name="TodoWrite",
            description=(
                "Update the todo list for the current session. Use proactively for complex multi-step work, "
                "when the user explicitly asks for a todo list, after receiving multiple instructions, before "
                "starting work by marking one item in_progress, and after completing each task. Skip it for "
                "single trivial or purely informational requests. Provide each item with content, status, and activeForm."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "todos": {
                        "type": "array",
                        "description": "The complete updated todo list.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "content": {
                                    "type": "string",
                                    "description": "Imperative task description, e.g. Run tests.",
                                },
                                "status": {
                                    "type": "string",
                                    "enum": ["pending", "in_progress", "completed"],
                                    "description": "Current task status.",
                                },
                                "activeForm": {
                                    "type": "string",
                                    "description": "Present-continuous form, e.g. Running tests.",
                                },
                            },
                            "required": ["content", "status", "activeForm"],
                        },
                    },
                },
                "required": ["todos"],
            },
            run=_todo_write,
        ),
        ToolDefinition(
            name="web_fetch",
            description="Fetch content from a URL.",
            input_schema={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The URL to fetch."},
                },
                "required": ["url"],
            },
            run=_web_fetch,
        ),
        ToolDefinition(
            name="web_search",
            description="Search the web.",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query."},
                },
                "required": ["query"],
            },
            run=_web_search,
        ),
    ]
    return ToolRegistry(
        tools=tools,
        metadata=ToolRegistryMetadata(source="builtin", label="Built-in Tools"),
    )
