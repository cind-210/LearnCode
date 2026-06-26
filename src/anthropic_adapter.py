"""
Anthropic API model adapter.

Mirrors src/anthropic-adapter.ts from the TypeScript version.
"""
from __future__ import annotations

import asyncio
import json
import os
import random
from typing import Any, Optional

import httpx

from .config import RuntimeConfig, load_runtime_config
from .tool import ToolRegistry
from .types import (
    AgentStep,
    ChatMessage,
    ModelAdapter,
    ProviderThinkingBlock,
    ProviderUsage,
    StepDiagnostics,
    ToolCall,
)
from .utils.context import resolve_max_output_tokens


DEFAULT_MAX_RETRIES = 4
BASE_RETRY_DELAY_MS = 500
MAX_RETRY_DELAY_MS = 8_000


def _get_retry_limit() -> int:
    try:
        val = int(os.environ.get("MINI_CODE_MAX_RETRIES", "0"))
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


def _to_anthropic_messages(messages: list[ChatMessage]) -> tuple[str, list[dict]]:
    system = "\n\n".join(m.content for m in messages if m.role == "system")
    converted: list[dict] = []

    def _push(role: str, block: dict) -> None:
        if converted and converted[-1]["role"] == role:
            converted[-1]["content"].append(block)
        else:
            converted.append({"role": role, "content": [block]})

    for m in messages:
        if m.role == "system":
            continue
        elif m.role == "user":
            _push("user", {"type": "text", "text": m.content})
        elif m.role == "assistant_thinking":
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

    async def next(self, messages: list[ChatMessage]) -> AgentStep:
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
        }

        max_retries = _get_retry_limit()
        response = None
        data = None
        for attempt in range(max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=120.0) as client:
                    resp = await client.post(url, headers=headers, json=body)
                    try:
                        data = resp.json()
                    except Exception:
                        data = resp.text
                    if resp.is_success:
                        response = resp
                        break
                    if not _should_retry_status(resp.status_code) or attempt >= max_retries:
                        response = resp
                        break
                    retry_after = _parse_retry_after_ms(resp.headers.get("retry-after"))
                    await asyncio.sleep(_get_retry_delay_ms(attempt + 1, retry_after) / 1000)
            except httpx.RequestError:
                if attempt >= max_retries:
                    raise
                await asyncio.sleep(_get_retry_delay_ms(attempt + 1, None) / 1000)

        if response is None:
            raise RuntimeError("Model request failed before receiving a response")

        if not response.is_success:
            if isinstance(data, dict):
                msg = data.get("error", {}).get("message", str(response.status_code))
            else:
                msg = str(data)[:200] if data else f"HTTP {response.status_code}"
            raise RuntimeError(f"Model request failed: {response.status_code} - {msg}")

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
                thinking_blocks.append(ProviderThinkingBlock(type=block["type"]))
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