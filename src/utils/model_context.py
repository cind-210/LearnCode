"""
Model context window definitions.

Mirrors src/utils/model-context.ts from the TypeScript version.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ModelContextWindow:
    context_window: int
    output_reserve: int
    effective_input: int


@dataclass
class _ModelContextRule:
    patterns: list[str]
    context_window: int
    output_reserve: int


_UNKNOWN_MODEL_CONTEXT = _ModelContextRule(
    patterns=[],
    context_window=128_000,
    output_reserve=8_000,
)

_MODEL_CONTEXT_RULES: list[_ModelContextRule] = [
    _ModelContextRule(["claude-opus-4-6", "claude opus 4.6", "opus-4-6"], 200_000, 16_000),
    _ModelContextRule(["claude-sonnet-4-6", "claude sonnet 4.6", "sonnet-4-6"], 200_000, 16_000),
    _ModelContextRule(["claude-haiku-4-5", "claude haiku 4.5", "haiku-4-5"], 200_000, 16_000),
    _ModelContextRule(["claude-opus-4-1", "claude opus 4.1", "opus-4-1", "claude-opus-4", "claude opus 4", "opus-4"], 200_000, 16_000),
    _ModelContextRule(["claude-sonnet-4", "claude sonnet 4", "sonnet-4"], 200_000, 16_000),
    _ModelContextRule(["claude-3-7-sonnet", "claude 3.7 sonnet", "3-7-sonnet"], 200_000, 8_192),
    _ModelContextRule(["claude-3-5-sonnet", "claude 3.5 sonnet", "3-5-sonnet", "claude-3-sonnet"], 200_000, 8_192),
    _ModelContextRule(["claude-3-5-haiku", "claude 3.5 haiku", "3-5-haiku"], 200_000, 8_192),
    _ModelContextRule(["claude-3-opus", "claude 3 opus"], 200_000, 4_096),
    _ModelContextRule(["claude-3-haiku", "claude 3 haiku"], 200_000, 4_096),
    _ModelContextRule(["gpt-5-codex", "gpt-5.4", "gpt-5.2", "gpt-5.1", "gpt-5"], 128_000, 16_000),
    _ModelContextRule(["o4-mini", "o3", "o1-pro", "o1"], 200_000, 16_000),
    _ModelContextRule(["gpt-4.1-mini", "gpt-4.1-nano", "gpt-4.1"], 1_047_576, 16_000),
    _ModelContextRule(["gpt-4o-mini", "gpt-4o"], 128_000, 16_384),
    _ModelContextRule(["gpt-4"], 128_000, 8_192),
    _ModelContextRule(["gemini-2.5-pro", "gemini 2.5 pro"], 1_048_576, 16_000),
    _ModelContextRule(["gemini-2.5-flash-lite", "gemini 2.5 flash-lite"], 1_048_576, 16_000),
    _ModelContextRule(["gemini-2.5-flash", "gemini 2.5 flash"], 1_048_576, 16_000),
    _ModelContextRule(["deepseek-reasoner"], 128_000, 16_000),
    _ModelContextRule(["deepseek-chat"], 128_000, 4_000),
]


def get_model_context_window(model: str) -> ModelContextWindow:
    normalized = model.strip().lower()
    for rule in _MODEL_CONTEXT_RULES:
        if any(pattern in normalized for pattern in rule.patterns):
            return ModelContextWindow(
                context_window=rule.context_window,
                output_reserve=rule.output_reserve,
                effective_input=rule.context_window - rule.output_reserve,
            )
    return ModelContextWindow(
        context_window=_UNKNOWN_MODEL_CONTEXT.context_window,
        output_reserve=_UNKNOWN_MODEL_CONTEXT.output_reserve,
        effective_input=_UNKNOWN_MODEL_CONTEXT.context_window - _UNKNOWN_MODEL_CONTEXT.output_reserve,
    )