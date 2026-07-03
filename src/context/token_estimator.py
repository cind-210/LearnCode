"""
Token estimation and context statistics.

Mirrors src/utils/token-estimator.ts from the TypeScript version.
"""
from __future__ import annotations

from typing import Optional

from ..loop.messages import ChatMessage, ProviderUsage
from .model_context import get_model_context_window

# ---------------------------------------------------------------------------
# Token accounting
# ---------------------------------------------------------------------------

CHARS_PER_TOKEN: dict[str, float] = {
    "system": 3.5,
    "user": 3.0,
    "assistant_thinking": 3.0,
    "assistant": 3.5,
    "assistant_progress": 3.5,
    "assistant_tool_call": 2.5,
    "tool_result": 2.0,
    "context_summary": 3.5,
    "snip_boundary": 3.5,
    "microcompact_boundary": 3.5,
}

CLEAR_MARKER = "[Old tool result content cleared]"


class TokenAccountingResult:
    def __init__(
        self,
        total_tokens: int,
        provider_usage_tokens: int,
        estimated_tokens: int,
        source: str,
        is_exact: bool = False,
        usage_boundary: Optional[dict] = None,
        stale: bool = False,
        reason: str = "",
    ):
        self.total_tokens = total_tokens
        self.provider_usage_tokens = provider_usage_tokens
        self.estimated_tokens = estimated_tokens
        self.source = source
        self.is_exact = is_exact
        self.usage_boundary = usage_boundary
        self.stale = stale
        self.reason = reason


class ContextStats:
    def __init__(
        self,
        estimated_tokens: int,
        total_tokens: int,
        provider_usage_tokens: int,
        context_window: int,
        effective_input: int,
        utilization: float,
        warning_level: str,
        accounting: TokenAccountingResult,
    ):
        self.estimated_tokens = estimated_tokens
        self.total_tokens = total_tokens
        self.provider_usage_tokens = provider_usage_tokens
        self.context_window = context_window
        self.effective_input = effective_input
        self.utilization = utilization
        self.warning_level = warning_level
        self.accounting = accounting


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _message_content_length(message: ChatMessage) -> int:
    import json
    if message.role in ("system", "user", "assistant", "assistant_progress"):
        return len(message.content)
    if message.role == "assistant_thinking":
        try:
            return len(json.dumps([b.__dict__ for b in (message.blocks or [])]))
        except Exception:
            return 0
    if message.role == "assistant_tool_call":
        try:
            return len(json.dumps(message.input))
        except Exception:
            return 0
    if message.role in ("tool_result", "context_summary", "snip_boundary", "microcompact_boundary"):
        return len(message.content)
    return 0


def estimate_message_tokens(message: ChatMessage) -> int:
    ratio = CHARS_PER_TOKEN.get(message.role, 3.0)
    length = _message_content_length(message)
    return max(1, int(length / ratio + 0.999))


def estimate_messages_tokens(messages: list[ChatMessage]) -> int:
    return sum(estimate_message_tokens(m) for m in messages)


def _message_provider_usage(message: ChatMessage) -> Optional[ProviderUsage]:
    if message.role in ("assistant", "assistant_progress", "assistant_tool_call"):
        if message.provider_usage and not message.usage_stale:
            return message.provider_usage
    return None


def token_count_with_estimation(messages: list[ChatMessage]) -> TokenAccountingResult:
    for i in range(len(messages) - 1, -1, -1):
        usage = _message_provider_usage(messages[i])
        if not usage:
            continue
        tail = messages[i + 1:]
        estimated = estimate_messages_tokens(tail)
        return TokenAccountingResult(
            total_tokens=usage.total_tokens + estimated,
            provider_usage_tokens=usage.total_tokens,
            estimated_tokens=estimated,
            source="provider_usage_plus_estimate" if estimated > 0 else "provider_usage",
            is_exact=estimated == 0,
            usage_boundary={"message_index": i},
        )

    estimated = estimate_messages_tokens(messages)
    return TokenAccountingResult(
        total_tokens=estimated,
        provider_usage_tokens=0,
        estimated_tokens=estimated,
        source="estimate_only",
        is_exact=False,
        reason="no provider usage available",
    )


def mark_provider_usage_stale(message: ChatMessage, reason: str) -> ChatMessage:
    if message.role in ("assistant", "assistant_progress", "assistant_tool_call"):
        if message.provider_usage:
            message.usage_stale = True
            message.usage_stale_reason = reason
    return message


def compute_context_stats(messages: list[ChatMessage], model: str) -> ContextStats:
    window = get_model_context_window(model)
    accounting = token_count_with_estimation(messages)
    utilization = min(1.0, accounting.total_tokens / window.effective_input)

    if utilization >= 0.95:
        warning_level = "blocked"
    elif utilization >= 0.85:
        warning_level = "critical"
    elif utilization >= 0.50:
        warning_level = "warning"
    else:
        warning_level = "normal"

    return ContextStats(
        estimated_tokens=accounting.estimated_tokens,
        total_tokens=accounting.total_tokens,
        provider_usage_tokens=accounting.provider_usage_tokens,
        context_window=window.context_window,
        effective_input=window.effective_input,
        utilization=utilization,
        warning_level=warning_level,
        accounting=accounting,
    )
