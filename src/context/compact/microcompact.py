"""
Micro-compact: clear older tool results when context is under pressure.

Mirrors TypeScript compact/microcompact.ts from the TypeScript version.
"""
from __future__ import annotations

from ...loop.messages import ChatMessage
from ..tool_limits import COMPACTABLE_TOOLS
from ..token_estimator import compute_context_stats, CLEAR_MARKER
from .constants import THRESHOLDS, RETENTION


def microcompact(messages: list[ChatMessage], model: str) -> list[ChatMessage]:
    stats = compute_context_stats(messages, model)
    if stats.utilization < THRESHOLDS["MICROCOMPACT_UTILIZATION"]:
        return messages

    tool_result_indices: list[int] = []
    for i, msg in enumerate(messages):
        if msg.role == "tool_result" and msg.tool_name in COMPACTABLE_TOOLS:
            tool_result_indices.append(i)

    if len(tool_result_indices) <= RETENTION["KEEP_RECENT_TOOL_RESULTS"]:
        return messages

    keep_from = len(tool_result_indices) - RETENTION["KEEP_RECENT_TOOL_RESULTS"]
    indices_to_clear = set(tool_result_indices[:keep_from])

    changed = False
    result: list[ChatMessage] = []
    for i, msg in enumerate(messages):
        if i in indices_to_clear and msg.role == "tool_result":
            if msg.content != CLEAR_MARKER:
                changed = True
                result.append(ChatMessage.tool_result(
                    tool_use_id=msg.tool_use_id,
                    tool_name=msg.tool_name,
                    content=CLEAR_MARKER,
                    is_error=msg.is_error,
                ))
            else:
                result.append(msg)
        else:
            result.append(msg)

    return result if changed else messages