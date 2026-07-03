"""
Time-based microcompact: clear old tool results after the prompt cache is cold.

This keeps the session log append-only by emitting a microcompact boundary. The
active in-memory context is replaced with the compacted view so subsequent turns
do not reload the cleared tool results.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from ...loop.messages import ChatMessage
from ..token_estimator import CLEAR_MARKER, estimate_message_tokens
from ..tool_limits import COMPACTABLE_TOOLS
from .constants import RETENTION, TIME_BASED_MICROCOMPACT_GAP_MS


@dataclass
class MicrocompactResult:
    messages: list[ChatMessage]
    boundary: ChatMessage | None = None
    did_microcompact: bool = False
    tokens_freed: int = 0


def _now_ms() -> int:
    return int(time.time() * 1000)


def _last_assistant_timestamp(messages: list[ChatMessage]) -> int | None:
    for message in reversed(messages):
        if message.role in ("assistant", "assistant_progress", "assistant_thinking") and message.timestamp:
            return message.timestamp
    return None


def _build_boundary(cleared_ids: list[str], tokens_freed: int, timestamp: int) -> ChatMessage:
    return ChatMessage.microcompact_boundary(
        content=f"Microcompact cleared {len(cleared_ids)} old tool result(s), saving approximately {tokens_freed} tokens.",
        cleared_message_ids=cleared_ids,
        removed_count=len(cleared_ids),
        tokens_freed=tokens_freed,
        timestamp=timestamp,
        id=f"microcompact-{timestamp}",
    )


def _no_microcompact(messages: list[ChatMessage]) -> MicrocompactResult:
    return MicrocompactResult(messages=messages)


def microcompact(messages: list[ChatMessage], model: str, now_ms: int | None = None) -> MicrocompactResult:
    del model
    now = now_ms if now_ms is not None else _now_ms()
    last_assistant = _last_assistant_timestamp(messages)
    if last_assistant is None or now - last_assistant < TIME_BASED_MICROCOMPACT_GAP_MS:
        return _no_microcompact(messages)

    tool_result_indices: list[int] = []
    for index, message in enumerate(messages):
        if message.role == "tool_result" and message.tool_name in COMPACTABLE_TOOLS:
            tool_result_indices.append(index)

    keep_recent = max(1, RETENTION["KEEP_RECENT_TOOL_RESULTS"])
    if len(tool_result_indices) <= keep_recent:
        return _no_microcompact(messages)

    keep_indices = set(tool_result_indices[-keep_recent:])
    indices_to_clear = [
        index
        for index in tool_result_indices
        if index not in keep_indices and messages[index].content != CLEAR_MARKER
    ]
    if not indices_to_clear:
        return _no_microcompact(messages)

    tokens_freed = sum(estimate_message_tokens(messages[index]) for index in indices_to_clear)
    cleared_ids: list[str] = []
    result: list[ChatMessage] = []
    for index, message in enumerate(messages):
        if index not in indices_to_clear:
            result.append(message)
            continue
        cleared_ids.append(message.id or message.tool_use_id)
        result.append(ChatMessage.tool_result(
            tool_use_id=message.tool_use_id,
            tool_name=message.tool_name,
            content=CLEAR_MARKER,
            is_error=message.is_error,
            id=message.id,
        ))

    boundary = _build_boundary(cleared_ids, tokens_freed, now)
    result.append(boundary)
    return MicrocompactResult(
        messages=result,
        boundary=boundary,
        did_microcompact=True,
        tokens_freed=tokens_freed,
    )
