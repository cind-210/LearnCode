"""
Tool result storage and truncation.

Mirrors src/utils/tool-result-storage.ts from the TypeScript version.
"""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from typing import Optional

DEFAULT_MAX_RESULT_SIZE_CHARS = 50_000
PREVIEW_SIZE_CHARS = 2_000

PERSISTED_OUTPUT_TAG = "<persisted-output>"
PERSISTED_OUTPUT_CLOSING_TAG = "</persisted-output>"


@dataclass
class ContentReplacementState:
    seen_ids: set[str] = field(default_factory=set)
    replacements: dict[str, str] = field(default_factory=dict)


def _sanitize(value: str) -> str:
    import re
    sanitized = re.sub(r"[^a-zA-Z0-9._-]", "_", value)
    return sanitized if sanitized else uuid.uuid4().hex[:12]


def _get_tool_results_dir() -> str:
    learncode_dir = os.path.join(os.path.expanduser("~"), ".learncode")
    return os.path.join(learncode_dir, "tool-results")


def _get_tool_result_path(tool_use_id: str) -> str:
    dir_path = _get_tool_results_dir()
    os.makedirs(dir_path, exist_ok=True)
    return os.path.join(dir_path, f"{_sanitize(tool_use_id)}.txt")


def _is_already_persisted(content: str) -> bool:
    return content.startswith(PERSISTED_OUTPUT_TAG)


def _generate_preview(content: str) -> tuple[str, bool]:
    if len(content) <= PREVIEW_SIZE_CHARS:
        return content, False
    truncated = content[:PREVIEW_SIZE_CHARS]
    last_newline = truncated.rfind("\n")
    cut = last_newline if last_newline > PREVIEW_SIZE_CHARS * 0.5 else PREVIEW_SIZE_CHARS
    return content[:cut], True


def _format_chars(chars: int) -> str:
    if chars >= 1_000_000:
        return f"{chars / 1_000_000:.1f}M chars"
    if chars >= 1_000:
        return f"{chars // 1_000}K chars"
    return f"{chars} chars"


def _persist_tool_result(content: str, tool_use_id: str) -> Optional[dict]:
    filepath = _get_tool_result_path(tool_use_id)
    try:
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        if not os.path.exists(filepath):
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)
    except OSError:
        return None

    preview, has_more = _generate_preview(content)
    return {
        "filepath": filepath,
        "original_size": len(content),
        "preview": preview,
        "has_more": has_more,
    }


def _build_persisted_message(result: dict) -> str:
    parts = [
        PERSISTED_OUTPUT_TAG,
        f"Output too large ({_format_chars(result['original_size'])}). "
        f"Full output saved to: {result['filepath']}",
        "",
        f"Preview (first {_format_chars(PREVIEW_SIZE_CHARS)}):",
        result["preview"],
    ]
    if result["has_more"]:
        parts.append("...")
    parts.append(PERSISTED_OUTPUT_CLOSING_TAG)
    return "\n".join(parts)


def replace_large_tool_result(
    tool_use_id: str,
    tool_name: str,
    content: str,
    state: Optional[ContentReplacementState] = None,
    threshold: int = DEFAULT_MAX_RESULT_SIZE_CHARS,
) -> str:
    if state is None:
        state = ContentReplacementState()

    previous = state.replacements.get(tool_use_id)
    if previous is not None:
        return previous

    if content.strip() == "":
        state.seen_ids.add(tool_use_id)
        return f"({tool_name} completed with no output)"

    if _is_already_persisted(content):
        state.seen_ids.add(tool_use_id)
        state.replacements[tool_use_id] = content
        return content

    if len(content) <= threshold:
        return content

    persisted = _persist_tool_result(content, tool_use_id)
    if not persisted:
        return content

    replacement = _build_persisted_message(persisted)
    state.seen_ids.add(tool_use_id)
    state.replacements[tool_use_id] = replacement
    return replacement
