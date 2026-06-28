"""
Compact summary prompts.

Mirrors TypeScript compact/prompt.ts from the TypeScript version.
"""
from __future__ import annotations

import re


def build_compact_summary_prompt(conversation_text: str) -> str:
    return f"""You are summarizing a conversation for context compression.
Produce a structured summary in <summary> tags.

Sections:
1. Primary Request — What the user asked for
2. Key Decisions — Important choices made
3. Files Modified — Which files were changed and why
4. Errors Encountered — Problems hit and how they were resolved
5. Current State — Where things stand right now
6. Pending Tasks — What still needs to be done

Rules:
- Be concise but preserve actionable details (file paths, command outputs, error messages)
- Use <analysis> tags as scratchpad, then <summary> tags for final output
- The summary will replace all messages before the recent tail

Conversation to summarize:

{conversation_text}"""


def parse_summary_from_response(response: str) -> Optional[str]:
    import re
    m = re.search(r"<summary>([\s\S]*?)</summary>", response)
    if m and m.group(1):
        return m.group(1).strip()

    m2 = re.search(r"<analysis>([\s\S]*?)</analysis>", response)
    if not m2:
        trimmed = response.strip()
        if trimmed:
            return trimmed
    return None