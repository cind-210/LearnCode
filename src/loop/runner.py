"""
Core agent loop: process messages, execute tools, manage context.

Mirrors the main agent loop from the TypeScript version.
"""
from __future__ import annotations

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
from ..config.runtime import RuntimeConfig, load_runtime_config
from ..memory.store import MemoryStore, get_default_memory_store
from ..mcp.client import build_mcp_registry
from ..tools.permissions import (
    PermissionConfig,
    PermissionDecision,
    PermissionMode,
    PermissionRequest,
    PermissionResolver,
    PermissionResponse,
    get_default_permission_resolver,
)
from .prompt import build_system_prompt
from ..skills.registry import get_default_skill_registry
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


@dataclass
class AgentLoopConfig:
    workspace: str
    model: str = ""
    permission_mode: PermissionMode = PermissionMode.DEFAULT
    additional_directories: list[str] = field(default_factory=list)
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


def _build_tool_context(workspace: str) -> ToolContext:
    return {"workspace": workspace}


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
    on_permission_request: Optional[Callable] = None,
    replacement_state: Optional[ContentReplacementState] = None,
) -> list[ChatMessage]:
    messages: list[ChatMessage] = []
    executed_results: list[dict] = []

    if replacement_state is None:
        replacement_state = ContentReplacementState()

    for call in calls:
        request = PermissionRequest(
            tool_name=call.tool_name,
            input=call.input,
            message=f"Tool '{call.tool_name}' wants to run",
        )
        response = await permission_resolver.check(request)

        if response.decision == PermissionDecision.DENY or response.decision == PermissionDecision.NEVER:
            messages.append(_build_tool_call_message(call))
            messages.append(_build_tool_result_message(
                call,
                ToolResult(ok=False, output=f"Permission denied: {response.reason or 'Tool execution blocked'}"),
            ))
            if response.decision == PermissionDecision.NEVER:
                permission_resolver.apply_session_decision(call.tool_name, PermissionDecision.NEVER)
            continue

        if on_permission_request and response.decision == PermissionDecision.ALLOW:
            await on_permission_request(call.tool_name, "allow")

        messages.append(_build_tool_call_message(call))
        result = await registry.execute(call.tool_name, call.input, context)

        raw_output = result.output if isinstance(result.output, str) else str(result.output)
        truncated = replace_large_tool_result(
            call.id, call.tool_name, raw_output, replacement_state,
        )

        executed_results.append({
            "tool_use_id": call.id,
            "tool_name": call.tool_name,
            "content": truncated,
            "is_error": not result.ok,
        })

    budgeted = apply_tool_result_budget(executed_results, replacement_state)

    for entry in budgeted:
        for call in calls:
            if call.id == entry["tool_use_id"]:
                messages.append(_build_tool_result_message(
                    call,
                    ToolResult(ok=not entry.get("is_error", False), output=entry["content"]),
                ))
                break

    return messages


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
    on_permission_request: Optional[Callable[[str, str], Awaitable[None]]] = None,
) -> AgentLoopResult:
    if state is None:
        state = AgentLoopState()

    state.auto_compact_result = None

    if not state.messages:
        skills = get_default_skill_registry()
        state.messages.append(_build_system_message(
            workspace=config.workspace,
            skills_prompt=skills.build_system_prompt(),
            permission_mode=config.permission_mode.value,
        ))

    state.messages.append(ChatMessage.user(user_input))
    state.turn += 1

    builtin_registry = build_builtin_registry()
    runtime_config = state._runtime_config or await load_runtime_config()

    mcp_registry = None
    mcp_connections = []
    if runtime_config.mcp_servers:
        mcp_registry, mcp_connections = await build_mcp_registry(runtime_config.mcp_servers)

    if mcp_registry is None:
        registry = builtin_registry
    else:
        registry = builtin_registry.merge(mcp_registry)

    permission_config = PermissionConfig(
        mode=config.permission_mode,
        additional_directories=config.additional_directories,
    )
    permission_resolver = PermissionResolver(config=permission_config)

    tool_context = _build_tool_context(config.workspace)
    memory = get_default_memory_store()
    replacement_state = ContentReplacementState()

    try:
        while not state.should_stop and state.turn <= config.max_turns:
            await _apply_compression(state, model_adapter, config.model or runtime_config.model)
            model_view = project_collapsed_view(state.messages, state.collapse_state)

            step = await model_adapter.next(model_view)

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

                tool_messages = await _handle_tool_calls(
                    step.calls, registry, permission_resolver, tool_context,
                    on_permission_request=on_permission_request,
                    replacement_state=replacement_state,
                )
                state.messages.extend(tool_messages)
                state.turn += 1
                continue

            state.should_stop = True
            state.stop_reason = "no_valid_response"

        if state.turn > config.max_turns:
            state.stop_reason = "max_turns_exceeded"

    finally:
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
