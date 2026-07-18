"""
Core agent loop: process messages, execute tools, manage context.

Mirrors the main agent loop from the TypeScript version.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Awaitable

from ..context.compact.auto_compact import auto_compact
from ..context.compact.manual_compact import manual_compact
from ..context.compact.microcompact import microcompact
from ..context.compact.context_collapse import (
    ContextCollapseState,
    create_context_collapse_state,
    project_collapsed_view,
)
from ..context.compact.snip_compact import snip_compact_conversation
from ..config.runtime import LEARN_CODE_PERMISSIONS_PATH, RuntimeConfig, load_runtime_config
from ..memory.store import MemoryStore, get_default_memory_store
from ..mcp.client import build_mcp_registry
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
    apply_tool_result_budget,
)


TODO_WRITE_TOOL_NAME = "TodoWrite"
TODO_REMINDER_TURNS = 10


@dataclass
class AgentLoopConfig:
    workspace: str
    model: str = ""
    permission_mode: PermissionMode = PermissionMode.DEFAULT
    additional_directories: list[str] = field(default_factory=list)
    permissions: Optional[PermissionRules] = None
    max_turns: int = 100
    session_dir: str = "./sessions"


@dataclass
class AgentLoopState:
    messages: list[ChatMessage] = field(default_factory=list)
    turn: int = 0
    should_stop: bool = False
    stop_reason: str = ""
    collapse_state: ContextCollapseState = field(default_factory=create_context_collapse_state)
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


def _get_tool_call_id(call: ToolCall) -> str:
    return call.id or f"tc-{int(time.time() * 1000)}"


def _build_tool_call_message(call: ToolCall) -> ChatMessage:
    return ChatMessage.tool_call(
        tool_use_id=_get_tool_call_id(call),
        tool_name=call.tool_name,
        input=call.input,
        id=call.id,
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
        if message.role in ("assistant", "assistant_progress"):
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
        if message.role in ("assistant", "assistant_progress"):
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
            messages.append(ChatMessage.assistant(content=content, provider_usage=step.usage, timestamp=timestamp))
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
    executed_results: list[dict] = []
    result_slots: list[tuple[int, ToolCall]] = []
    appended_indices: set[int] = set()

    def append_result(index: int, result: ToolResult) -> None:
        call = calls[index]
        raw_output = result.output if isinstance(result.output, str) else str(result.output)
        truncated = replace_large_tool_result(call.id, call.tool_name, raw_output, replacement_state)
        executed_results.append({
            "tool_use_id": call.id,
            "tool_name": call.tool_name,
            "content": truncated,
            "is_error": not result.ok,
        })
        result_slots.append((len(state_messages), call))
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

    budgeted = apply_tool_result_budget(executed_results, replacement_state)

    for entry in budgeted:
        for result_index, call in result_slots:
            if call.id == entry["tool_use_id"]:
                state_messages[result_index] = _build_tool_result_message(
                    call,
                    ToolResult(ok=not entry.get("is_error", False), output=entry["content"]),
                )
                break

    if on_messages_changed:
        await on_messages_changed(state_messages)
    if any(call.tool_name == TODO_WRITE_TOOL_NAME for call in calls) and on_todos_changed:
        await on_todos_changed(extract_todos_from_messages(state_messages))


async def _apply_compression(
    state: AgentLoopState,
    model_adapter: ModelAdapter,
    model: str,
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
        state.messages = result.messages
        state.auto_compact_result = result
        return True

    return changed


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
    permission_config = load_permission_config(LEARN_CODE_PERMISSIONS_PATH)
    if config.permissions is not None:
        permission_config.permission_rules = config.permissions.copy()
    if config.permission_mode != PermissionMode.DEFAULT:
        permission_config.mode = config.permission_mode
    elif permission_config.mode == PermissionMode.DEFAULT:
        permission_config.mode = config.permission_mode
    permission_config.additional_directories.extend(config.additional_directories)

    skills = refresh_default_skill_registry(config.workspace)
    system_message = _build_system_message(
        workspace=config.workspace,
        skills_prompt=skills.build_system_prompt(),
        permission_mode=permission_config.mode.value,
    )
    if not state.messages or state.messages[0].role != "system":
        state.messages.insert(0, system_message)
    else:
        state.messages[0] = system_message

    if not user_already_appended:
        state.messages.append(ChatMessage.user(user_input))
    state.turn += 1

    runtime_config = state._runtime_config or await load_runtime_config()

    owns_registry = tool_registry is None
    mcp_connections = []
    if tool_registry is not None:
        registry = tool_registry
    else:
        builtin_registry = build_builtin_registry()
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

    tool_context = sandbox.to_tool_context()
    memory = get_default_memory_store()
    replacement_state = ContentReplacementState()

    try:
        while not state.should_stop and state.turn <= config.max_turns:
            await _apply_compression(state, model_adapter, config.model or runtime_config.model)
            model_view = project_collapsed_view(state.messages, state.collapse_state)
            todo_reminder = _build_todo_reminder(model_view)
            if todo_reminder:
                state.messages.append(todo_reminder)
                if on_messages_changed:
                    await on_messages_changed(state.messages)
                model_view = project_collapsed_view(state.messages, state.collapse_state)

            step = await model_adapter.next(model_view, on_delta=on_delta)

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
