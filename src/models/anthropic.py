"""
Anthropic API model adapter.

Mirrors src/anthropic-adapter.ts from the TypeScript version.
"""
from __future__ import annotations

import asyncio
import json
import os
import random
from typing import Any, Awaitable, Callable, Optional

import httpx

from ..config.runtime import RuntimeConfig, load_runtime_config
from ..tools.registry import ToolRegistry
from ..loop.messages import (
    AgentStep,
    ChatMessage,
    ModelAdapter,
    ProviderThinkingBlock,
    ProviderUsage,
    StepDiagnostics,
    ToolCall,
)
from ..context.tool_limits import resolve_max_output_tokens


DEFAULT_MAX_RETRIES = 1
BASE_RETRY_DELAY_MS = 500
MAX_RETRY_DELAY_MS = 8_000


def _get_retry_limit() -> int:
    try:
        val = int(os.environ.get("LEARN_CODE_MAX_RETRIES", "0"))
        if val > 0:
            return val
    except (ValueError, TypeError):
        pass
    return DEFAULT_MAX_RETRIES


def _should_retry_status(status: int) -> bool:
    return status == 429 or (500 <= status < 600)


def _parse_retry_after_ms(retry_after: Optional[str]) -> Optional[int]:
    if not retry_after:
        return None
    try:
        return int(float(retry_after) * 1000)
    except (ValueError, TypeError):
        pass
    return None


def _get_retry_delay_ms(attempt: int, retry_after_ms: Optional[int]) -> int:
    if retry_after_ms is not None:
        return retry_after_ms
    base = min(BASE_RETRY_DELAY_MS * (2 ** max(0, attempt - 1)), MAX_RETRY_DELAY_MS)
    jitter = random.random() * 0.25 * base
    return int(base + jitter)


def _parse_assistant_text(content: str) -> tuple[str, Optional[str]]:
    trimmed = content.strip()
    if not trimmed:
        return "", None

    markers = [
        ("<final>", "final"),
        ("[FINAL]", "final"),
        ("<progress>", "progress"),
        ("[PROGRESS]", "progress"),
    ]
    import re
    for prefix, kind in markers:
        if trimmed.startswith(prefix):
            raw = trimmed[len(prefix):].strip()
            closing = re.compile(r"</progress>", re.IGNORECASE) if kind == "progress" else re.compile(r"</final>", re.IGNORECASE)
            return closing.sub("", raw).strip(), kind

    return trimmed, None


def _is_assistant_continuation(message: ChatMessage) -> bool:
    return message.role in ("assistant", "assistant_progress", "assistant_tool_call")


def _should_keep_thinking_message(messages: list[ChatMessage], index: int) -> bool:
    for later in messages[index + 1:]:
        if later.role == "assistant_thinking":
            continue
        return _is_assistant_continuation(later)
    return False


def _to_anthropic_messages(messages: list[ChatMessage]) -> tuple[str, list[dict]]:
    system = "\n\n".join(m.content for m in messages if m.role == "system")
    converted: list[dict] = []

    def _push(role: str, block: dict) -> None:
        if converted and converted[-1]["role"] == role:
            converted[-1]["content"].append(block)
        else:
            converted.append({"role": role, "content": [block]})

    for index, m in enumerate(messages):
        if m.role == "system":
            continue
        elif m.role == "user":
            _push("user", {"type": "text", "text": m.content})
        elif m.role == "assistant_thinking":
            if not _should_keep_thinking_message(messages, index):
                continue
            for block in (m.blocks or []):
                _push("assistant", block.__dict__)
        elif m.role in ("assistant", "assistant_progress"):
            prefix = "<progress>\n" if m.role == "assistant_progress" else ""
            suffix = "\n</progress>" if m.role == "assistant_progress" else ""
            _push("assistant", {"type": "text", "text": f"{prefix}{m.content}{suffix}"})
        elif m.role == "assistant_tool_call":
            _push("assistant", {"type": "tool_use", "id": m.tool_use_id, "name": m.tool_name, "input": m.input})
        elif m.role == "context_summary":
            _push("user", {"type": "text", "text": f"[Context Summary from earlier conversation]\n{m.content}"})
        elif m.role == "todo_reminder":
            _push("user", {"type": "text", "text": m.content})
        elif m.role == "snip_boundary":
            _push("user", {"type": "text", "text": (
                "[Snipped earlier conversation segment]\n\n"
                "A middle portion of the earlier conversation was removed to preserve context space.\n"
                "The recent conversation and active task context are preserved."
            )})
        elif m.role == "tool_result":
            _push("user", {"type": "tool_result", "tool_use_id": m.tool_use_id, "content": m.content, "is_error": m.is_error})

    return system, converted


def _normalize_anthropic_usage(usage: Optional[dict]) -> Optional[ProviderUsage]:
    if not usage:
        return None
    input_tokens = (usage.get("input_tokens", 0) or 0) + (usage.get("cache_creation_input_tokens", 0) or 0) + (usage.get("cache_read_input_tokens", 0) or 0)
    output_tokens = usage.get("output_tokens", 0) or 0
    total = input_tokens + output_tokens
    if total <= 0:
        return None
    return ProviderUsage(input_tokens=input_tokens, output_tokens=output_tokens, total_tokens=total, source="anthropic")


class AnthropicModelAdapter(ModelAdapter):
    def __init__(self, tools: ToolRegistry, get_runtime_config=None):
        self._tools = tools
        self._get_runtime_config = get_runtime_config or load_runtime_config

    async def next(
        self,
        messages: list[ChatMessage],
        on_delta: Optional[Callable[[dict[str, Any]], Awaitable[None]]] = None,
    ) -> AgentStep:
        runtime = await self._get_runtime_config()
        system, anthropic_msgs = _to_anthropic_messages(messages)
        url = runtime.base_url
        max_tokens = resolve_max_output_tokens(runtime.model, runtime.max_output_tokens)

        headers = {
            "content-type": "application/json",
            "anthropic-version": "2023-06-01",
        }
        if runtime.auth_token:
            headers["Authorization"] = f"Bearer {runtime.auth_token}"
        elif runtime.api_key:
            headers["x-api-key"] = runtime.api_key

        body = {
            "model": runtime.model,
            "system": system,
            "messages": anthropic_msgs,
            "tools": [
                {"name": t.name, "description": t.description, "input_schema": t.input_schema}
                for t in self._tools.list()
            ],
            "max_tokens": max_tokens,
            "stream": True,
        }

        max_retries = _get_retry_limit()
        response_status = None
        response_text = ""
        data = None
        for attempt in range(max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=120.0) as client:
                    async with client.stream("POST", url, headers=headers, json=body) as resp:
                        response_status = resp.status_code
                        if not resp.is_success:
                            raw = await resp.aread()
                            response_text = raw.decode("utf-8", errors="replace")
                            if not _should_retry_status(resp.status_code) or attempt >= max_retries:
                                break
                            retry_after = _parse_retry_after_ms(resp.headers.get("retry-after"))
                            await asyncio.sleep(_get_retry_delay_ms(attempt + 1, retry_after) / 1000)
                            continue

                        data = await self._read_stream(resp, on_delta=on_delta)
                        break
            except httpx.RequestError:
                if attempt >= max_retries:
                    raise
                await asyncio.sleep(_get_retry_delay_ms(attempt + 1, None) / 1000)

        if data is None and response_status is None:
            raise RuntimeError("Model request failed before receiving a response")

        if data is None:
            msg = response_text[:200] if response_text else f"HTTP {response_status}"
            raise RuntimeError(f"Model request failed: {response_status} - {msg}")

        tool_calls: list[ToolCall] = []
        text_parts: list[str] = []
        thinking_blocks: list[ProviderThinkingBlock] = []
        block_types: list[str] = []
        ignored_block_types: set[str] = set()

        for block in (data.get("content") or []):
            block_types.append(block.get("type", "unknown"))
            if block.get("type") == "text" and "text" in block:
                text_parts.append(block["text"])
            elif block.get("type") == "tool_use":
                tool_calls.append(ToolCall(
                    id=block.get("id", ""),
                    tool_name=block.get("name", ""),
                    input=block.get("input"),
                ))
            elif block.get("type") in ("thinking", "redacted_thinking"):
                thinking_blocks.append(ProviderThinkingBlock(**block))
            else:
                ignored_block_types.add(block.get("type", "unknown"))

        full_text = "\n".join(text_parts).strip()
        parsed_text, kind = _parse_assistant_text(full_text)
        diagnostics = StepDiagnostics(
            stop_reason=data.get("stop_reason"),
            block_types=block_types,
            ignored_block_types=list(ignored_block_types),
        )
        usage = _normalize_anthropic_usage(data.get("usage"))

        if tool_calls:
            return AgentStep(
                type="tool_calls",
                calls=tool_calls,
                content=parsed_text or None,
                content_kind="progress" if kind == "progress" else None,
                thinking_blocks=thinking_blocks,
                diagnostics=diagnostics,
                usage=usage,
            )

        return AgentStep(
            type="assistant",
            content=parsed_text,
            kind=kind,
            thinking_blocks=thinking_blocks,
            diagnostics=diagnostics,
            usage=usage,
        )

    async def _read_stream(
        self,
        resp: httpx.Response,
        on_delta: Optional[Callable[[dict[str, Any]], Awaitable[None]]] = None,
    ) -> dict[str, Any]:
        content_blocks: dict[int, dict[str, Any]] = {}
        stop_reason = None
        usage: dict[str, Any] = {}

        async for line in resp.aiter_lines():
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload or payload == "[DONE]":
                continue
            event = json.loads(payload)
            event_type = event.get("type")

            if event_type == "message_start":
                usage.update((event.get("message") or {}).get("usage") or {})
                continue

            if event_type == "content_block_start":
                index = int(event.get("index", 0) or 0)
                block = dict(event.get("content_block") or {})
                if block.get("type") == "tool_use":
                    block["_input_json"] = ""
                content_blocks[index] = block
                continue

            if event_type == "content_block_delta":
                index = int(event.get("index", 0) or 0)
                delta = event.get("delta") or {}
                block = content_blocks.setdefault(index, {"type": "text", "text": ""})
                delta_type = delta.get("type")
                if delta_type == "text_delta":
                    text = delta.get("text", "")
                    block["text"] = block.get("text", "") + text
                    if text and on_delta:
                        await on_delta({"type": "text", "text": text})
                elif delta_type == "input_json_delta":
                    block["_input_json"] = block.get("_input_json", "") + delta.get("partial_json", "")
                elif delta_type == "thinking_delta":
                    thinking = delta.get("thinking", "")
                    block["thinking"] = block.get("thinking", "") + thinking
                    if thinking and on_delta:
                        await on_delta({"type": "thinking", "thinking": thinking})
                elif delta_type == "signature_delta":
                    block["signature"] = block.get("signature", "") + delta.get("signature", "")
                continue

            if event_type == "content_block_stop":
                index = int(event.get("index", 0) or 0)
                block = content_blocks.get(index)
                if block and block.get("type") == "tool_use":
                    raw_input = block.pop("_input_json", "")
                    if raw_input:
                        block["input"] = json.loads(raw_input)
                    elif "input" not in block:
                        block["input"] = {}
                continue

            if event_type == "message_delta":
                delta = event.get("delta") or {}
                stop_reason = delta.get("stop_reason", stop_reason)
                usage.update(event.get("usage") or {})
                continue

        return {
            "content": [
                content_blocks[index]
                for index in sorted(content_blocks)
            ],
            "stop_reason": stop_reason,
            "usage": usage,
        }
