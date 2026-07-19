"""Session template data model and loader."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..config.runtime import LEARN_CODE_AGENTS_DIR, McpServerConfig
from ..tools.permissions import PermissionMode


PROJECT_TEMPLATES_DIR = ".learncode/agents"


@dataclass
class SessionTemplate:
    name: str
    description: str = ""
    prompt: str = ""
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
    def from_dict(cls, data: dict[str, Any]) -> SessionTemplate:
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
            prompt=str(data.get("prompt") or ""),
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


GENERAL_PURPOSE_TEMPLATE = SessionTemplate(
    name="general-purpose",
    description=(
        "General-purpose template for researching complex questions, searching code, "
        "and executing multi-step tasks."
    ),
    prompt=(
        "You are a focused child session for LearnCode. Complete the delegated task fully, "
        "using the available tools when helpful. You start with only the task prompt, "
        "not the parent conversation, so rely on the provided instructions and inspect "
        "the workspace as needed. When done, respond with a concise report covering "
        "what you did and any key findings."
    ),
    tools=["*"],
)


def load_session_templates(workspace: str) -> list[SessionTemplate]:
    templates = [GENERAL_PURPOSE_TEMPLATE]
    templates.extend(_load_templates_dir(LEARN_CODE_AGENTS_DIR))
    templates.extend(_load_templates_dir(Path(workspace) / PROJECT_TEMPLATES_DIR))
    return _dedupe_by_name(templates)


def find_session_template(templates: list[SessionTemplate], name: str | None) -> SessionTemplate | None:
    target = name or GENERAL_PURPOSE_TEMPLATE.name
    for template in templates:
        if template.name == target:
            return template
    return None


def _dedupe_by_name(templates: list[SessionTemplate]) -> list[SessionTemplate]:
    by_name: dict[str, SessionTemplate] = {}
    for template in templates:
        if template.name:
            by_name[template.name] = template
    return list(by_name.values())


def _load_templates_dir(path: Path) -> list[SessionTemplate]:
    if not path.is_dir():
        return []
    return [
        _load_template_file(file_path)
        for file_path in sorted(path.glob("*.md"))
    ]


def _load_template_file(path: Path) -> SessionTemplate:
    content = path.read_text(encoding="utf-8")
    frontmatter, body = _split_frontmatter(content)
    data = _parse_frontmatter(frontmatter)
    name = str(data.get("name") or data.get("agentType") or path.stem)
    description = str(data.get("description") or data.get("whenToUse") or "")
    return SessionTemplate(
        name=name,
        description=description,
        prompt=str(data.get("prompt") or body.strip()),
        tools=_string_list_or_none(data.get("tools")),
        disallowed_tools=_string_list(data.get("disallowedTools")),
        skills=_string_list_or_none(data.get("skills")),
        model=str(data["model"]) if data.get("model") is not None else None,
        permission_mode=PermissionMode(data["permissionMode"]) if data.get("permissionMode") else None,
        max_turns=int(data["maxTurns"]) if data.get("maxTurns") is not None else None,
        background=bool(data.get("background", False)),
        isolation=str(data["isolation"]) if data.get("isolation") is not None else None,
    )


def _split_frontmatter(content: str) -> tuple[str, str]:
    if not content.startswith("---\n"):
        return "", content
    end = content.find("\n---", 4)
    if end == -1:
        return "", content
    return content[4:end], content[end + 4:].lstrip("\r\n")


def _parse_frontmatter(text: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    current_key = ""
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue
        if line.startswith("  - ") and current_key:
            existing = result.setdefault(current_key, [])
            if isinstance(existing, list):
                existing.append(_parse_scalar(line[4:].strip()))
            continue
        if ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        current_key = key.strip()
        value = raw_value.strip()
        result[current_key] = [] if value == "" else _parse_scalar(value)
    return result


def _parse_scalar(value: str) -> Any:
    if value in ("true", "True"):
        return True
    if value in ("false", "False"):
        return False
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_strip_quotes(part.strip()) for part in inner.split(",")]
    if value.isdigit():
        return int(value)
    return _strip_quotes(value)


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str)]


def _string_list_or_none(value: Any) -> list[str] | None:
    items = _string_list(value)
    return items if items else None
