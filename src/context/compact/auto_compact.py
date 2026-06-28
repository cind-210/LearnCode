"""
Auto compact: LLM-based compression when context is critical.

Mirrors TypeScript compact/auto-compact.ts from the TypeScript version.
"""
from __future__ import annotations

import os
from typing import Optional

from ...loop.messages import ChatMessage, CompressionResult, ModelAdapter
from ..token_estimator import compute_context_stats
from ..model_context import get_model_context_window
from .compact import compact_conversation
from .constants import THRESHOLDS, LIMITS


class AutoCompactState:
    def __init__(self):
        self.consecutive_failures = 0
        self.disabled = False


_state = AutoCompactState()


def reset_auto_compact_state() -> None:
    _state.consecutive_failures = 0
    _state.disabled = False


def get_auto_compact_state() -> AutoCompactState:
    return _state


def _debug(msg: str) -> None:
    if os.environ.get("LEARN_CODE_DEBUG_AUTOCOMPACT") == "1":
        import sys
        print(f"[auto-compact] {msg}", file=sys.stderr)


def should_auto_compact(messages: list[ChatMessage], model: str) -> bool:
    stats = compute_context_stats(messages, model)
    should = stats.utilization >= THRESHOLDS["AUTOCOMPACT_UTILIZATION"]
    _debug(
        f"source={stats.accounting.source} total={stats.accounting.total_tokens} "
        f"utilization={stats.utilization:.3f} threshold={THRESHOLDS['AUTOCOMPACT_UTILIZATION']} "
        f"should={should}"
    )
    return should


async def auto_compact(
    messages: list[ChatMessage],
    model: str,
    model_adapter: ModelAdapter,
) -> Optional[CompressionResult]:
    if _state.disabled:
        return None

    window = get_model_context_window(model)
    if window.effective_input < LIMITS["MIN_EFFECTIVE_INPUT_FOR_AUTOCOMPACT"]:
        return None

    if not should_auto_compact(messages, model):
        return None

    try:
        result = await compact_conversation(messages, model_adapter)
        if result:
            _state.consecutive_failures = 0
            return result
        _state.consecutive_failures += 1
        if _state.consecutive_failures >= LIMITS["MAX_AUTOCOMPACT_FAILURES"]:
            _state.disabled = True
        return None
    except Exception:
        _state.consecutive_failures += 1
        if _state.consecutive_failures >= LIMITS["MAX_AUTOCOMPACT_FAILURES"]:
            _state.disabled = True
        return None
