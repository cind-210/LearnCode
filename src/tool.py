"""
Tool registry and execution framework.

Mirrors src/tool.ts from the TypeScript version.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol

from .types import ChatMessage


# ---------------------------------------------------------------------------
# Tool execution types
# ---------------------------------------------------------------------------


ToolContext = dict[str, Any]


@dataclass
class BackgroundTaskResult:
    task_id: str
    type: str
    command: str
    pid: int
    status: str
    started_at: int


@dataclass
class ToolResult:
    ok: bool
    output: str
    background_task: Optional[BackgroundTaskResult] = None
    await_user: bool = False


# ---------------------------------------------------------------------------
# Tool definition
# ---------------------------------------------------------------------------


@dataclass
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]
    run: Callable[..., Any]  # async (input, context) -> ToolResult


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------


@dataclass
class SkillSummary:
    name: str
    description: str
    path: str
    source: str


@dataclass
class McpServerSummary:
    name: str
    command: str
    status: str
    tool_count: int = 0
    error: Optional[str] = None
    protocol: Optional[str] = None
    resource_count: Optional[int] = None
    prompt_count: Optional[int] = None


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------


@dataclass
class ToolRegistryMetadata:
    source: str = ""
    label: str = ""
    skills: list[SkillSummary] = field(default_factory=list)
    mcp_servers: list[McpServerSummary] = field(default_factory=list)


class ToolRegistry:
    def __init__(
        self,
        tools: list[ToolDefinition],
        metadata: Optional[ToolRegistryMetadata] = None,
        disposer: Optional[Callable[[], Any]] = None,
    ):
        self._tools: list[ToolDefinition] = list(tools)
        self._metadata = metadata or ToolRegistryMetadata()
        self._disposers: list[Callable[[], Any]] = []
        if disposer:
            self._disposers.append(disposer)

    def list(self) -> list[ToolDefinition]:
        return self._tools

    def find(self, name: str) -> Optional[ToolDefinition]:
        for tool in self._tools:
            if tool.name == name:
                return tool
        return None

    def get_skills(self) -> list[SkillSummary]:
        return self._metadata.skills

    def get_mcp_servers(self) -> list[McpServerSummary]:
        return self._metadata.mcp_servers

    def set_mcp_servers(self, servers: list[McpServerSummary]) -> None:
        self._metadata.mcp_servers = list(servers)

    def add_tools(self, next_tools: list[ToolDefinition]) -> None:
        existing = {t.name for t in self._tools}
        for tool in next_tools:
            if tool.name not in existing:
                self._tools.append(tool)
                existing.add(tool.name)

    def merge(self, other: ToolRegistry) -> ToolRegistry:
        merged = ToolRegistry(
            tools=list(self._tools),
            metadata=ToolRegistryMetadata(
                source=f"{self._metadata.source}+{other._metadata.source}",
                label=f"{self._metadata.label} + {other._metadata.label}",
                skills=list(self._metadata.skills) + list(other._metadata.skills),
                mcp_servers=list(self._metadata.mcp_servers) + list(other._metadata.mcp_servers),
            ),
        )
        merged.add_tools(other._tools)
        for disp in other._disposers:
            merged.add_disposer(disp)
        return merged

    def add_disposer(self, disposer: Callable[[], Any]) -> None:
        self._disposers.append(disposer)

    async def execute(self, tool_name: str, input: Any, context: ToolContext) -> ToolResult:
        tool = self.find(tool_name)
        if not tool:
            return ToolResult(ok=False, output=f"Unknown tool: {tool_name}")

        try:
            return await tool.run(input, context)
        except Exception as e:
            return ToolResult(ok=False, output=str(e))

    async def dispose(self) -> None:
        for disposer in self._disposers:
            try:
                result = disposer()
                if hasattr(result, "__await__"):
                    await result
            except Exception:
                pass