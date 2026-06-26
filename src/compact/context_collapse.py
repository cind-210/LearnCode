"""
Context collapse: compress older conversation into model-visible summaries.

Mirrors src/compact/context-collapse.ts from the TypeScript version.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Optional

from ..types import ChatMessage, ModelAdapter
from ..utils.token_estimator import (
    compute_context_stats,
    estimate_messages_tokens,
    mark_provider_usage_stale,
)
from .constants import (
    CONTEXT_COLLAPSE_KEEP_RECENT_MESSAGES,
    CONTEXT_COLLAPSE_MAX_FAILURES,
    CONTEXT_COLLAPSE_MAX_SPANS_PER_PASS,
    CONTEXT_COLLAPSE_MIN_TOKENS_TO_SAVE,
    CONTEXT_COLLAPSE_TARGET_USAGE,
    CONTEXT_COLLAPSE_UTILIZATION,
)
from .prompt import parse_summary_from_response

STALE_REASON = "conversation was context-collapsed in the model-visible projection after this provider usage was recorded"


@dataclass
class CollapseSpan:
    id: str
    start_message_id: str
    end_message_id: str
    message_ids: list[str]
    summary: str
    tokens_before: int
    tokens_after: int
    status: str  # 'staged' | 'committed'
    created_at: int
    reason: str  # 'context_pressure' | 'manual' | 'overflow_recovery'


@dataclass
class ContextCollapseState:
    spans: list[CollapseSpan] = field(default_factory=list)
    enabled: bool = True
    consecutive_failures: int = 0


@dataclass
class ContextCollapseOptions:
    utilization_threshold: float = CONTEXT_COLLAPSE_UTILIZATION
    target_usage: float = CONTEXT_COLLAPSE_TARGET_USAGE
    keep_recent_messages: int = CONTEXT_COLLAPSE_KEEP_RECENT_MESSAGES
    min_tokens_to_save: int = CONTEXT_COLLAPSE_MIN_TOKENS_TO_SAVE
    current_tokens: Optional[int] = None
    effective_input: Optional[int] = None
    max_spans_per_pass: int = CONTEXT_COLLAPSE_MAX_SPANS_PER_PASS
    max_failures: int = CONTEXT_COLLAPSE_MAX_FAILURES
    reason: str = "context_pressure"


@dataclass
class ContextCollapseResult:
    messages: list[ChatMessage]
    state: ContextCollapseState
    collapsed: bool
    spans: list[CollapseSpan] = field(default_factory=list)
    span: Optional[CollapseSpan] = None


def create_context_collapse_state() -> ContextCollapseState:
    return ContextCollapseState()


def _is_collapse_boundary(message: ChatMessage) -> bool:
    return message.role in ("system", "context_summary", "snip_boundary")


def _message_id(message: ChatMessage, index: int) -> str:
    return message.id or f"message-{index}"


def _estimate_collapse_summary_tokens(tokens_before: int) -> int:
    return max(128, int(tokens_before * 0.15 + 0.999))


def _build_collapsed_summary_content(span: CollapseSpan) -> str:
    return "\n".join([
        "[Collapsed context summary]",
        f"This summary replaces messages {span.start_message_id} through {span.end_message_id} in the model-visible context only.",
        "The original transcript is preserved in the session/UI.",
        "",
        span.summary,
    ])


def _build_collapsed_summary_message(span: CollapseSpan) -> ChatMessage:
    return ChatMessage.context_summary(
        content=_build_collapsed_summary_content(span),
        compressed_count=len(span.message_ids),
        timestamp=span.created_at,
        id=f"collapse-summary-{span.id}",
    )


def _project_span(messages: list[ChatMessage], span: CollapseSpan) -> Optional[tuple[int, int, ChatMessage]]:
    if span.status != "committed" or not span.message_ids:
        return None

    index_by_id = {_message_id(messages[i], i): i for i in range(len(messages))}
    indices = [index_by_id.get(mid) for mid in span.message_ids]
    if None in indices:
        return None

    indices = [i for i in indices if i is not None]
    for j in range(1, len(indices)):
        if indices[j] != indices[j - 1] + 1:
            return None

    start = indices[0]
    end = indices[-1] + 1
    if _message_id(messages[start], start) != span.start_message_id:
        return None
    if _message_id(messages[end - 1], end - 1) != span.end_message_id:
        return None

    return start, end, _build_collapsed_summary_message(span)


def project_collapsed_view(messages: list[ChatMessage], state: ContextCollapseState) -> list[ChatMessage]:
    if not state.enabled or not state.spans:
        return messages

    projections = [
        p for span in state.spans
        if (p := _project_span(messages, span)) is not None
    ]
    projections.sort(key=lambda p: p[0])

    if not projections:
        return messages

    result: list[ChatMessage] = []
    occupied = set()
    cursor = 0

    for proj_start, proj_end, proj_msg in projections:
        if any(i in occupied for i in range(proj_start, proj_end)):
            continue
        while cursor < proj_start:
            result.append(mark_provider_usage_stale(messages[cursor], STALE_REASON))
            cursor += 1
        result.append(proj_msg)
        for i in range(proj_start, proj_end):
            occupied.add(i)
        cursor = proj_end

    while cursor < len(messages):
        result.append(mark_provider_usage_stale(messages[cursor], STALE_REASON))
        cursor += 1

    return result


@dataclass
class _MessageGroup:
    start: int
    end: int
    messages: list[ChatMessage]
    tokens: int
    protected: bool = False


def _tool_group_is_closed(messages: list[ChatMessage]) -> bool:
    calls = {m.tool_use_id for m in messages if m.role == "assistant_tool_call"}
    results = {m.tool_use_id for m in messages if m.role == "tool_result"}
    if not calls and not results:
        return True
    if not calls or not results:
        return False
    return calls == results


def _build_message_groups(messages: list[ChatMessage]) -> list[_MessageGroup]:
    groups: list[_MessageGroup] = []
    i = 0
    while i < len(messages):
        msg = messages[i]

        if msg.role == "assistant_thinking":
            grouped = [msg]
            cursor = i + 1
            while cursor < len(messages) and messages[cursor].role == "assistant_tool_call":
                grouped.append(messages[cursor])
                cursor += 1
            while cursor < len(messages) and messages[cursor].role == "tool_result":
                grouped.append(messages[cursor])
                cursor += 1
            has_tool = any(m.role == "assistant_tool_call" for m in grouped)
            groups.append(_MessageGroup(
                start=i, end=cursor, messages=grouped,
                tokens=estimate_messages_tokens(grouped),
                protected=has_tool and not _tool_group_is_closed(grouped),
            ))
            i = cursor
            continue

        if msg.role == "assistant_tool_call":
            grouped = []
            cursor = i
            while cursor < len(messages) and messages[cursor].role == "assistant_tool_call":
                grouped.append(messages[cursor])
                cursor += 1
            while cursor < len(messages) and messages[cursor].role == "tool_result":
                grouped.append(messages[cursor])
                cursor += 1
            groups.append(_MessageGroup(
                start=i, end=cursor, messages=grouped,
                tokens=estimate_messages_tokens(grouped),
                protected=not _tool_group_is_closed(grouped),
            ))
            i = cursor
            continue

        if msg.role == "tool_result":
            groups.append(_MessageGroup(
                start=i, end=i + 1, messages=[msg],
                tokens=estimate_messages_tokens([msg]),
                protected=True,
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


def _committed_collapsed_message_ids(state: ContextCollapseState) -> set[str]:
    ids = set()
    for span in state.spans:
        if span.status in ("committed", "staged"):
            for mid in span.message_ids:
                ids.add(mid)
    return ids


def _desired_tokens_to_save(options: ContextCollapseOptions) -> int:
    if options.current_tokens is not None and options.effective_input is not None and options.effective_input > 0:
        return max(options.min_tokens_to_save, int(options.current_tokens - options.effective_input * options.target_usage))
    return options.min_tokens_to_save


@dataclass
class _CollapseCandidate:
    start_index: int
    end_index: int
    start_message_id: str
    end_message_id: str
    message_ids: list[str]
    messages: list[ChatMessage]
    tokens_before: int
    estimated_tokens_after: int
    estimated_tokens_to_save: int


def _build_candidate_from_groups(
    messages: list[ChatMessage],
    groups: list[_MessageGroup],
    options: ContextCollapseOptions,
) -> Optional[_CollapseCandidate]:
    desired = _desired_tokens_to_save(options)
    tokens = 0
    end_group_idx = -1

    for i, group in enumerate(groups):
        tokens += group.tokens
        est_after = _estimate_collapse_summary_tokens(tokens)
        est_save = max(0, tokens - est_after)
        end_group_idx = i
        if est_save >= desired:
            break

    if end_group_idx < 0:
        return None

    selected = groups[:end_group_idx + 1]
    first, last = selected[0], selected[-1]
    selected_msgs = messages[first.start:last.end]
    msg_ids = [_message_id(selected_msgs[j], first.start + j) for j in range(len(selected_msgs))]
    est_after = _estimate_collapse_summary_tokens(tokens)
    est_save = max(0, tokens - est_after)

    if est_save < options.min_tokens_to_save:
        return None

    return _CollapseCandidate(
        start_index=first.start, end_index=last.end,
        start_message_id=msg_ids[0], end_message_id=msg_ids[-1],
        message_ids=msg_ids, messages=selected_msgs,
        tokens_before=tokens, estimated_tokens_after=est_after,
        estimated_tokens_to_save=est_save,
    )


def find_collapse_candidate(
    messages: list[ChatMessage],
    state: ContextCollapseState,
    raw_options: Optional[ContextCollapseOptions] = None,
) -> Optional[_CollapseCandidate]:
    options = raw_options or ContextCollapseOptions()
    if not messages:
        return None

    last_user_idx = -1
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].role == "user":
            last_user_idx = i
            break

    keep_recent = max(0, len(messages) - options.keep_recent_messages)
    protected_start = min(keep_recent, last_user_idx if last_user_idx >= 0 else len(messages))
    if protected_start <= 0:
        return None

    collapsed_ids = _committed_collapsed_message_ids(state)
    groups = _build_message_groups(messages)

    safe_runs: list[list[_MessageGroup]] = []
    current_run: list[_MessageGroup] = []

    for group in groups:
        protected = (
            group.protected
            or group.end > protected_start
            or any(_is_collapse_boundary(m) for m in group.messages)
            or any(_message_id(m, group.start + j) in collapsed_ids for j, m in enumerate(group.messages))
        )
        if protected:
            if current_run:
                safe_runs.append(current_run)
                current_run = []
            continue
        current_run.append(group)

    if current_run:
        safe_runs.append(current_run)

    for run in safe_runs:
        candidate = _build_candidate_from_groups(messages, run, options)
        if candidate:
            return candidate

    return None


def _message_to_collapse_text(message: ChatMessage) -> str:
    if message.role == "user":
        return f"[User]: {message.content}"
    if message.role in ("assistant", "assistant_progress"):
        return f"[Assistant]: {message.content}"
    if message.role == "assistant_thinking":
        return "[Assistant Thinking]: preserved provider reasoning block"
    if message.role == "assistant_tool_call":
        return f"[Tool Call: {message.tool_name}]: {json.dumps(message.input)}"
    if message.role == "tool_result":
        c = message.content[:500] + "... (truncated)" if len(message.content) > 500 else message.content
        err = " ERROR" if message.is_error else ""
        return f"[Tool Result: {message.tool_name}{err}]: {c}"
    if message.role == "context_summary":
        return f"[Previous Summary]: {message.content}"
    if message.role == "snip_boundary":
        return f"[Snipped Context Boundary]: {message.content}"
    return ""


async def _summarize_candidate(
    candidate: _CollapseCandidate,
    model_adapter: ModelAdapter,
    reason: str,
) -> Optional[CollapseSpan]:
    text = "\n\n".join(_message_to_collapse_text(m) for m in candidate.messages)
    prompt = (
        "You are summarizing a conversation segment for context compression.\n\n"
        "Produce a structured summary in <summary> tags.\n\n"
        "Sections:\n"
        "1. Primary Request — What the user asked for\n"
        "2. Key Decisions — Important choices made\n"
        "3. Files Modified — Which files were changed and why\n"
        "4. Errors Encountered — Problems hit and how they were resolved\n"
        "5. Current State — Where things stand right now\n"
        "6. Pending Tasks — What still needs to be done\n\n"
        "Rules:\n"
        "- Be concise but preserve actionable details\n"
        "- The summary will replace these messages in the model-visible context\n\n"
        f"Conversation to summarize:\n\n{text}"
    )

    req: list[ChatMessage] = [
        ChatMessage.system("You are a helpful assistant that summarizes conversations concisely."),
        ChatMessage.user(prompt),
    ]

    try:
        resp = await model_adapter.next(req)
        if resp.type != "assistant" or not resp.content.strip():
            return None
        summary = parse_summary_from_response(resp.content)
        if not summary:
            return None

        span_id = f"collapse-{int(time.time() * 1000)}-{candidate.start_message_id}"
        return CollapseSpan(
            id=span_id,
            start_message_id=candidate.start_message_id,
            end_message_id=candidate.end_message_id,
            message_ids=candidate.message_ids,
            summary=summary,
            tokens_before=candidate.tokens_before,
            tokens_after=candidate.estimated_tokens_after,
            status="staged",
            created_at=int(time.time() * 1000),
            reason=reason,
        )
    except Exception:
        return None


async def apply_context_collapse_if_needed(
    messages: list[ChatMessage],
    model: str,
    model_adapter: ModelAdapter,
    state: ContextCollapseState,
    raw_options: Optional[ContextCollapseOptions] = None,
) -> ContextCollapseResult:
    options = raw_options or ContextCollapseOptions()

    if not state.enabled:
        return ContextCollapseResult(messages=project_collapsed_view(messages, state), state=state, collapsed=False)

    stats = compute_context_stats(messages, model)
    options.current_tokens = stats.total_tokens
    options.effective_input = stats.effective_input

    if stats.utilization < options.utilization_threshold and options.reason != "manual":
        return ContextCollapseResult(messages=project_collapsed_view(messages, state), state=state, collapsed=False)

    new_spans: list[CollapseSpan] = []
    working_messages = list(messages)

    for _ in range(options.max_spans_per_pass):
        candidate = find_collapse_candidate(working_messages, state, options)
        if not candidate:
            break

        span = await _summarize_candidate(candidate, model_adapter, options.reason)
        if not span:
            state.consecutive_failures += 1
            if state.consecutive_failures >= options.max_failures:
                state.enabled = False
            break

        span.status = "committed"
        state.spans.append(span)
        new_spans.append(span)
        state.consecutive_failures = 0

        summary_msg = _build_collapsed_summary_message(span)
        working_messages = [
            *working_messages[:candidate.start_index],
            summary_msg,
            *working_messages[candidate.end_index:],
        ]

    if new_spans:
        return ContextCollapseResult(
            messages=project_collapsed_view(messages, state),
            state=state,
            collapsed=True,
            spans=new_spans,
            span=new_spans[0] if len(new_spans) == 1 else None,
        )

    return ContextCollapseResult(
        messages=project_collapsed_view(messages, state),
        state=state,
        collapsed=False,
    )