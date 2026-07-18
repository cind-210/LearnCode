"""
Snip compact: remove middle conversation segments to free context.

Mirrors TypeScript compact/snipCompact.ts from the TypeScript version.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from ...loop.messages import ChatMessage
from ..token_estimator import (
    estimate_messages_tokens,
    mark_provider_usage_stale,
    token_count_with_estimation,
)
from .constants import (
    SNIP_COMPACT_THRESHOLD,
    SNIP_KEEP_RECENT_MESSAGES,
    SNIP_MIN_MESSAGES_TO_REMOVE,
    SNIP_MIN_TOKENS_TO_FREE,
    SNIP_TARGET_USAGE,
)


@dataclass
class SnipCompactResult:
    messages: list[ChatMessage]
    did_snip: bool
    tokens_before: int
    tokens_after: int
    tokens_freed: int
    removed_message_ids: list[str]
    boundary_message: Optional[ChatMessage] = None
    reason: str = ""


PROTECTED_TOOL_NAMES = {"edit_file", "modify_file", "patch_file", "write_file", "apply_patch"}

ERROR_MARKERS = ["error", "failed", "failure", "exception", "traceback", "permission denied"]


def _message_id(message: ChatMessage, index: int) -> str:
    return message.id or f"message-{index}"


def _is_boundary_message(message: ChatMessage) -> bool:
    return message.role in ("system", "context_summary", "snip_boundary")


def _is_protected_tool_name(tool_name: str) -> bool:
    n = tool_name.strip().lower()
    return n in PROTECTED_TOOL_NAMES or any(
        k in n for k in ("patch", "write", "edit", "modify")
    )


def _tool_result_looks_important_error(message: ChatMessage) -> bool:
    if message.is_error:
        return True
    content = message.content.lower()
    return any(marker in content for marker in ERROR_MARKERS)


def _message_text_looks_important_error(message: ChatMessage) -> bool:
    if message.role not in ("user", "assistant", "assistant_progress", "context_summary", "snip_boundary"):
        return False
    content = message.content.lower()
    return any(marker in content for marker in ERROR_MARKERS)


@dataclass
class _MessageGroup:
    start: int
    end: int
    messages: list[ChatMessage]
    tokens: int
    protected: bool = False
    reasons: list[str] = field(default_factory=list)


@dataclass
class _SafeRun:
    groups: list[_MessageGroup]
    start: int
    end: int
    messages_count: int
    tokens: int


def _build_message_groups(messages: list[ChatMessage]) -> list[_MessageGroup]:
    groups: list[_MessageGroup] = []
    i = 0
    while i < len(messages):
        msg = messages[i]

        if msg.role == "assistant_tool_call":
            nxt = messages[i + 1] if i + 1 < len(messages) else None
            grouped = [msg, nxt] if nxt and nxt.role == "tool_result" and nxt.tool_use_id == msg.tool_use_id else [msg]
            groups.append(_MessageGroup(
                start=i, end=i + len(grouped),
                messages=grouped,
                tokens=estimate_messages_tokens(grouped),
                protected=len(grouped) == 1,
                reasons=["unclosed_tool_call"] if len(grouped) == 1 else [],
            ))
            i += len(grouped)
            continue

        if msg.role == "tool_result":
            groups.append(_MessageGroup(
                start=i, end=i + 1, messages=[msg],
                tokens=estimate_messages_tokens([msg]),
                protected=True, reasons=["orphan_tool_result"],
            ))
            i += 1
            continue

        groups.append(_MessageGroup(
            start=i, end=i + 1, messages=[msg],
            tokens=estimate_messages_tokens([msg]),
            protected=False,
        ))
        i += 1

    return groups


def _group_has_protected_tool(group: _MessageGroup) -> bool:
    return any(
        m.role == "assistant_tool_call" and _is_protected_tool_name(m.tool_name)
        for m in group.messages
    )


def _group_has_important_error(group: _MessageGroup) -> bool:
    return any(
        _message_text_looks_important_error(m)
        or (m.role == "tool_result" and _tool_result_looks_important_error(m))
        for m in group.messages
    )


def _add_protected_reason(group: _MessageGroup, reason: str) -> None:
    group.protected = True
    if reason not in group.reasons:
        group.reasons.append(reason)


def _protect_nearby_groups(groups: list[_MessageGroup], index: int, reason: str) -> None:
    for j in range(max(0, index - 1), min(len(groups), index + 2)):
        _add_protected_reason(groups[j], reason)


def _mark_protected_groups(groups: list[_MessageGroup], candidate_start: int, candidate_end: int) -> None:
    for group in groups:
        if group.start < candidate_start or group.end > candidate_end:
            _add_protected_reason(group, "outside_candidate_range")
            continue
        if any(_is_boundary_message(m) for m in group.messages):
            _add_protected_reason(group, "boundary_message")

    for i, group in enumerate(groups):
        if _group_has_protected_tool(group):
            _protect_nearby_groups(groups, i, "near_file_edit")
        if _group_has_important_error(group):
            _protect_nearby_groups(groups, i, "near_important_error")


def _find_candidate_range(messages: list[ChatMessage]) -> tuple[int, int, Optional[str]]:
    if len(messages) <= SNIP_KEEP_RECENT_MESSAGES + SNIP_MIN_MESSAGES_TO_REMOVE:
        return 0, 0, "too_few_messages"

    keep_recent = max(0, len(messages) - SNIP_KEEP_RECENT_MESSAGES)
    last_user = -1
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].role == "user":
            last_user = i
            break

    end = min(keep_recent, last_user if last_user >= 0 else len(messages))
    if end <= 0:
        return 0, 0, "no_middle_range"

    start = 0
    for i in range(end):
        if _is_boundary_message(messages[i]):
            start = i + 1

    if end - start < SNIP_MIN_MESSAGES_TO_REMOVE:
        return start, end, "candidate_range_too_small"

    return start, end, None


def _find_safe_runs(groups: list[_MessageGroup]) -> list[_SafeRun]:
    runs: list[_SafeRun] = []
    current: list[_MessageGroup] = []

    def flush():
        if not current:
            return
        first, last = current[0], current[-1]
        runs.append(_SafeRun(
            groups=current, start=first.start, end=last.end,
            messages_count=last.end - first.start,
            tokens=sum(g.tokens for g in current),
        ))
        current.clear()

    for group in groups:
        if group.protected:
            flush()
            continue
        current.append(group)
    flush()

    return runs


def _select_deletion_from_run(run: _SafeRun, desired_tokens: int) -> tuple[int, int, int, int]:
    tokens = 0
    end_group_idx = -1
    for i, group in enumerate(run.groups):
        tokens += group.tokens
        end_group_idx = i
        if tokens >= desired_tokens and group.end - run.start >= SNIP_MIN_MESSAGES_TO_REMOVE:
            break
    end_group = run.groups[max(0, end_group_idx)]
    return run.start, end_group.end, tokens, end_group.end - run.start


def _build_snip_boundary_content(removed_count: int, tokens_freed: int) -> str:
    return "\n".join([
        "[Snipped earlier conversation segment]",
        "",
        "A middle portion of the earlier conversation was removed to preserve context space.",
        "",
        "Removed range:",
        f"- messages: {removed_count}",
        f"- approximate tokens freed: {max(0, round(tokens_freed))}",
        "",
        "The recent conversation and active task context are preserved.",
    ])


def _build_anthropic_snip_boundary_text() -> str:
    return "\n".join([
        "[Snipped earlier conversation segment]",
        "",
        "A middle portion of the earlier conversation was removed to preserve context space.",
        "The recent conversation and active task context are preserved.",
    ])


def _build_boundary_message(removed_ids: list[str], removed_count: int, tokens_freed: int) -> ChatMessage:
    ts = int(time.time() * 1000)
    first = removed_ids[0] if removed_ids else "none"
    return ChatMessage.snip_boundary(
        content=_build_snip_boundary_content(removed_count, tokens_freed),
        removed_message_ids=removed_ids,
        removed_count=removed_count,
        tokens_freed=tokens_freed,
        timestamp=ts,
        id=f"snip-{ts}-{first}",
    )


def _no_snip_result(messages: list[ChatMessage], tokens_before: int, reason: str) -> SnipCompactResult:
    return SnipCompactResult(
        messages=messages, did_snip=False,
        tokens_before=tokens_before, tokens_after=tokens_before,
        tokens_freed=0, removed_message_ids=[], reason=reason,
    )


async def snip_compact_conversation(
    messages: list[ChatMessage],
    context_stats: Any,
    model_context_window: int,
) -> SnipCompactResult:
    trigger_tokens = context_stats.total_tokens
    tokens_before = estimate_messages_tokens(messages)
    effective_input = max(context_stats.effective_input or model_context_window, 1)
    utilization = trigger_tokens / effective_input

    if utilization < SNIP_COMPACT_THRESHOLD:
        return _no_snip_result(messages, tokens_before, "below_threshold")

    start, end, range_reason = _find_candidate_range(messages)
    if range_reason:
        return _no_snip_result(messages, tokens_before, range_reason)

    groups = _build_message_groups(messages)
    _mark_protected_groups(groups, start, end)

    safe_runs = [
        r for r in _find_safe_runs(groups)
        if r.messages_count >= SNIP_MIN_MESSAGES_TO_REMOVE and r.tokens >= SNIP_MIN_TOKENS_TO_FREE
    ]
    safe_runs.sort(key=lambda r: (-r.tokens, -r.messages_count, r.start))

    if not safe_runs:
        return _no_snip_result(messages, tokens_before, "no_safe_interval")

    best_run = safe_runs[0]
    target_tokens = int(effective_input * SNIP_TARGET_USAGE)
    desired = max(SNIP_MIN_TOKENS_TO_FREE, trigger_tokens - target_tokens)
    del_start, del_end, del_tokens, del_count = _select_deletion_from_run(best_run, desired)

    if del_count < SNIP_MIN_MESSAGES_TO_REMOVE:
        return _no_snip_result(messages, tokens_before, "below_min_messages")

    removed = messages[del_start:del_end]
    removed_ids = [_message_id(m, del_start + i) for i, m in enumerate(removed)]
    boundary = _build_boundary_message(removed_ids, len(removed), del_tokens)
    boundary_tokens = estimate_messages_tokens([boundary])
    estimated_freed = max(0, del_tokens - boundary_tokens)

    if estimated_freed < SNIP_MIN_TOKENS_TO_FREE:
        return _no_snip_result(messages, tokens_before, "below_min_tokens")

    stale_part = [mark_provider_usage_stale(m, "conversation was snip-compacted after this") for m in messages]

    new_messages = [
        *stale_part[:del_start],
        ChatMessage.snip_boundary(
            content=_build_snip_boundary_content(len(removed), estimated_freed),
            removed_message_ids=removed_ids,
            removed_count=len(removed),
            tokens_freed=estimated_freed,
            timestamp=int(time.time() * 1000),
        ),
        *stale_part[del_end:],
    ]

    tokens_after = token_count_with_estimation(new_messages).total_tokens
    tokens_freed = max(0, tokens_before - tokens_after)

    if tokens_after >= tokens_before:
        return _no_snip_result(messages, tokens_before, "no_token_reduction")

    return SnipCompactResult(
        messages=new_messages, did_snip=True,
        tokens_before=tokens_before, tokens_after=tokens_after,
        tokens_freed=tokens_freed, removed_message_ids=removed_ids,
        boundary_message=new_messages[del_start],
        reason="snipped_safe_middle_interval",
    )
