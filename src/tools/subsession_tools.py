"""Sub session tools."""
from __future__ import annotations

from typing import Any

from ..loop.runner import AgentLoopConfig
from ..sessions.characters import Character, find_character, load_characters
from ..sessions.subsessions import SubSessionRuntime
from ..sessions.store import validate_session_name
from .permissions import PermissionRules
from .registry import ToolContext, ToolDefinition, ToolRegistry, ToolResult


LIST_CHARACTERS_TOOL_NAME = "ListCharacters"
LIST_SUBSESSIONS_TOOL_NAME = "ListSubSessions"
NEW_SUBSESSION_TOOL_NAME = "NewSubSession"
FORK_SUBSESSION_TOOL_NAME = "ForkSubSession"
SEND_MESSAGE_TOOL_NAME = "SendMessage"


def build_subsession_tools(characters: list[Character] | None = None) -> list[ToolDefinition]:
    definitions = characters or []
    return [
        build_list_characters_tool(definitions),
        build_list_subsessions_tool(),
        build_new_subsession_tool(definitions),
        build_fork_subsession_tool(definitions),
        build_send_message_tool(),
    ]


def build_list_characters_tool(characters: list[Character] | None = None) -> ToolDefinition:
    return ToolDefinition(
        name=LIST_CHARACTERS_TOOL_NAME,
        description="List available child-session characters by name and description only.",
        input_schema={"type": "object", "properties": {}},
        run=_run_list_characters_tool,
    )


def build_list_subsessions_tool() -> ToolDefinition:
    return ToolDefinition(
        name=LIST_SUBSESSIONS_TOOL_NAME,
        description="List child sessions owned by the current parent session, including unloaded sessions on disk.",
        input_schema={"type": "object", "properties": {}},
        run=_run_list_subsessions_tool,
    )


def build_new_subsession_tool(characters: list[Character] | None = None) -> ToolDefinition:
    return ToolDefinition(
        name=NEW_SUBSESSION_TOOL_NAME,
        description=_new_subsession_description(characters or []),
        input_schema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Required unique child session name, at most 30 characters.",
                },
                "prompt": {
                    "type": "string",
                    "description": "Optional system-level instruction layered on top of the character prompt. Do not put the task request here.",
                },
                "message": {
                    "type": "string",
                    "description": "Optional first task/request message to send to the child session. If empty, the child session is created idle without running its loop.",
                },
                "character": {
                    "type": "string",
                    "description": "Optional character to use. Omit it to create a plain child session.",
                },
                "permissions": _permissions_schema(),
            },
            "required": ["name"],
        },
        run=_run_new_subsession_tool,
    )


def build_fork_subsession_tool(characters: list[Character] | None = None) -> ToolDefinition:
    return ToolDefinition(
        name=FORK_SUBSESSION_TOOL_NAME,
        description=_fork_subsession_description(characters or []),
        input_schema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Optional name for the forked child session.",
                },
                "description": {
                    "type": "string",
                    "description": "A short 3-5 word description of the forked child session task.",
                },
                "prompt": {
                    "type": "string",
                    "description": "Optional session-specific system prompt addition layered on top of the character prompt.",
                },
                "message": {
                    "type": "string",
                    "description": "Optional first user message to send after forking the parent context. If empty, the forked child session is created idle without running its loop.",
                },
                "character": {
                    "type": "string",
                    "description": "Optional character to use. Omit it to create a plain forked child session.",
                },
                "permissions": _permissions_schema(),
            },
        },
        run=_run_fork_subsession_tool,
    )


def build_send_message_tool() -> ToolDefinition:
    return ToolDefinition(
        name=SEND_MESSAGE_TOOL_NAME,
        description="Send a message to an existing child session and let it continue its loop.",
        input_schema={
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Child session id or name."},
                "message": {"type": "string", "description": "Message to send to the child session."},
            },
            "required": ["to", "message"],
        },
        run=_run_send_message_tool,
    )


async def _run_list_characters_tool(input: dict, context: ToolContext) -> ToolResult:
    characters = _characters(context)
    lines = [
        f"- {character.name}: {character.description or 'No description'}"
        for character in characters
    ]
    return ToolResult(ok=True, output="\n".join(lines) or "No characters available.")


async def _run_list_subsessions_tool(input: dict, context: ToolContext) -> ToolResult:
    runtime = _runtime(context)
    config = _loop_config(context)
    parent_session_id = str(context.get("session_id") or "")
    links = runtime.list_links(config.session_dir, parent_session_id)
    if not links:
        return ToolResult(ok=True, output="No child sessions.")

    loaded_by_id = {
        loaded.link.id: loaded
        for loaded in runtime.list_sessions(parent_session_id)
    }
    lines = []
    for link in links:
        loaded = loaded_by_id.get(link.id)
        status = loaded.status if loaded else "idle-on-disk"
        description = f" - {link.description}" if link.description else ""
        character = link.character or "none"
        lines.append(
            f"- {link.name} ({link.id}) [{status}] character={character} updated_at={link.updated_at}{description}"
        )
    return ToolResult(ok=True, output="\n".join(lines))


async def _run_new_subsession_tool(input: dict, context: ToolContext) -> ToolResult:
    prompt = str(input.get("prompt") or "").strip()
    message = str(input.get("message") or "").strip()

    runtime = _runtime(context)
    registry = _registry(context)
    config = _loop_config(context)
    model_factory = context.get("model_adapter_factory")
    if model_factory is None:
        return ToolResult(ok=False, output="SubSession model factory is unavailable.")

    characters = _characters(context)
    character_name = _optional_str(input.get("character"))
    character = find_character(characters, character_name)
    if character_name and character is None:
        return ToolResult(ok=False, output=f"Character not found. Available characters: {_character_names(characters)}")

    workspace = str(context.get("workspace") or ".")
    sub_registry = _registry_for_subsession(registry, character)
    child_permissions = _child_permissions(config.permissions, character, input.get("permissions"))
    name = _optional_str(input.get("name"))
    if not name:
        return ToolResult(ok=False, output="NewSubSession requires 'name'.")
    name_error = _validate_new_subsession_name(runtime, config, str(context.get("session_id") or ""), name)
    if name_error:
        return ToolResult(ok=False, output=name_error)
    child, output = await runtime.create_session(
        parent_session_id=str(context.get("session_id") or ""),
        name=name,
        description="",
        character=character,
        prompt=prompt,
        message=message,
        workspace=workspace,
        config=config,
        registry=sub_registry,
        model_factory=model_factory,
        permissions=child_permissions,
    )
    return ToolResult(
        ok=True,
        output=(
            f"SubSession created: {child.link.name} ({child.link.id})\n"
            f"Character: {child.link.character or 'none'}\n"
            f"Status: {child.status}\n\n"
            f"{output}"
        ),
    )


async def _run_fork_subsession_tool(input: dict, context: ToolContext) -> ToolResult:
    prompt = str(input.get("prompt") or "").strip()
    message = str(input.get("message") or "").strip()

    source_messages = context.get("fork_source_messages")
    if not isinstance(source_messages, list):
        source_messages = context.get("current_messages")
    if not isinstance(source_messages, list) or not source_messages:
        return ToolResult(ok=False, output="ForkSubSession parent context is unavailable.")

    runtime = _runtime(context)
    registry = _registry(context)
    config = _loop_config(context)
    model_factory = context.get("model_adapter_factory")
    if model_factory is None:
        return ToolResult(ok=False, output="SubSession model factory is unavailable.")

    characters = _characters(context)
    character_name = _optional_str(input.get("character"))
    character = find_character(characters, character_name)
    if character_name and character is None:
        return ToolResult(ok=False, output=f"Character not found. Available characters: {_character_names(characters)}")

    workspace = str(context.get("workspace") or ".")
    sub_registry = _registry_for_subsession(registry, character)
    child_permissions = _child_permissions(config.permissions, character, input.get("permissions"))
    description = _optional_str(input.get("description")) or ""
    name = _optional_str(input.get("name")) or (character.name if character else "subsession")
    name_error = _validate_new_subsession_name(runtime, config, str(context.get("session_id") or ""), name)
    if name_error:
        return ToolResult(ok=False, output=name_error)
    child, output = await runtime.fork_session(
        parent_session_id=str(context.get("session_id") or ""),
        name=name,
        description=description,
        character=character,
        prompt=prompt,
        message=message,
        workspace=workspace,
        source_messages=source_messages,
        config=config,
        registry=sub_registry,
        model_factory=model_factory,
        permissions=child_permissions,
    )
    return ToolResult(
        ok=True,
        output=(
            f"SubSession forked: {child.link.name} ({child.link.id})\n"
            f"Character: {child.link.character or 'none'}\n"
            f"Status: {child.status}\n\n"
            f"{output}"
        ),
    )


async def _run_send_message_tool(input: dict, context: ToolContext) -> ToolResult:
    target = str(input.get("to") or "").strip()
    message = str(input.get("message") or "").strip()
    if not target or not message:
        return ToolResult(ok=False, output="SendMessage requires both 'to' and 'message'.")

    runtime = _runtime(context)
    characters = _characters(context)
    child = runtime.ensure_loaded_session(
        parent_session_id=str(context.get("session_id") or ""),
        target=target,
        config=_loop_config(context),
    )
    if child is None:
        return ToolResult(ok=False, output=f"SubSession not found on disk: {target}")
    character = find_character(characters, child.link.character or child.session.meta.character_name)
    if character is None and (child.link.character or child.session.meta.character_name):
        return ToolResult(ok=False, output=f"Character not found for existing child session: {child.link.character}")
    model_factory = context.get("model_adapter_factory")
    if model_factory is None:
        return ToolResult(ok=False, output="SubSession model factory is unavailable.")

    output = await runtime.send_message(
        parent_session_id=str(context.get("session_id") or ""),
        target=target,
        message=message,
        character=character,
        config=_loop_config(context),
        registry=_registry_for_subsession(_registry(context), character),
        model_factory=model_factory,
    )
    ok = not output.startswith("SubSession not found") and not output.startswith("SubSession is already running")
    return ToolResult(ok=ok, output=output)


def _runtime(context: ToolContext) -> SubSessionRuntime:
    runtime = context.get("subsession_runtime")
    if isinstance(runtime, SubSessionRuntime):
        return runtime
    runtime = SubSessionRuntime()
    context["subsession_runtime"] = runtime
    return runtime


def _registry(context: ToolContext) -> ToolRegistry:
    registry = context.get("tool_registry")
    if isinstance(registry, ToolRegistry):
        return registry
    return ToolRegistry([])


def _loop_config(context: ToolContext) -> AgentLoopConfig:
    config = context.get("loop_config")
    if isinstance(config, AgentLoopConfig):
        return config
    return AgentLoopConfig(workspace=str(context.get("workspace") or "."))


def _characters(context: ToolContext) -> list[Character]:
    characters = context.get("characters")
    if isinstance(characters, list):
        return characters
    return load_characters()


def _registry_for_subsession(registry: ToolRegistry, character: Character | None) -> ToolRegistry:
    tools = [
        tool for tool in registry.list()
        if tool.name not in (
            LIST_CHARACTERS_TOOL_NAME,
            LIST_SUBSESSIONS_TOOL_NAME,
            NEW_SUBSESSION_TOOL_NAME,
            FORK_SUBSESSION_TOOL_NAME,
        )
        and (character is None or _character_allows_tool(character, tool.name))
    ]
    return ToolRegistry(tools=tools, metadata=registry.metadata_copy())


def _character_allows_tool(character: Character, tool_name: str) -> bool:
    if tool_name in character.disallowed_tools:
        return False
    if character.tools is None or character.tools == ["*"]:
        return True
    return tool_name in character.tools


def _child_permissions(
    parent: PermissionRules | None,
    character: Character | None,
    raw_child: Any,
) -> PermissionRules:
    input_rules = PermissionRules.from_dict(raw_child) if isinstance(raw_child, dict) else PermissionRules()
    child = _prioritized_permissions(character.permissions if character else PermissionRules(), input_rules)
    parent_rules = parent.copy() if parent else PermissionRules()
    return PermissionRules(
        allow=[rule for rule in child.allow if rule in parent_rules.allow or not parent_rules.allow],
        deny=list(dict.fromkeys(parent_rules.deny + child.deny)),
        ask=list(dict.fromkeys(parent_rules.ask + child.ask)),
    )


def _prioritized_permissions(base: PermissionRules, extra: PermissionRules) -> PermissionRules:
    result = base.copy()
    for rule in extra.allow:
        _move_rule(result, rule, "allow")
    for rule in extra.ask:
        _move_rule(result, rule, "ask")
    for rule in extra.deny:
        _move_rule(result, rule, "deny")
    return result


def _move_rule(rules: PermissionRules, rule: str, target: str) -> None:
    rules.allow = [item for item in rules.allow if item != rule]
    rules.ask = [item for item in rules.ask if item != rule]
    rules.deny = [item for item in rules.deny if item != rule]
    getattr(rules, target).append(rule)


def _validate_new_subsession_name(
    runtime: SubSessionRuntime,
    config: AgentLoopConfig,
    parent_session_id: str,
    name: str,
) -> str:
    validation_error = validate_session_name(name, "SubSession name")
    if validation_error:
        return validation_error
    if runtime.name_exists(config.session_dir, parent_session_id, name):
        return f"SubSession name already exists in this parent session: {name}"
    return ""


def _new_subsession_description(characters: list[Character]) -> str:
    base = (
        "Create a child session for complex, multi-step tasks. "
        "Use SendMessage to continue communicating with an existing child session."
    )
    if not characters:
        return base
    lines = [
        f"- {character.name}: {character.description or 'No description'}"
        for character in characters
    ]
    return base + "\n\nAvailable characters:\n" + "\n".join(lines)


def _fork_subsession_description(characters: list[Character]) -> str:
    base = (
        "Fork the current parent context into a child session, optionally sending it an initial message. "
        "Parameters match NewSubSession, but the child starts with the parent's current conversation context."
    )
    if not characters:
        return base
    lines = [
        f"- {character.name}: {character.description or 'No description'}"
        for character in characters
    ]
    return base + "\n\nAvailable characters:\n" + "\n".join(lines)


def _permissions_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "description": "Optional child session permission rules.",
        "properties": {
            "allow": {"type": "array", "items": {"type": "string"}},
            "deny": {"type": "array", "items": {"type": "string"}},
            "ask": {"type": "array", "items": {"type": "string"}},
        },
    }


def _character_names(characters: list[Character]) -> str:
    return ", ".join(character.name for character in characters) or "none"


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
