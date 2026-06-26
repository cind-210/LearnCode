"""
OpenAI-compatible API model adapter.

Supports OpenAI, DeepSeek, SiliconFlow, and other OpenAI-compatible providers.
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


def _to_openai_messages(messages: list[ChatMessage]) -> list[dict]:
    converted: list[dict] = []

    system_parts: list[str] = []
    for m in messages:
        if m.role == "system":
            system_parts.append(m.content)
            continue

    if system_parts:
        converted.append({"role": "system", "content": "\n\n".join(system_parts)})

    for m in messages:
        if m.role == "system":
            continue
        elif m.role == "user":
            converted.append({"role": "user", "content": m.content})
        elif m.role in ("assistant", "assistant_progress"):
            content = m.content or ""
            if m.role == "assistant_progress":
                content = f"<progress>\n{content}\n</progress>"
            converted.append({"role": "assistant", "content": content})
        elif m.role == "assistant_tool_call":
            converted.append({
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": m.tool_use_id,
                    "type": "function",
                    "function": {
                        "name": m.tool_name,
                        "arguments": json.dumps(m.input, ensure_ascii=False) if isinstance(m.input, dict) else str(m.input),
                    },
                }],
            })
        elif m.role == "assistant_thinking":
            for block in (m.blocks or []):
                if hasattr(block, 'thinking') and getattr(block, 'thinking', None):
                    converted.append({"role": "assistant", "content": f"[Thinking]\n{block.thinking}"})
        elif m.role == "tool_result":
            converted.append({
                "role": "tool",
                "tool_call_id": m.tool_use_id,
                "content": m.content,
            })
        elif m.role == "context_summary":
            converted.append({"role": "user", "content": f"[Context Summary]\n{m.content}"})
        elif m.role == "snip_boundary":
            converted.append({"role": "user", "content": "[Snipped earlier conversation segment]"})

    return converted


def _tools_to_openai(tools: list) -> list[dict]:
    result = []
    for t in tools:
        schema = t.input_schema or {}
        result.append({
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description or "",
                "parameters": schema if isinstance(schema, dict) else {"type": "object", "properties": {}},
            },
        })
    return result


class OpenAIModelAdapter(ModelAdapter):
    def __init__(self, tools: ToolRegistry, get_runtime_config=None):
        self._tools = tools
        self._get_runtime_config = get_runtime_config or load_runtime_config

    async def next(self, messages: list[ChatMessage]) -> AgentStep:
        runtime = await self._get_runtime_config()
        openai_msgs = _to_openai_messages(messages)
        url = runtime.base_url
        max_tokens = resolve_max_output_tokens(runtime.model, runtime.max_output_tokens)

        api_key = runtime.api_key or runtime.auth_token
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }

        body: dict[str, Any] = {
            "model": runtime.model,
            "messages": openai_msgs,
            "max_tokens": max_tokens,
            "stream": False,
        }

        if self._tools:
            body["tools"] = _tools_to_openai(self._tools.list())

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

        if not isinstance(data, dict):
            raise RuntimeError(f"Unexpected model response: {str(data)[:200]}")

        choice = (data.get("choices") or [{}])[0]
        finish_reason = choice.get("finish_reason", "stop")
        msg = choice.get("message", {})
        content = msg.get("content") or ""
        tool_calls_raw = msg.get("tool_calls") or []

        usage_raw = data.get("usage")
        usage = None
        if usage_raw:
            usage = ProviderUsage(
                input_tokens=usage_raw.get("prompt_tokens", 0),
                output_tokens=usage_raw.get("completion_tokens", 0),
                total_tokens=usage_raw.get("total_tokens", 0),
                source="openai",
            )

        if tool_calls_raw:
            tool_calls: list[ToolCall] = []
            for tc in tool_calls_raw:
                func = tc.get("function", {})
                try:
                    args = json.loads(func.get("arguments", "{}"))
                except (json.JSONDecodeError, TypeError):
                    args = func.get("arguments", "")
                tool_calls.append(ToolCall(
                    id=tc.get("id", ""),
                    tool_name=func.get("name", ""),
                    input=args,
                ))
            return AgentStep(
                type="tool_calls",
                calls=tool_calls,
                usage=usage,
                diagnostics=StepDiagnostics(
                    stop_reason=finish_reason,
                    block_types=["tool_calls"],
                ),
            )

        return AgentStep(
            type="assistant",
            content=content.strip(),
            usage=usage,
            diagnostics=StepDiagnostics(
                stop_reason=finish_reason,
                block_types=["text"],
            ),
        )