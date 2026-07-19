"""
Core agent loop: process messages, execute tools, manage context.

Mirrors the main agent loop from the TypeScript version.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Awaitable

from ..context.compact.auto_compact import auto_compact
from ..context.compact.compact import messages_to_text
from ..context.compact.manual_compact import manual_compact
from ..context.compact.microcompact import microcompact
from ..context.compact.snip_compact import snip_compact_conversation
from ..config.runtime import LEARN_CODE_PERMISSIONS_PATH, RuntimeConfig, load_runtime_config
from ..memory.store import MemoryStore, get_default_memory_store
from ..mcp.client import build_mcp_registry
from ..sessions.characters import (
    Character,
    append_character_experiences,
    find_character,
    load_characters,
    replace_character_experiences,
)
from ..models.anthropic import AnthropicModelAdapter
from ..models.openai import OpenAIModelAdapter
from ..tools.permissions import (
    PermissionDecision,
    PermissionMode,
    PermissionRequest,
    PermissionResolver,
    PermissionRules,
    Sandbox,
    load_permission_config,
)
from .prompt import build_system_prompt
from ..skills.registry import refresh_default_skill_registry
from ..skills.registry import SkillRegistry
from ..tools.registry import ToolRegistry, ToolResult, ToolContext
from ..tools.builtin import build_builtin_registry
from ..loop.messages import (
    AgentStep,
    ChatMessage,
    CompressionResult,
    ModelAdapter,
    ProviderUsage,
    ToolCall,
)
from ..context.token_estimator import (
    compute_context_stats,
    estimate_messages_tokens,
    mark_provider_usage_stale,
)
from ..context.model_context import get_model_context_window
from ..context.tool_result_storage import (
    ContentReplacementState,
    replace_large_tool_result,
)


TODO_WRITE_TOOL_NAME = "TodoWrite"
TODO_REMINDER_TURNS = 10


@dataclass
class AgentLoopConfig:
    workspace: str
    model: str = ""
    custom_system_prompt: str = ""
    session_id: str = ""
    character_name: str = ""
    subsession_runtime: Any = None
    permission_mode: PermissionMode = PermissionMode.DEFAULT
    additional_directories: list[str] = field(default_factory=list)
    permissions: Optional[PermissionRules] = None
    max_turns: int = 100
    max_loaded_subsessions: int = 10
    session_dir: str = "./sessions"


@dataclass
class AgentLoopState:
    messages: list[ChatMessage] = field(default_factory=list)
    turn: int = 0
    should_stop: bool = False
    stop_reason: str = ""
    auto_compact_failures: int = 0
    auto_compact_result: Optional[CompressionResult] = None
    _runtime_config: Optional[RuntimeConfig] = None


@dataclass
class AgentLoopResult:
    messages: list[ChatMessage]
    turn: int
    stop_reason: str
    usage: Optional[ProviderUsage] = None
    auto_compact_result: Optional[CompressionResult] = None


def _build_system_message(
    workspace: str,
    skills_prompt: str = "",
    custom_prompt: str = "",
    permission_mode: str = "default",
) -> ChatMessage:
    prompt = build_system_prompt(
        workspace=workspace,
        skills_prompt=skills_prompt,
        custom_prompt=custom_prompt,
        permission_mode=permission_mode,
    )
    return ChatMessage.system(prompt)


def _sync_model_tools(model_adapter: ModelAdapter, registry: ToolRegistry) -> None:
    if hasattr(model_adapter, "_tools"):
        setattr(model_adapter, "_tools", registry)


def _merge_permission_rules(base: PermissionRules, overlay: PermissionRules) -> PermissionRules:
    return PermissionRules(
        allow=list(dict.fromkeys(base.allow + overlay.allow)),
        deny=list(dict.fromkeys(base.deny + overlay.deny)),
        ask=list(dict.fromkeys(base.ask + overlay.ask)),
    )


def _get_tool_call_id(call: ToolCall) -> str:
    if not call.id:
        call.id = f"tc-{uuid.uuid4().hex[:12]}"
    return call.id


def _build_tool_call_message(call: ToolCall) -> ChatMessage:
    return ChatMessage.tool_call(
        tool_use_id=_get_tool_call_id(call),
        tool_name=call.tool_name,
        input=call.input,
        id=call.id,
        timestamp=int(time.time() * 1000),
    )


def _build_tool_result_message(
    call: ToolCall,
    result: ToolResult,
) -> ChatMessage:
    return ChatMessage.tool_result(
        tool_use_id=_get_tool_call_id(call),
        tool_name=call.tool_name,
        content=result.output,
        is_error=not result.ok,
        timestamp=int(time.time() * 1000),
    )


def extract_todos_from_messages(messages: list[ChatMessage]) -> list[dict[str, Any]]:
    for message in reversed(messages):
        if message.role != "assistant_tool_call" or message.tool_name != TODO_WRITE_TOOL_NAME:
            continue
        input_value = message.input if isinstance(message.input, dict) else {}
        todos = input_value.get("todos", [])
        if not isinstance(todos, list):
            return []
        todo_items = [item for item in todos if isinstance(item, dict)]
        if todo_items and all(item.get("status") == "completed" for item in todo_items):
            return []
        return todo_items
    return []


def _assistant_turn_counts_since_todo_write(messages: list[ChatMessage]) -> int:
    turns = 0
    counted_tool_round = False
    for message in reversed(messages):
        if message.role == "assistant_thinking":
            continue
        if message.role == "assistant_tool_call":
            if message.tool_name == TODO_WRITE_TOOL_NAME:
                return turns
            if not counted_tool_round:
                turns += 1
                counted_tool_round = True
            continue
        counted_tool_round = False
        if message.role in ("assistant", "assistant_final", "assistant_progress"):
            turns += 1
    return turns


def _assistant_turn_counts_since_todo_reminder(messages: list[ChatMessage]) -> int:
    turns = 0
    counted_tool_round = False
    for message in reversed(messages):
        if message.role == "assistant_thinking":
            continue
        if message.role == "todo_reminder":
            return turns
        if message.role == "assistant_tool_call":
            if not counted_tool_round:
                turns += 1
                counted_tool_round = True
            continue
        counted_tool_round = False
        if message.role in ("assistant", "assistant_final", "assistant_progress"):
            turns += 1
    return turns


def _build_todo_reminder(messages: list[ChatMessage]) -> Optional[ChatMessage]:
    turns_since_write = _assistant_turn_counts_since_todo_write(messages)
    if turns_since_write < TODO_REMINDER_TURNS:
        return None
    turns_since_reminder = _assistant_turn_counts_since_todo_reminder(messages)
    if turns_since_reminder < TODO_REMINDER_TURNS:
        return None

    todos = extract_todos_from_messages(messages)
    todo_lines = "\n".join(
        f"{index + 1}. [{todo.get('status', 'pending')}] {todo.get('content', '')}"
        for index, todo in enumerate(todos)
    )
    content = (
        "<system-reminder>\n"
        "The TodoWrite tool hasn't been used recently. If the current work would benefit from tracking progress, "
        "use TodoWrite to update the task list. Also clean up the todo list if it has become stale and no longer "
        "matches the current work. Only use it if relevant, and never mention this reminder to the user."
    )
    if todo_lines:
        content += f"\n\nHere are the existing contents of your todo list:\n\n{todo_lines}"
    content += "\n</system-reminder>"
    return ChatMessage.todo_reminder(content)


def _build_assistant_message(step: AgentStep) -> list[ChatMessage]:
    messages: list[ChatMessage] = []
    timestamp = int(time.time() * 1000)
    if step.thinking_blocks:
        messages.append(ChatMessage.thinking(
            blocks=step.thinking_blocks,
            provider_usage=step.usage,
            timestamp=timestamp,
        ))
    if step.type == "assistant":
        content = step.content or ""
        if step.kind == "progress":
            messages.append(ChatMessage.progress(content=content, provider_usage=step.usage, timestamp=timestamp))
        else:
            messages.append(ChatMessage.assistant_final(content=content, provider_usage=step.usage, timestamp=timestamp))
    return messages


async def _handle_tool_calls(
    calls: list[ToolCall],
    registry: ToolRegistry,
    permission_resolver: PermissionResolver,
    context: ToolContext,
    state_messages: list[ChatMessage],
    on_permission_request: Optional[Callable[[PermissionRequest], Awaitable[Any]]] = None,
    on_messages_changed: Optional[Callable[[list[ChatMessage]], Awaitable[None]]] = None,
    on_todos_changed: Optional[Callable[[list[dict[str, Any]]], Awaitable[None]]] = None,
    replacement_state: Optional[ContentReplacementState] = None,
) -> None:
    if replacement_state is None:
        replacement_state = ContentReplacementState()

    context["current_messages"] = state_messages
    context["fork_source_messages"] = list(state_messages)

    for call in calls:
        state_messages.append(_build_tool_call_message(call))
    if on_messages_changed:
        await on_messages_changed(state_messages)

    async def execute_call(call: ToolCall) -> ToolResult:
        request = PermissionRequest(
            tool_name=call.tool_name,
            input=call.input,
            message=f"Tool '{call.tool_name}' wants to run",
        )
        response = await permission_resolver.check(request)

        if response.decision == PermissionDecision.DENY or response.decision == PermissionDecision.NEVER:
            if response.decision == PermissionDecision.NEVER:
                permission_resolver.apply_session_decision(call.tool_name, PermissionDecision.NEVER, response.rules or request.suggested_rules)
            return ToolResult(
                ok=False,
                output=f"Permission denied: {response.reason or 'Tool execution blocked'}",
            )
        if response.decision == PermissionDecision.ALWAYS:
            permission_resolver.apply_session_decision(call.tool_name, PermissionDecision.ALWAYS, response.rules or request.suggested_rules)

        return await registry.execute(call.tool_name, call.input, context)

    async def execute_index(index: int, call: ToolCall) -> tuple[int, ToolResult]:
        return index, await execute_call(call)

    tasks = [asyncio.create_task(execute_index(index, call)) for index, call in enumerate(calls)]
    results: list[ToolResult | None] = [None] * len(calls)
    appended_indices: set[int] = set()

    def append_result(index: int, result: ToolResult) -> None:
        call = calls[index]
        raw_output = result.output if isinstance(result.output, str) else str(result.output)
        truncated = replace_large_tool_result(call.id, call.tool_name, raw_output, replacement_state)
        state_messages.append(_build_tool_result_message(call, ToolResult(ok=result.ok, output=truncated)))
        appended_indices.add(index)

    try:
        for completed in asyncio.as_completed(tasks):
            index, result = await completed
            results[index] = result
            append_result(index, result)
            if on_messages_changed:
                await on_messages_changed(state_messages)
            if calls[index].tool_name == TODO_WRITE_TOOL_NAME and on_todos_changed:
                await on_todos_changed(extract_todos_from_messages(state_messages))
    except asyncio.CancelledError:
        for task in tasks:
            task.cancel()
        for index, task in enumerate(tasks):
            if task.done() and not task.cancelled():
                done_index, done_result = task.result()
                results[done_index] = done_result
        for index, result in enumerate(results):
            if result is None:
                results[index] = ToolResult(ok=False, output="")
            if index not in appended_indices:
                append_result(index, results[index])
        if on_messages_changed:
            await on_messages_changed(state_messages)
        raise

    if any(call.tool_name == TODO_WRITE_TOOL_NAME for call in calls) and on_todos_changed:
        await on_todos_changed(extract_todos_from_messages(state_messages))


async def _apply_compression(
    state: AgentLoopState,
    model_adapter: ModelAdapter,
    model: str,
    config: AgentLoopConfig,
) -> bool:
    if len(state.messages) <= 2:
        return False

    changed = False

    ctx_window = get_model_context_window(model)
    snip_result = await snip_compact_conversation(
        state.messages,
        compute_context_stats(state.messages, model),
        ctx_window.context_window,
    )
    if snip_result.did_snip:
        state.messages = snip_result.messages
        changed = True

    microcompact_result = microcompact(state.messages, model)
    if microcompact_result.did_microcompact:
        state.messages = microcompact_result.messages
        changed = True

    # Context collapse code is kept for reference/resume compatibility, but it
    # is not triggered in the active compression pipeline. Its candidate rules
    # overlap heavily with snip compact, and the upstream Open-ClaudeCode
    # checkout does not include the concrete implementation this module mirrors.

    result = await auto_compact(state.messages, model, model_adapter)
    if result:
        if config.character_name and result.removed_messages:
            await _update_character_experiences(
                character_name=config.character_name,
                removed_messages=result.removed_messages,
                model_adapter=model_adapter,
            )
        state.messages = result.messages
        state.auto_compact_result = result
        return True

    return changed


async def _update_character_experiences(
    character_name: str,
    removed_messages: list[ChatMessage],
    model_adapter: ModelAdapter,
) -> None:
    character = find_character(load_characters(), character_name)
    if character is None:
        return
    conversation_text = messages_to_text(removed_messages)
    if not conversation_text.strip():
        return

    response = await model_adapter.next([
        ChatMessage.system(character.system_prompt()),
        ChatMessage.user(
            "请基于以下即将被压缩/移出上下文的原始对话片段，总结新的可复用经验。\n\n"
            "你已经在 system prompt 中看到了该 character 已有经验。\n"
            "不要重复已有经验，不要改写已有经验。\n"
            "不要记录一次性细节、临时路径、隐私信息。\n"
            "只输出新增经验；如果没有新经验，输出空内容。\n\n"
            f"对话片段：\n{conversation_text}"
        ),
    ])
    if response.type != "assistant" or not response.content.strip():
        return

    updated = append_character_experiences(character, _experience_lines(response.content))
    if _experiences_length(updated) <= updated.experience_compact_threshold:
        return

    compacted = await model_adapter.next([
        ChatMessage.system(updated.system_prompt()),
        ChatMessage.user(
            "请精简并合并该 character 的长期经验，保留稳定、可复用、非重复内容。\n"
            "只输出精简后的经验列表。"
        ),
    ])
    if compacted.type == "assistant" and compacted.content.strip():
        replace_character_experiences(updated, _experience_lines(compacted.content))


def _experience_lines(content: str) -> list[str]:
    lines: list[str] = []
    for raw in content.splitlines():
        text = raw.strip()
        while text.startswith(("-", "*")):
            text = text[1:].strip()
        if len(text) >= 2 and text[0].isdigit() and text[1] in (".", "、"):
            text = text[2:].strip()
        if text:
            lines.append(text)
    return lines


def _experiences_length(character: Character) -> int:
    return sum(len(item) for item in character.experiences)


async def run_agent_loop(
    user_input: str,
    model_adapter: ModelAdapter,
    config: AgentLoopConfig,
    state: Optional[AgentLoopState] = None,
    on_step: Optional[Callable[[AgentStep], Awaitable[None]]] = None,
    on_delta: Optional[Callable[[dict[str, Any]], Awaitable[None]]] = None,
    on_permission_request: Optional[Callable[[PermissionRequest], Awaitable[Any]]] = None,
    on_messages_changed: Optional[Callable[[list[ChatMessage]], Awaitable[None]]] = None,
    on_todos_changed: Optional[Callable[[list[dict[str, Any]]], Awaitable[None]]] = None,
    on_mcp_servers_changed: Optional[Callable[[list[dict[str, Any]]], Awaitable[None]]] = None,
    tool_registry: Optional[ToolRegistry] = None,
    user_already_appended: bool = False,
) -> AgentLoopResult:
    if state is None:
        state = AgentLoopState()

    state.auto_compact_result = None
    character = find_character(load_characters(), config.character_name) if config.character_name else None
    permission_config = load_permission_config(LEARN_CODE_PERMISSIONS_PATH)
    if character is not None:
        permission_config.permission_rules = character.permissions.copy()
    if config.permissions is not None:
        permission_config.permission_rules = _merge_permission_rules(
            permission_config.permission_rules,
            config.permissions,
        )
    if config.permission_mode != PermissionMode.DEFAULT:
        permission_config.mode = config.permission_mode
    elif permission_config.mode == PermissionMode.DEFAULT:
        permission_config.mode = config.permission_mode
    permission_config.additional_directories.extend(config.additional_directories)

    skills = refresh_default_skill_registry(config.workspace)
    if character is not None and character.skills is not None and character.skills != ["*"]:
        filtered_skills = SkillRegistry()
        for skill_name in character.skills:
            skill = skills.get(skill_name)
            if skill:
                filtered_skills.register(skill)
        skills = filtered_skills
    custom_prompt = config.custom_system_prompt
    if character is not None and character.system_prompt():
        custom_prompt = "\n\n".join(part for part in (character.system_prompt(), custom_prompt) if part)
    system_message = _build_system_message(
        workspace=config.workspace,
        skills_prompt=skills.build_system_prompt(),
        custom_prompt=custom_prompt,
        permission_mode=permission_config.mode.value,
    )
    if not state.messages or state.messages[0].role != "system":
        state.messages.insert(0, system_message)
    else:
        state.messages[0] = system_message

    if not user_already_appended:
        state.messages.append(ChatMessage.user(user_input, timestamp=int(time.time() * 1000)))
    state.turn += 1

    runtime_config = state._runtime_config or await load_runtime_config()
    if config.max_loaded_subsessions <= 0:
        config.max_loaded_subsessions = runtime_config.max_loaded_subsessions

    owns_registry = tool_registry is None
    mcp_connections = []
    if tool_registry is not None:
        registry = tool_registry
    else:
        builtin_registry = build_builtin_registry()
        from ..tools.subsession_tools import build_subsession_tools
        builtin_registry.add_tools(build_subsession_tools(load_characters()))
        mcp_registry = None
        if runtime_config.mcp_servers:
            mcp_registry, mcp_connections = await build_mcp_registry(runtime_config.mcp_servers)
            if mcp_registry and on_mcp_servers_changed:
                await on_mcp_servers_changed([server.__dict__ for server in mcp_registry.get_mcp_servers()])

        if mcp_registry is None:
            registry = builtin_registry
        else:
            registry = builtin_registry.merge(mcp_registry)

    sandbox = Sandbox.from_config(
        permission_config,
        workspace=config.workspace,
    )
    registry = registry.filtered_by_sandbox(sandbox)
    _sync_model_tools(model_adapter, registry)
    permission_resolver = PermissionResolver(
        config=permission_config,
        permission_context=sandbox.permission_context,
    )
    if on_permission_request:
        permission_resolver.set_callback(on_permission_request)

    async def model_adapter_factory(tools: ToolRegistry) -> ModelAdapter:
        runtime = await load_runtime_config()
        if runtime.provider == "openai":
            return OpenAIModelAdapter(tools)
        return AnthropicModelAdapter(tools)

    subsession_runtime = config.subsession_runtime
    if subsession_runtime is None:
        from ..sessions.subsessions import SubSessionRuntime
        subsession_runtime = SubSessionRuntime()

    tool_context = sandbox.to_tool_context()
    tool_context.update({
        "tool_registry": registry,
        "model_adapter_factory": model_adapter_factory,
        "loop_config": config,
        "characters": load_characters(),
        "subsession_runtime": subsession_runtime,
        "session_id": config.session_id,
        "character_name": config.character_name,
    })
    memory = get_default_memory_store()
    replacement_state = ContentReplacementState()

    try:
        while not state.should_stop and state.turn <= config.max_turns:
            await _apply_compression(state, model_adapter, config.model or runtime_config.model, config)
            todo_reminder = _build_todo_reminder(state.messages)
            if todo_reminder:
                state.messages.append(todo_reminder)
                if on_messages_changed:
                    await on_messages_changed(state.messages)

            step = await model_adapter.next(state.messages, on_delta=on_delta)
            if step.calls:
                for call in step.calls:
                    _get_tool_call_id(call)

            if on_step:
                await on_step(step)

            if step.type == "assistant":
                state.messages.extend(_build_assistant_message(step))
                state.should_stop = True
                state.stop_reason = "assistant_responded"
                return AgentLoopResult(
                    messages=state.messages,
                    turn=state.turn,
                    stop_reason=state.stop_reason,
                    usage=step.usage,
                    auto_compact_result=state.auto_compact_result,
                )

            if step.type == "tool_calls" and step.calls:
                assistant_msgs = _build_assistant_message(step)
                state.messages.extend(assistant_msgs)

                await _handle_tool_calls(
                    step.calls, registry, permission_resolver, tool_context,
                    state_messages=state.messages,
                    on_permission_request=on_permission_request,
                    on_messages_changed=on_messages_changed,
                    on_todos_changed=on_todos_changed,
                    replacement_state=replacement_state,
                )
                state.turn += 1
                continue

            state.should_stop = True
            state.stop_reason = "no_valid_response"

        if state.turn > config.max_turns:
            state.stop_reason = "max_turns_exceeded"

    finally:
        if owns_registry:
            for conn in mcp_connections:
                try:
                    await conn.close()
                except Exception:
                    pass

    return AgentLoopResult(
        messages=state.messages,
        turn=state.turn,
        stop_reason=state.stop_reason,
        auto_compact_result=state.auto_compact_result,
    )


async def run_manual_compact(
    messages: list[ChatMessage],
    model_adapter: ModelAdapter,
) -> Optional[CompressionResult]:
    return await manual_compact(messages, model_adapter)
