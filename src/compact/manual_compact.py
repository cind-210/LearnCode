"""
Manual compact: user-triggered conversation compression.

Mirrors src/compact/manual-compact.ts from the TypeScript version.
"""
from __future__ import annotations

from typing import Optional

from ..types import ChatMessage, CompressionResult, ModelAdapter
from .compact import compact_conversation
from .auto_compact import reset_auto_compact_state


async def manual_compact(
    messages: list[ChatMessage],
    model_adapter: ModelAdapter,
) -> Optional[CompressionResult]:
    result = await compact_conversation(messages, model_adapter)
    if result:
        reset_auto_compact_state()
    return result