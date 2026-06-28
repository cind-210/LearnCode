"""
Context resolution utilities (max output tokens, compactable tools).

Mirrors src/utils/context.ts from the TypeScript version.
"""
from __future__ import annotations

from typing import Optional


COMPACTABLE_TOOLS: set[str] = {
    "read_file",
    "run_command",
    "grep_files",
    "list_files",
    "web_fetch",
}


class _ModelMaxOutputTokens:
    def __init__(self, default: int, upper_limit: int):
        self.default = default
        self.upper_limit = upper_limit


class _ModelMaxOutputTokenRule:
    def __init__(self, patterns: list[str], limits: _ModelMaxOutputTokens):
        self.patterns = patterns
        self.limits = limits


_UNKNOWN_MODEL_MAX_OUTPUT_TOKENS = _ModelMaxOutputTokens(32_000, 64_000)

_MODEL_MAX_OUTPUT_TOKEN_RULES: list[_ModelMaxOutputTokenRule] = [
    _ModelMaxOutputTokenRule(["claude-opus-4-6", "claude opus 4.6", "opus-4-6"], _ModelMaxOutputTokens(128_000, 128_000)),
    _ModelMaxOutputTokenRule(["claude-sonnet-4-6", "claude sonnet 4.6", "sonnet-4-6"], _ModelMaxOutputTokens(64_000, 64_000)),
    _ModelMaxOutputTokenRule(["claude-haiku-4-5", "claude haiku 4.5", "haiku-4-5"], _ModelMaxOutputTokens(64_000, 64_000)),
    _ModelMaxOutputTokenRule(["claude-opus-4-1", "claude opus 4.1", "opus-4-1", "claude-opus-4", "claude opus 4", "opus-4"], _ModelMaxOutputTokens(32_000, 32_000)),
    _ModelMaxOutputTokenRule(["claude-sonnet-4", "claude sonnet 4", "sonnet-4"], _ModelMaxOutputTokens(64_000, 64_000)),
    _ModelMaxOutputTokenRule(["claude-3-7-sonnet", "claude 3.7 sonnet", "3-7-sonnet"], _ModelMaxOutputTokens(8_192, 8_192)),
    _ModelMaxOutputTokenRule(["claude-3-5-sonnet", "claude 3.5 sonnet", "3-5-sonnet", "claude-3-sonnet"], _ModelMaxOutputTokens(8_192, 8_192)),
    _ModelMaxOutputTokenRule(["claude-3-5-haiku", "claude 3.5 haiku", "3-5-haiku"], _ModelMaxOutputTokens(8_192, 8_192)),
    _ModelMaxOutputTokenRule(["claude-3-opus", "claude 3 opus"], _ModelMaxOutputTokens(4_096, 4_096)),
    _ModelMaxOutputTokenRule(["claude-3-haiku", "claude 3 haiku"], _ModelMaxOutputTokens(4_096, 4_096)),
    _ModelMaxOutputTokenRule(["gpt-5-codex", "gpt-5.4", "gpt-5.2", "gpt-5.1", "gpt-5"], _ModelMaxOutputTokens(128_000, 128_000)),
    _ModelMaxOutputTokenRule(["o4-mini", "o3", "o1-pro", "o1"], _ModelMaxOutputTokens(100_000, 100_000)),
    _ModelMaxOutputTokenRule(["gpt-4.1-mini", "gpt-4.1-nano", "gpt-4.1"], _ModelMaxOutputTokens(32_768, 32_768)),
    _ModelMaxOutputTokenRule(["gpt-4o-mini", "gpt-4o"], _ModelMaxOutputTokens(16_384, 16_384)),
    _ModelMaxOutputTokenRule(["gpt-4"], _ModelMaxOutputTokens(8_192, 8_192)),
    _ModelMaxOutputTokenRule(["gemini-2.5-pro", "gemini 2.5 pro", "gemini-2.5-flash-lite", "gemini 2.5 flash-lite", "gemini-2.5-flash", "gemini 2.5 flash"], _ModelMaxOutputTokens(65_536, 65_536)),
    _ModelMaxOutputTokenRule(["deepseek-reasoner"], _ModelMaxOutputTokens(32_000, 64_000)),
    _ModelMaxOutputTokenRule(["deepseek-chat"], _ModelMaxOutputTokens(4_000, 8_000)),
]


def get_model_max_output_tokens(model: str) -> _ModelMaxOutputTokens:
    normalized = model.strip().lower()
    for rule in _MODEL_MAX_OUTPUT_TOKEN_RULES:
        if any(pattern in normalized for pattern in rule.patterns):
            return rule.limits
    return _UNKNOWN_MODEL_MAX_OUTPUT_TOKENS


def resolve_max_output_tokens(model: str, configured_max_output_tokens: Optional[int] = None) -> int:
    limits = get_model_max_output_tokens(model)
    if configured_max_output_tokens is not None and configured_max_output_tokens > 0:
        return min(configured_max_output_tokens, limits.upper_limit)
    return limits.default
