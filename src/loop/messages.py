"""
Types and data models for the LearnCode agent loop.

Mirrors src/types.ts from the TypeScript version.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal, Optional, Protocol, Union

# ---------------------------------------------------------------------------
# Provider usage
# ---------------------------------------------------------------------------


@dataclass
class ProviderUsage:
    input_tokens: int
    output_tokens: int
    total_tokens: int
    source: str = ""


@dataclass
class ProviderUsageMetadata:
    provider_usage: Optional[ProviderUsage] = None
    usage_stale: bool = False
    usage_stale_reason: str = ""


class ProviderThinkingBlock:
    """A thinking block from the provider."""
    type: str  # 'thinking' | 'redacted_thinking'

    def __init__(self, type: str, **kwargs: Any):
        self.type = type
        self.__dict__.update(kwargs)


# ---------------------------------------------------------------------------
# Message types
# ---------------------------------------------------------------------------


class MessageIdentity:
    id: Optional[str] = None


@dataclass
class ChatMessage:
    role: str
    # Populated by sub-type
    content: str = ""
    blocks: Optional[list[ProviderThinkingBlock]] = None
    tool_use_id: str = ""
    tool_name: str = ""
    input: Any = None
    is_error: bool = False
    compressed_count: int = 0
    timestamp: int = 0
    removed_message_ids: list[str] = field(default_factory=list)
    cleared_message_ids: list[str] = field(default_factory=list)
    removed_count: int = 0
    tokens_freed: int = 0
    provider_usage: Optional[ProviderUsage] = None
    usage_stale: bool = False
    usage_stale_reason: str = ""
    id: Optional[str] = None

    @classmethod
    def system(cls, content: str, **kwargs: Any) -> ChatMessage:
        return cls(role="system", content=content, **kwargs)

    @classmethod
    def user(cls, content: str, **kwargs: Any) -> ChatMessage:
        return cls(role="user", content=content, **kwargs)

    @classmethod
    def assistant(cls, content: str, **kwargs: Any) -> ChatMessage:
        return cls(role="assistant", content=content, **kwargs)

    @classmethod
    def assistant_progress(cls, content: str, **kwargs: Any) -> ChatMessage:
        return cls(role="assistant_progress", content=content, **kwargs)

    @classmethod
    def assistant_thinking(cls, blocks: list[ProviderThinkingBlock], **kwargs: Any) -> ChatMessage:
        return cls(role="assistant_thinking", blocks=blocks, **kwargs)

    @classmethod
    def assistant_tool_call(cls, tool_use_id: str, tool_name: str, input: Any, **kwargs: Any) -> ChatMessage:
        return cls(role="assistant_tool_call", tool_use_id=tool_use_id, tool_name=tool_name, input=input, **kwargs)

    @classmethod
    def tool_result(cls, tool_use_id: str, tool_name: str, content: str, is_error: bool = False, **kwargs: Any) -> ChatMessage:
        return cls(role="tool_result", tool_use_id=tool_use_id, tool_name=tool_name, content=content, is_error=is_error, **kwargs)

    @classmethod
    def context_summary(cls, content: str, compressed_count: int, timestamp: int, **kwargs: Any) -> ChatMessage:
        return cls(role="context_summary", content=content, compressed_count=compressed_count, timestamp=timestamp, **kwargs)

    @classmethod
    def snip_boundary(cls, content: str, removed_message_ids: list[str], removed_count: int, tokens_freed: int, timestamp: int, **kwargs: Any) -> ChatMessage:
        return cls(role="snip_boundary", content=content, removed_message_ids=removed_message_ids, removed_count=removed_count, tokens_freed=tokens_freed, timestamp=timestamp, **kwargs)

    @classmethod
    def microcompact_boundary(cls, content: str, cleared_message_ids: list[str], removed_count: int, tokens_freed: int, timestamp: int, **kwargs: Any) -> ChatMessage:
        return cls(role="microcompact_boundary", content=content, cleared_message_ids=cleared_message_ids, removed_count=removed_count, tokens_freed=tokens_freed, timestamp=timestamp, **kwargs)

    @classmethod
    def todo_reminder(cls, content: str, **kwargs: Any) -> ChatMessage:
        return cls(role="todo_reminder", content=content, **kwargs)

    # Convenience aliases
    @classmethod
    def tool_call(cls, tool_use_id: str, tool_name: str, input: Any, **kwargs: Any) -> ChatMessage:
        return cls.assistant_tool_call(tool_use_id=tool_use_id, tool_name=tool_name, input=input, **kwargs)

    @classmethod
    def thinking(cls, blocks: list[ProviderThinkingBlock], **kwargs: Any) -> ChatMessage:
        return cls.assistant_thinking(blocks=blocks, **kwargs)

    @classmethod
    def progress(cls, content: str, **kwargs: Any) -> ChatMessage:
        return cls.assistant_progress(content=content, **kwargs)


# ---------------------------------------------------------------------------
# Tool call
# ---------------------------------------------------------------------------


@dataclass
class ToolCall:
    id: str
    tool_name: str
    input: Any


# ---------------------------------------------------------------------------
# Step diagnostics
# ---------------------------------------------------------------------------


@dataclass
class StepDiagnostics:
    stop_reason: Optional[str] = None
    block_types: Optional[list[str]] = None
    ignored_block_types: Optional[list[str]] = None


# ---------------------------------------------------------------------------
# Agent step
# ---------------------------------------------------------------------------


@dataclass
class AgentStep:
    type: str  # 'assistant' | 'tool_calls'
    content: str = ""
    kind: Optional[str] = None  # 'final' | 'progress'
    calls: Optional[list[ToolCall]] = None
    content_kind: Optional[str] = None
    thinking_blocks: Optional[list[ProviderThinkingBlock]] = None
    diagnostics: Optional[StepDiagnostics] = None
    usage: Optional[ProviderUsage] = None


# ---------------------------------------------------------------------------
# Model adapter protocol
# ---------------------------------------------------------------------------


class ModelAdapter(Protocol):
    async def next(
        self,
        messages: list[ChatMessage],
        on_delta: Optional[Callable[[dict[str, Any]], Awaitable[None]]] = None,
    ) -> AgentStep: ...


# ---------------------------------------------------------------------------
# Compression result
# ---------------------------------------------------------------------------


@dataclass
class CompressionResult:
    messages: list[ChatMessage]
    summary: ChatMessage  # context_summary
    removed_count: int
    tokens_before: int
    tokens_after: int = 0
    did_snip: bool = False
    removed_messages: list[ChatMessage] = field(default_factory=list)
