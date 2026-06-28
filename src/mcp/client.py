"""
MCP (Model Context Protocol) server integration.

Mirrors src/mcp.ts from the TypeScript version.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
from dataclasses import dataclass, field
from typing import Any, Optional

from ..config.runtime import McpServerConfig
from ..tools.registry import ToolDefinition, ToolRegistry, ToolRegistryMetadata, ToolResult


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
        result = await self._send_request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "minicode-py", "version": "1.0.0"},
        })
        await self._send_request("notifications/initialized", {})

    async def list_tools(self) -> list[dict]:
        result = await self._send_request("tools/list")
        return result.get("tools", [])

    async def call_tool(self, name: str, arguments: dict) -> dict:
        result = await self._send_request("tools/call", {"name": name, "arguments": arguments})
        return result

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


async def start_mcp_server(config: McpServerConfig, server_id: str) -> Optional[McpConnection]:
    try:
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
            return None

        conn = McpConnection(
            server_id=server_id,
            proc=proc,
            _reader=proc.stdout,
            _writer=proc.stdin,
        )
        asyncio.create_task(conn._read_loop())
        await conn.initialize()
        return conn
    except Exception:
        return None


def _build_mcp_tool_definition(mcp_tool: dict, conn: McpConnection) -> ToolDefinition:
    async def _run(input: Any, context: Any) -> ToolResult:
        try:
            result = await conn.call_tool(mcp_tool["name"], input if isinstance(input, dict) else {})
            content = result.get("content", [])
            text_parts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
            return ToolResult(ok=True, output="\n".join(text_parts))
        except Exception as e:
            return ToolResult(ok=False, output=str(e))

    return ToolDefinition(
        name=mcp_tool["name"],
        description=mcp_tool.get("description", ""),
        input_schema=mcp_tool.get("inputSchema", {}),
        run=_run,
    )


async def build_mcp_registry(
    servers: dict[str, McpServerConfig],
) -> tuple[Optional[ToolRegistry], list[McpConnection]]:
    if not servers:
        return None, []

    connections: list[McpConnection] = []
    all_tools: list[ToolDefinition] = []

    for server_id, server_config in servers.items():
        conn = await start_mcp_server(server_config, server_id)
        if conn is None:
            continue
        connections.append(conn)
        try:
            mcp_tools = await conn.list_tools()
            for mcp_tool in mcp_tools:
                tool_def = _build_mcp_tool_definition(mcp_tool, conn)
                all_tools.append(tool_def)
        except Exception:
            pass

    if not all_tools:
        for conn in connections:
            await conn.close()
        return None, []

    async def _disposer() -> None:
        for conn in connections:
            await conn.close()

    registry = ToolRegistry(
        tools=all_tools,
        metadata=ToolRegistryMetadata(source="mcp_servers", label="MCP Tools"),
        disposer=_disposer,
    )
    return registry, connections