"""
Mock model adapter for offline testing.

Mirrors src/mock-model.ts from the TypeScript version.
"""
from __future__ import annotations

import time
from typing import Any, Awaitable, Callable, Optional

from ..loop.messages import AgentStep, ChatMessage, ModelAdapter, ToolCall


def _last_user_message(messages: list[ChatMessage]) -> str:
    for m in reversed(messages):
        if m.role == "user":
            return m.content
    return ""


def _last_tool_message(messages: list[ChatMessage]) -> Optional[ChatMessage]:
    for m in reversed(messages):
        if m.role == "tool_result":
            return m
    return None


def _extract_latest_assistant_call(messages: list[ChatMessage]) -> Optional[str]:
    for m in reversed(messages):
        if m.role == "assistant_tool_call":
            return m.tool_name
    return None


class MockModelAdapter(ModelAdapter):
    async def next(
        self,
        messages: list[ChatMessage],
        on_delta: Optional[Callable[[dict[str, Any]], Awaitable[None]]] = None,
    ) -> AgentStep:
        tool_msg = _last_tool_message(messages)
        if tool_msg and tool_msg.role == "tool_result":
            last_call = _extract_latest_assistant_call(messages)
            if last_call == "list_files":
                return AgentStep(type="assistant", content=f"目录内容如下：\n\n{tool_msg.content}")
            if last_call == "read_file":
                return AgentStep(type="assistant", content=f"文件内容如下：\n\n{tool_msg.content}")
            if last_call in ("write_file", "edit_file"):
                return AgentStep(type="assistant", content=tool_msg.content)
            return AgentStep(type="assistant", content=f"我拿到了工具结果：\n\n{tool_msg.content}")

        user_text = _last_user_message(messages).strip()
        ts = int(time.time() * 1000)

        if user_text == "/tools":
            return AgentStep(type="assistant", content="可用工具：ask_user, list_files, grep_files, read_file, write_file, edit_file, run_command")

        if user_text.startswith("/ls"):
            d = user_text.replace("/ls", "").strip()
            return AgentStep(type="tool_calls", calls=[ToolCall(id=f"mock-{ts}", tool_name="list_files", input={"path": d} if d else {})])

        if user_text.startswith("/grep "):
            payload = user_text[6:].strip()
            parts = payload.split("::", 1)
            return AgentStep(type="tool_calls", calls=[ToolCall(id=f"mock-{ts}", tool_name="grep_files", input={"pattern": parts[0].strip(), "path": parts[1].strip() if len(parts) > 1 else None})])

        if user_text.startswith("/read "):
            return AgentStep(type="tool_calls", calls=[ToolCall(id=f"mock-{ts}", tool_name="read_file", input={"path": user_text[6:].strip()})])

        if user_text.startswith("/cmd "):
            parts = user_text[5:].strip().split()
            return AgentStep(type="tool_calls", calls=[ToolCall(id=f"mock-{ts}", tool_name="run_command", input={"command": parts[0], "args": parts[1:]})])

        if user_text.startswith("/write "):
            payload = user_text[7:]
            split_at = payload.find("::")
            if split_at == -1:
                return AgentStep(type="assistant", content="用法: /write 路径::内容")
            return AgentStep(type="tool_calls", calls=[ToolCall(id=f"mock-{ts}", tool_name="write_file", input={"path": payload[:split_at].strip(), "content": payload[split_at + 2:]})])

        if user_text.startswith("/edit "):
            payload = user_text[6:]
            parts = payload.split("::", 2)
            if len(parts) < 3:
                return AgentStep(type="assistant", content="用法: /edit 路径::查找文本::替换文本")
            return AgentStep(type="tool_calls", calls=[ToolCall(id=f"mock-{ts}", tool_name="edit_file", input={"path": parts[0].strip(), "search": parts[1], "replace": parts[2]})])

        return AgentStep(type="assistant", content="\n".join([
            "这是一个最小骨架版本。",
            "你可以试试：",
            "/tools",
            "/ls",
            "/grep pattern::src",
            "/read README.md",
            "/cmd pwd",
            "/write notes.txt::hello",
            "/edit notes.txt::hello::hello world",
        ]))
