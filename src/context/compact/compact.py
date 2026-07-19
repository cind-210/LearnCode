"""
LLM-based conversation compaction.

Mirrors TypeScript compact/compact.ts from the TypeScript version.
"""
from __future__ import annotations

import time
from typing import Optional

from ...loop.messages import ChatMessage, CompressionResult, ModelAdapter
from ..token_estimator import (
    estimate_messages_tokens,
    mark_provider_usage_stale,
    token_count_with_estimation,
)
from .constants import RETENTION
from .prompt import build_compact_summary_prompt, parse_summary_from_response


def group_messages_by_api_round(messages: list[ChatMessage]) -> list[list[ChatMessage]]:
    groups: list[list[ChatMessage]] = []
    i = 0
    while i < len(messages):
        group: list[ChatMessage] = []
        cursor = i

        if cursor < len(messages) and messages[cursor].role == "assistant_thinking":
            group.append(messages[cursor])
            cursor += 1

        while cursor < len(messages) and messages[cursor].role == "assistant_tool_call":
            group.append(messages[cursor])
            cursor += 1

        while cursor < len(messages) and messages[cursor].role == "tool_result":
            group.append(messages[cursor])
            cursor += 1

        if any(m.role in ("assistant_tool_call", "tool_result") for m in group):
            groups.append(group)
            i = cursor
            continue

        groups.append([messages[i]])
        i += 1

    return groups


def align_boundary_to_api_round(messages: list[ChatMessage], boundary: int) -> int:
    start = 0
    for group in group_messages_by_api_round(messages):
        end = start + len(group)
        if start < boundary < end:
            return start
        start = end
    return boundary


def find_retention_boundary(messages: list[ChatMessage]) -> int:
    token_sum = 0
    boundary = len(messages)

    for i in range(len(messages) - 1, 0, -1):
        msg_tokens = estimate_messages_tokens([messages[i]])
        if token_sum + msg_tokens > RETENTION["MAX_KEEP_TOKENS"]:
            break
        token_sum += msg_tokens
        boundary = i

    min_boundary = max(1, len(messages) - RETENTION["MIN_KEEP_MESSAGES"])
    boundary = min(boundary, min_boundary)

    if boundary <= 1 and len(messages) > RETENTION["MIN_KEEP_MESSAGES"] + 1:
        boundary = max(1, len(messages) - RETENTION["MIN_KEEP_MESSAGES"])

    return align_boundary_to_api_round(messages, boundary)


def messages_to_text(messages: list[ChatMessage]) -> str:
    import json
    parts: list[str] = []
    for msg in messages:
        if msg.role == "user":
            parts.append(f"[User]: {msg.content}")
        elif msg.role in ("assistant", "assistant_progress"):
            parts.append(f"[Assistant]: {msg.content}")
        elif msg.role == "assistant_thinking":
            parts.append("[Assistant Thinking]: preserved provider reasoning block")
        elif msg.role == "assistant_tool_call":
            parts.append(f"[Tool Call: {msg.tool_name}]: {json.dumps(msg.input)}")
        elif msg.role == "tool_result":
            content = msg.content[:500] + "... (truncated)" if len(msg.content) > 500 else msg.content
            err = " ERROR" if msg.is_error else ""
            parts.append(f"[Tool Result: {msg.tool_name}{err}]: {content}")
        elif msg.role == "context_summary":
            parts.append(f"[Previous Summary]: {msg.content}")
        elif msg.role == "snip_boundary":
            parts.append(f"[Snipped Context Boundary]: {msg.content}")
    return "\n\n".join(parts)


async def compact_conversation(
    messages: list[ChatMessage],
    model_adapter: ModelAdapter,
) -> Optional[CompressionResult]:
    if len(messages) <= 2:
        return None

    tokens_before = token_count_with_estimation(messages).total_tokens

    system_messages = [m for m in messages if m.role == "system"]
    non_system = [m for m in messages if m.role != "system"]

    if len(non_system) <= RETENTION["MIN_KEEP_MESSAGES"]:
        return None

    boundary = find_retention_boundary(messages)
    messages_to_compress = messages[1:boundary]
    messages_to_keep = [
        mark_provider_usage_stale(m, "conversation was compacted after this provider usage was recorded")
        for m in messages[boundary:]
    ]

    if not messages_to_compress:
        return None

    conversation_text = messages_to_text(messages_to_compress)
    summary_prompt = build_compact_summary_prompt(conversation_text)

    summary_request: list[ChatMessage] = [
        ChatMessage.system("You are a helpful assistant that summarizes conversations concisely."),
        ChatMessage.user(summary_prompt),
    ]

    try:
        response = await model_adapter.next(summary_request)
        if response.type != "assistant" or not response.content.strip():
            return None

        summary_content = parse_summary_from_response(response.content)
        if not summary_content:
            return None

        summary_message = ChatMessage.context_summary(
            content=summary_content,
            compressed_count=len(messages_to_compress),
            timestamp=int(time.time() * 1000),
        )

        new_messages = system_messages + [summary_message] + messages_to_keep
        tokens_after = token_count_with_estimation(new_messages).total_tokens

        return CompressionResult(
            messages=new_messages,
            summary=summary_message,
            removed_count=len(messages_to_compress),
            tokens_before=tokens_before,
            tokens_after=tokens_after,
            removed_messages=messages_to_compress,
        )
    except Exception:
        return None
