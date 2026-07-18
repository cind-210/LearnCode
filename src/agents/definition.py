"""Agent definition data model.

This is intentionally only the static definition layer. Agent execution and the
Agent tool are separate concerns that can be added on top of this shape.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from ..config.runtime import McpServerConfig
from ..tools.permissions import PermissionMode


@dataclass
class AgentDefinition:
    name: str
    description: str = ""
    tools: Optional[list[str]] = None
    disallowed_tools: list[str] = field(default_factory=list)
    skills: Optional[list[str]] = None
    mcp_servers: dict[str, McpServerConfig] = field(default_factory=dict)
    model: Optional[str] = None
    permission_mode: Optional[PermissionMode] = None
    max_turns: Optional[int] = None
    background: bool = False
    isolation: Optional[str] = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentDefinition:
        raw_mcp = data.get("mcpServers", {})
        mcp_servers = {
            name: McpServerConfig(**cfg)
            for name, cfg in raw_mcp.items()
            if isinstance(name, str) and isinstance(cfg, dict)
        } if isinstance(raw_mcp, dict) else {}
        raw_mode = data.get("permissionMode")
        return cls(
            name=str(data.get("name") or data.get("agentType") or ""),
            description=str(data.get("description") or data.get("whenToUse") or ""),
            tools=data.get("tools") if isinstance(data.get("tools"), list) else None,
            disallowed_tools=data.get("disallowedTools") if isinstance(data.get("disallowedTools"), list) else [],
            skills=data.get("skills") if isinstance(data.get("skills"), list) else None,
            mcp_servers=mcp_servers,
            model=str(data["model"]) if data.get("model") is not None else None,
            permission_mode=PermissionMode(raw_mode) if raw_mode else None,
            max_turns=int(data["maxTurns"]) if data.get("maxTurns") is not None else None,
            background=bool(data.get("background", False)),
            isolation=str(data["isolation"]) if data.get("isolation") is not None else None,
        )
