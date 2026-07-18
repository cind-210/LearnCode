"""
MCP (Model Context Protocol) server integration.

Mirrors src/mcp.ts from the TypeScript version.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any, Optional

from ..config.runtime import McpServerConfig
from ..tools.registry import McpServerSummary, ToolDefinition, ToolRegistry, ToolRegistryMetadata, ToolResult


def _sanitize_tool_name_part(value: str) -> str:
    sanitized = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in value)
    return sanitized.strip("_") or "tool"


def _error_text(error: BaseException) -> str:
    return str(error) or repr(error) or error.__class__.__name__


def _server_label(config: McpServerConfig) -> str:
    if config.url:
        return config.url
    return " ".join([config.command, *(config.args or [])]).strip()


def _format_mcp_content(result: dict) -> str:
    content = result.get("content", [])
    if not isinstance(content, list):
        return ""

    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        content_type = item.get("type")
        if content_type == "text":
            parts.append(str(item.get("text", "")))
        else:
            parts.append(f"[non-text MCP content omitted: {content_type or 'unknown'}]")
    return "\n".join(part for part in parts if part)


def _format_resource_list(server_id: str, resources: list[dict]) -> str:
    if not resources:
        return f"[{server_id}] no resources"
    lines: list[str] = []
    for resource in resources:
        uri = resource.get("uri", "")
        name = resource.get("name", "")
        mime = resource.get("mimeType", "")
        description = resource.get("description", "")
        label = f"{name} " if name else ""
        meta = f" ({mime})" if mime else ""
        lines.append(f"[{server_id}] {label}{uri}{meta}".strip())
        if description:
            lines.append(f"  {description}")
    return "\n".join(lines)


def _format_resource_contents(result: dict) -> str:
    contents = result.get("contents", [])
    if not isinstance(contents, list):
        return ""
    parts: list[str] = []
    for item in contents:
        if not isinstance(item, dict):
            continue
        uri = item.get("uri", "")
        mime = item.get("mimeType", "")
        header = f"<resource uri=\"{uri}\" mimeType=\"{mime}\">".strip()
        if "text" in item:
            parts.append(f"{header}\n{item.get('text', '')}\n</resource>")
        elif "blob" in item:
            parts.append(f"{header}\n[binary MCP resource content omitted]\n</resource>")
    return "\n\n".join(parts)


def _format_prompt_list(server_id: str, prompts: list[dict]) -> str:
    if not prompts:
        return f"[{server_id}] no prompts"
    lines: list[str] = []
    for prompt in prompts:
        name = prompt.get("name", "")
        description = prompt.get("description", "")
        arguments = prompt.get("arguments", [])
        arg_names = [
            str(arg.get("name", ""))
            for arg in arguments
            if isinstance(arg, dict) and arg.get("name")
        ] if isinstance(arguments, list) else []
        suffix = f"({', '.join(arg_names)})" if arg_names else ""
        lines.append(f"[{server_id}] {name}{suffix}")
        if description:
            lines.append(f"  {description}")
    return "\n".join(lines)


def _format_prompt_messages(result: dict) -> str:
    messages = result.get("messages", [])
    if not isinstance(messages, list):
        return ""
    parts: list[str] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = message.get("role", "user")
        content = message.get("content", "")
        if isinstance(content, dict):
            if content.get("type") == "text":
                text = content.get("text", "")
            else:
                text = f"[non-text MCP prompt content omitted: {content.get('type', 'unknown')}]"
        else:
            text = str(content)
        parts.append(f"[{role}]\n{text}")
    return "\n\n".join(parts)


def _parse_sse_response(text: str) -> dict:
    data_lines: list[str] = []
    for line in text.splitlines():
        if line.startswith("data:"):
            data_lines.append(line[len("data:"):].strip())
        elif line == "" and data_lines:
            break
    if not data_lines:
        return {}
    return json.loads("\n".join(data_lines))


@dataclass
class McpConnection:
    server_id: str
    proc: Any
    _reader: Any
    _writer: Any
    _id_counter: int = 0
    _pending: dict[int, asyncio.Future] = field(default_factory=dict)

    async def _send_request(self, method: str, params: Optional[dict] = None) -> Any:
        req_id = self._id_counter
        self._id_counter += 1
        msg = json.dumps({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}})
        self._writer.write(msg.encode("utf-8"))
        self._writer.write(b"\n")
        await self._writer.drain()

        future = asyncio.get_event_loop().create_future()
        self._pending[req_id] = future
        try:
            result = await asyncio.wait_for(future, timeout=30)
            return result
        finally:
            self._pending.pop(req_id, None)

    async def _send_notification(self, method: str, params: Optional[dict] = None) -> None:
        msg = json.dumps({"jsonrpc": "2.0", "method": method, "params": params or {}})
        self._writer.write(msg.encode("utf-8"))
        self._writer.write(b"\n")
        await self._writer.drain()

    async def _read_loop(self) -> None:
        try:
            while True:
                line = await self._reader.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line.decode("utf-8"))
                    if "id" in msg and msg["id"] is not None:
                        future = self._pending.get(msg["id"])
                        if future and not future.done():
                            if "error" in msg:
                                future.set_exception(RuntimeError(msg["error"].get("message", str(msg["error"]))))
                            else:
                                future.set_result(msg.get("result"))
                except json.JSONDecodeError:
                    pass
        except Exception:
            pass

    async def initialize(self) -> None:
        await self._send_request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "learncode", "version": "1.0.0"},
        })
        await self._send_notification("notifications/initialized", {})

    async def list_tools(self) -> list[dict]:
        tools: list[dict] = []
        cursor = None
        while True:
            params = {"cursor": cursor} if cursor else {}
            result = await self._send_request("tools/list", params)
            tools.extend(result.get("tools", []))
            cursor = result.get("nextCursor")
            if not cursor:
                return tools

    async def call_tool(self, name: str, arguments: dict) -> dict:
        result = await self._send_request("tools/call", {"name": name, "arguments": arguments})
        return result

    async def list_resources(self) -> list[dict]:
        resources: list[dict] = []
        cursor = None
        while True:
            params = {"cursor": cursor} if cursor else {}
            result = await self._send_request("resources/list", params)
            resources.extend(result.get("resources", []))
            cursor = result.get("nextCursor")
            if not cursor:
                return resources

    async def read_resource(self, uri: str) -> dict:
        return await self._send_request("resources/read", {"uri": uri})

    async def list_prompts(self) -> list[dict]:
        prompts: list[dict] = []
        cursor = None
        while True:
            params = {"cursor": cursor} if cursor else {}
            result = await self._send_request("prompts/list", params)
            prompts.extend(result.get("prompts", []))
            cursor = result.get("nextCursor")
            if not cursor:
                return prompts

    async def get_prompt(self, name: str, arguments: dict) -> dict:
        return await self._send_request("prompts/get", {"name": name, "arguments": arguments})

    async def close(self) -> None:
        try:
            self._writer.close()
            await self._writer.wait_closed()
        except Exception:
            pass
        try:
            self.proc.kill()
        except Exception:
            pass


@dataclass
class HttpMcpConnection:
    server_id: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    session_id: Optional[str] = None
    _id_counter: int = 0
    _client: Any = None

    async def _ensure_client(self) -> Any:
        if self._client is None:
            import httpx
            self._client = httpx.AsyncClient(timeout=30)
        return self._client

    async def _post_json(self, message: dict) -> dict:
        client = await self._ensure_client()
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            **self.headers,
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        response = await client.post(self.url, json=message, headers=headers)
        response.raise_for_status()
        session_id = response.headers.get("mcp-session-id")
        if session_id:
            self.session_id = session_id
        if not response.content:
            return {}
        content_type = response.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            data = _parse_sse_response(response.text)
        else:
            data = response.json()
        if "error" in data:
            error = data["error"]
            raise RuntimeError(error.get("message", str(error)) if isinstance(error, dict) else str(error))
        return data.get("result", data)

    async def _send_request(self, method: str, params: Optional[dict] = None) -> Any:
        req_id = self._id_counter
        self._id_counter += 1
        return await self._post_json({
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params or {},
        })

    async def _send_notification(self, method: str, params: Optional[dict] = None) -> None:
        await self._post_json({"jsonrpc": "2.0", "method": method, "params": params or {}})

    async def initialize(self) -> None:
        await self._send_request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "learncode", "version": "1.0.0"},
        })
        await self._send_notification("notifications/initialized", {})

    async def list_tools(self) -> list[dict]:
        tools: list[dict] = []
        cursor = None
        while True:
            params = {"cursor": cursor} if cursor else {}
            result = await self._send_request("tools/list", params)
            tools.extend(result.get("tools", []))
            cursor = result.get("nextCursor")
            if not cursor:
                return tools

    async def call_tool(self, name: str, arguments: dict) -> dict:
        return await self._send_request("tools/call", {"name": name, "arguments": arguments})

    async def list_resources(self) -> list[dict]:
        resources: list[dict] = []
        cursor = None
        while True:
            params = {"cursor": cursor} if cursor else {}
            result = await self._send_request("resources/list", params)
            resources.extend(result.get("resources", []))
            cursor = result.get("nextCursor")
            if not cursor:
                return resources

    async def read_resource(self, uri: str) -> dict:
        return await self._send_request("resources/read", {"uri": uri})

    async def list_prompts(self) -> list[dict]:
        prompts: list[dict] = []
        cursor = None
        while True:
            params = {"cursor": cursor} if cursor else {}
            result = await self._send_request("prompts/list", params)
            prompts.extend(result.get("prompts", []))
            cursor = result.get("nextCursor")
            if not cursor:
                return prompts

    async def get_prompt(self, name: str, arguments: dict) -> dict:
        return await self._send_request("prompts/get", {"name": name, "arguments": arguments})

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()


async def start_mcp_server(config: McpServerConfig, server_id: str) -> McpConnection | HttpMcpConnection:
    if config.url:
        protocol = (config.protocol or "streamable-http").lower()
        if protocol not in ("streamable-http", "http"):
            raise RuntimeError(f"MCP server '{server_id}' uses unsupported remote protocol: {protocol}")
        conn = HttpMcpConnection(
            server_id=server_id,
            url=config.url,
            headers={k: str(v) for k, v in (config.headers or {}).items()},
        )
        await conn.initialize()
        return conn

    if not config.command:
        raise RuntimeError(f"MCP server '{server_id}' has no command or url configured")

    proc = await asyncio.create_subprocess_exec(
        config.command,
        *(config.args or []),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=config.cwd or None,
        env={**os.environ, **{k: str(v) for k, v in (config.env or {}).items()}} if config.env else None,
    )
    if proc.stdin is None or proc.stdout is None:
        if proc.stdin:
            proc.stdin.close()
        proc.kill()
        raise RuntimeError(f"MCP server '{server_id}' did not expose stdio pipes")

    conn = McpConnection(
        server_id=server_id,
        proc=proc,
        _reader=proc.stdout,
        _writer=proc.stdin,
    )
    asyncio.create_task(conn._read_loop())
    await conn.initialize()
    return conn


def _build_mcp_tool_definition(mcp_tool: dict, conn: McpConnection | HttpMcpConnection) -> ToolDefinition:
    original_name = str(mcp_tool["name"])

    async def _run(input: Any, context: Any) -> ToolResult:
        try:
            result = await conn.call_tool(original_name, input if isinstance(input, dict) else {})
            output = _format_mcp_content(result)
            if result.get("isError"):
                return ToolResult(ok=False, output=output or "MCP tool returned isError=true")
            return ToolResult(ok=True, output=output)
        except Exception as e:
            return ToolResult(ok=False, output=_error_text(e))

    wrapped_name = f"mcp__{_sanitize_tool_name_part(conn.server_id)}__{_sanitize_tool_name_part(original_name)}"
    return ToolDefinition(
        name=wrapped_name,
        description=f"[MCP:{conn.server_id}] {mcp_tool.get('description', '')}".strip(),
        input_schema=mcp_tool.get("inputSchema", {}),
        run=_run,
    )


def _connection_by_server(
    connections: list[McpConnection | HttpMcpConnection],
    server_id: str,
) -> McpConnection | HttpMcpConnection:
    for conn in connections:
        if conn.server_id == server_id:
            return conn
    available = ", ".join(conn.server_id for conn in connections) or "none"
    raise RuntimeError(f"MCP server not found: {server_id}. Available servers: {available}")


def _build_mcp_helper_tools(connections: list[McpConnection | HttpMcpConnection]) -> list[ToolDefinition]:
    async def _list_resources(input: Any, context: Any) -> ToolResult:
        server = str((input or {}).get("server", "")).strip() if isinstance(input, dict) else ""
        targets = [_connection_by_server(connections, server)] if server else list(connections)
        parts: list[str] = []
        for conn in targets:
            resources = await conn.list_resources()
            parts.append(_format_resource_list(conn.server_id, resources))
        return ToolResult(ok=True, output="\n\n".join(parts))

    async def _read_resource(input: Any, context: Any) -> ToolResult:
        data = input if isinstance(input, dict) else {}
        server = str(data.get("server", "")).strip()
        uri = str(data.get("uri", "")).strip()
        if not server:
            return ToolResult(ok=False, output="server is required.")
        if not uri:
            return ToolResult(ok=False, output="uri is required.")
        conn = _connection_by_server(connections, server)
        result = await conn.read_resource(uri)
        return ToolResult(ok=True, output=_format_resource_contents(result))

    async def _list_prompts(input: Any, context: Any) -> ToolResult:
        server = str((input or {}).get("server", "")).strip() if isinstance(input, dict) else ""
        targets = [_connection_by_server(connections, server)] if server else list(connections)
        parts: list[str] = []
        for conn in targets:
            prompts = await conn.list_prompts()
            parts.append(_format_prompt_list(conn.server_id, prompts))
        return ToolResult(ok=True, output="\n\n".join(parts))

    async def _get_prompt(input: Any, context: Any) -> ToolResult:
        data = input if isinstance(input, dict) else {}
        server = str(data.get("server", "")).strip()
        name = str(data.get("name", "")).strip()
        arguments = data.get("arguments", {})
        if not server:
            return ToolResult(ok=False, output="server is required.")
        if not name:
            return ToolResult(ok=False, output="name is required.")
        if not isinstance(arguments, dict):
            return ToolResult(ok=False, output="arguments must be an object.")
        conn = _connection_by_server(connections, server)
        result = await conn.get_prompt(name, arguments)
        return ToolResult(ok=True, output=_format_prompt_messages(result))

    return [
        ToolDefinition(
            name="ListMcpResourcesTool",
            description="List resources exposed by connected MCP servers. Use before ReadMcpResourceTool.",
            input_schema={
                "type": "object",
                "properties": {
                    "server": {"type": "string", "description": "Optional MCP server id. Omit to list all connected servers."},
                },
            },
            run=_list_resources,
        ),
        ToolDefinition(
            name="ReadMcpResourceTool",
            description="Read a resource exposed by a connected MCP server.",
            input_schema={
                "type": "object",
                "properties": {
                    "server": {"type": "string", "description": "MCP server id from ListMcpResourcesTool."},
                    "uri": {"type": "string", "description": "Resource URI to read."},
                },
                "required": ["server", "uri"],
            },
            run=_read_resource,
        ),
        ToolDefinition(
            name="ListMcpPromptsTool",
            description="List prompt templates exposed by connected MCP servers. Use before GetMcpPromptTool.",
            input_schema={
                "type": "object",
                "properties": {
                    "server": {"type": "string", "description": "Optional MCP server id. Omit to list all connected servers."},
                },
            },
            run=_list_prompts,
        ),
        ToolDefinition(
            name="GetMcpPromptTool",
            description="Get a prompt template from a connected MCP server with optional arguments.",
            input_schema={
                "type": "object",
                "properties": {
                    "server": {"type": "string", "description": "MCP server id from ListMcpPromptsTool."},
                    "name": {"type": "string", "description": "Prompt name."},
                    "arguments": {"type": "object", "description": "Prompt arguments object.", "additionalProperties": True},
                },
                "required": ["server", "name"],
            },
            run=_get_prompt,
        ),
    ]


async def build_mcp_registry(
    servers: dict[str, McpServerConfig],
) -> tuple[Optional[ToolRegistry], list[McpConnection | HttpMcpConnection]]:
    if not servers:
        return None, []

    connections: list[McpConnection | HttpMcpConnection] = []
    all_tools: list[ToolDefinition] = []
    summaries: list[McpServerSummary] = []

    for server_id, server_config in servers.items():
        if not server_config.enabled:
            summaries.append(McpServerSummary(
                name=server_id,
                command=_server_label(server_config),
                status="disabled",
                protocol=server_config.protocol,
            ))
            continue
        try:
            conn = await start_mcp_server(server_config, server_id)
            connections.append(conn)
            mcp_tools = await conn.list_tools()
            for mcp_tool in mcp_tools:
                tool_def = _build_mcp_tool_definition(mcp_tool, conn)
                all_tools.append(tool_def)
            summaries.append(McpServerSummary(
                name=server_id,
                command=_server_label(server_config),
                status="connected",
                tool_count=len(mcp_tools),
                protocol=server_config.protocol or ("streamable-http" if server_config.url else "stdio"),
            ))
        except Exception as e:
            message = _error_text(e)
            print(f"[mcp:{server_id}] {message}", file=sys.stderr)
            summaries.append(McpServerSummary(
                name=server_id,
                command=_server_label(server_config),
                status="error",
                error=message,
                protocol=server_config.protocol or ("streamable-http" if server_config.url else "stdio"),
            ))

    if connections:
        all_tools.extend(_build_mcp_helper_tools(connections))

    if not all_tools and not summaries:
        for conn in connections:
            await conn.close()
        return None, []

    async def _disposer() -> None:
        for conn in connections:
            await conn.close()

    registry = ToolRegistry(
        tools=all_tools,
        metadata=ToolRegistryMetadata(source="mcp_servers", label="MCP Tools", mcp_servers=summaries),
        disposer=_disposer,
    )
    return registry, connections
