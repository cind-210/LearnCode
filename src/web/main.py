"""
FastAPI + WebSocket web application for LearnCode.

Provides the web frontend and API for the agent.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import dataclass
from typing import Any, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from ..loop.runner import (
    AgentLoopConfig,
    AgentLoopState,
    extract_todos_from_messages,
    run_agent_loop,
    run_manual_compact,
)
from ..models.anthropic import AnthropicModelAdapter
from ..models.openai import OpenAIModelAdapter
from ..sessions.subsessions import SubSessionRuntime
from ..sessions.characters import ensure_general_purpose_character, load_characters, save_character
from ..config.runtime import load_runtime_config
from ..mcp.client import build_mcp_registry
from ..tools.permissions import PermissionDecision, PermissionMode, PermissionRequest, PermissionResponse
from ..tools.permissions import PermissionRules
from ..sessions.store import (
    append_compact_boundary,
    cleanup_expired_sessions,
    create_session,
    delete_session,
    fork_session,
    list_sessions,
    load_session,
    load_transcript,
    rename_session,
    save_session,
    update_session_permissions,
    validate_session_name,
    Session,
)
from ..tools.builtin import build_builtin_registry
from ..tools.subsession_tools import build_subsession_tools
from ..tools.registry import ToolRegistry
from ..loop.messages import ChatMessage, ModelAdapter

app = FastAPI(title="LearnCode", version="1.0.0")

PROJECT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
STATIC_DIR = os.path.join(PROJECT_DIR, "static")
SESSION_DIR = os.path.join(PROJECT_DIR, ".sessions")
APP_WORKSPACE = os.path.abspath(os.environ.get("WORKSPACE", os.getcwd()))


def _error_payload(error: BaseException) -> dict[str, str]:
    message = str(error) or error.__class__.__name__
    return {
        "type": error.__class__.__name__,
        "message": message,
    }


def _log_error(error: BaseException) -> None:
    payload = _error_payload(error)
    print(f"{payload['type']}: {payload['message']}", file=sys.stderr)


def _close_open_tool_calls(messages: list[ChatMessage]) -> None:
    open_calls: dict[str, ChatMessage] = {}
    for message in messages:
        if message.role == "assistant_tool_call" and message.tool_use_id:
            open_calls[message.tool_use_id] = message
        elif message.role == "tool_result" and message.tool_use_id:
            open_calls.pop(message.tool_use_id, None)

    for call in open_calls.values():
        messages.append(ChatMessage.tool_result(
            tool_use_id=call.tool_use_id,
            tool_name=call.tool_name,
            content="",
            is_error=True,
        ))


def _session_todos(session: Optional[Session]) -> list[dict[str, Any]]:
    return extract_todos_from_messages(session.messages) if session else []


def _subsession_payload(runtime: SubSessionRuntime, parent_session_id: str) -> list[dict[str, Any]]:
    loaded = {
        item.link.id: item
        for item in runtime.list_sessions(parent_session_id)
    }
    payload = []
    for link in runtime.list_links(SESSION_DIR, parent_session_id):
        loaded_item = loaded.get(link.id)
        payload.append({
            "id": link.id,
            "name": link.name,
            "description": link.description,
            "character": link.character,
            "updated_at": link.updated_at,
            "status": loaded_item.status if loaded_item else "idle-on-disk",
            "parent_session_id": parent_session_id,
        })
    return payload


def _characters_payload() -> list[dict[str, str]]:
    return [
        {
            "name": character.name,
            "description": character.description,
        }
        for character in load_characters()
    ]


def _validate_ai_session_title(title: str) -> Optional[str]:
    return validate_session_name(title, "title")


async def _get_model_adapter(tools: Optional[ToolRegistry] = None) -> ModelAdapter:
    tools = tools or build_builtin_registry()
    runtime = await load_runtime_config()
    if runtime.provider == "openai":
        return OpenAIModelAdapter(tools)
    return AnthropicModelAdapter(tools)


def _mcp_config_key(runtime: Any) -> str:
    data = {
        name: cfg.__dict__
        for name, cfg in sorted(runtime.mcp_servers.items())
    }
    return json.dumps(data, ensure_ascii=False, sort_keys=True, default=str)


@dataclass
class WebAppState:
    tool_registry: Optional[ToolRegistry] = None
    mcp_config_key: str = ""
    mcp_servers: Optional[list[dict[str, Any]]] = None
    subsession_runtime: Optional[SubSessionRuntime] = None

    def __post_init__(self) -> None:
        if self.subsession_runtime is None:
            self.subsession_runtime = SubSessionRuntime()

    async def ensure_tools(self) -> ToolRegistry:
        runtime = await load_runtime_config()
        next_key = _mcp_config_key(runtime)
        if self.tool_registry is not None and self.mcp_config_key == next_key:
            return self.tool_registry

        await self.close()
        builtin_registry = build_builtin_registry()
        builtin_registry.add_tools(build_subsession_tools(load_characters()))
        mcp_registry = None
        if runtime.mcp_servers:
            mcp_registry, _ = await build_mcp_registry(runtime.mcp_servers)

        if mcp_registry is None:
            self.tool_registry = builtin_registry
            self.mcp_servers = []
        else:
            self.tool_registry = builtin_registry.merge(mcp_registry)
            self.mcp_servers = [server.__dict__ for server in mcp_registry.get_mcp_servers()]
        self.mcp_config_key = next_key
        return self.tool_registry

    async def close(self) -> None:
        if self.tool_registry is not None:
            await self.tool_registry.dispose()
        self.tool_registry = None
        self.mcp_servers = []
        self.mcp_config_key = ""


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/workspace")
async def api_workspace():
    return {"workspace": APP_WORKSPACE}


@app.get("/api/sessions")
async def api_list_sessions():
    cleanup_expired_sessions(SESSION_DIR)
    sessions = list_sessions(SESSION_DIR)
    return [{"id": s.id, "title": s.title, "created_at": s.created_at, "updated_at": s.updated_at, "message_count": s.message_count, "workspace": s.workspace} for s in sessions]


@app.post("/api/sessions")
async def api_create_session(data: dict[str, Any]):
    title = data.get("title", "New Session")
    session = create_session(SESSION_DIR, workspace=APP_WORKSPACE, title=title)
    save_session(SESSION_DIR, session)
    return {"id": session.meta.id, "title": session.meta.title}


@app.delete("/api/sessions/{session_id}")
async def api_delete_session(session_id: str):
    ok = delete_session(SESSION_DIR, session_id)
    return {"ok": ok}


@app.get("/api/sessions/{session_id}")
async def api_get_session(session_id: str):
    session = load_session(SESSION_DIR, session_id)
    if session is None:
        return {"error": "Session not found"}
    return {
        "id": session.meta.id,
        "title": session.meta.title,
        "messages": [
            {
                "role": m.role,
                "content": m.content,
                "tool_name": m.tool_name,
                "is_error": m.is_error,
                "timestamp": m.timestamp,
            }
            for m in session.messages
            if m.role != "todo_reminder"
        ],
    }


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    cleanup_expired_sessions(SESSION_DIR)
    adapter: Optional[ModelAdapter] = None
    state: Optional[AgentLoopState] = None
    session: Optional[Session] = None
    running = False
    current_task: Optional[asyncio.Task] = None
    active_run_id = 0
    permission_counter = 0
    pending_permissions: dict[str, asyncio.Future] = {}
    send_lock = asyncio.Lock()
    app_state = WebAppState()

    async def send_event(event_type: str, data: Any):
        async with send_lock:
            await ws.send_text(json.dumps({"type": event_type, "data": data}, ensure_ascii=False, default=str))

    await send_event("workspace", {"workspace": APP_WORKSPACE})
    await send_event("characters", _characters_payload())
    sessions = list_sessions(SESSION_DIR)
    await send_event("sessions", [{"id": s.id, "title": s.title, "updated_at": s.updated_at} for s in sessions])

    try:
        while True:
            raw = await ws.receive_text()
            req = json.loads(raw)
            action = req.get("action", "")

            if action == "chat":
                if running:
                    await send_event("error", "Agent is already running")
                    continue
                active_run_id += 1
                run_id = active_run_id

                message = req.get("message", "")
                workspace = APP_WORKSPACE
                session_id = req.get("session_id")
                created_new_session = False
                should_auto_name = bool(req.get("auto_name"))

                if session_id:
                    session = load_session(SESSION_DIR, session_id)
                    if session:
                        should_auto_name = not any(m.role == "user" for m in session.messages)
                        state = AgentLoopState(messages=session.messages)
                    else:
                        await send_event("error", f"Session not found: {session_id}")
                        continue
                else:
                    ensure_general_purpose_character()
                    session = create_session(SESSION_DIR, workspace=workspace, title="New Session")
                    created_new_session = True
                    should_auto_name = True
                    state = AgentLoopState(messages=[])

                if created_new_session:
                    first_user_message = ChatMessage.user(message)
                    session.messages = [first_user_message]
                    save_session(SESSION_DIR, session)
                    await send_event("session_created", {
                        "id": session.meta.id,
                        "title": session.meta.title,
                        "created_from_chat": True,
                        "naming": True,
                    })
                    sessions = list_sessions(SESSION_DIR)
                    await send_event("sessions", [{"id": s.id, "title": s.title, "updated_at": s.updated_at} for s in sessions])
                    state = AgentLoopState(messages=[first_user_message])

                tools = await app_state.ensure_tools()
                await send_event("mcp_servers", app_state.mcp_servers or [])
                adapter = await _get_model_adapter(tools)
                runtime = await load_runtime_config()
                sent_tool_result_ids: set[str] = set()

                config = AgentLoopConfig(
                    workspace=workspace,
                    session_id=session.meta.id,
                    character_name=session.meta.character_name,
                    subsession_runtime=app_state.subsession_runtime,
                    permission_mode=PermissionMode.DEFAULT,
                    permissions=session.permissions,
                    max_loaded_subsessions=runtime.max_loaded_subsessions,
                    session_dir=SESSION_DIR,
                )

                async def on_step(step):
                    if run_id != active_run_id:
                        return
                    await send_event("step", {
                        "type": step.type,
                        "content": step.content,
                        "kind": getattr(step, "kind", None),
                        "thinking_blocks": [b.__dict__ for b in (step.thinking_blocks or [])],
                        "calls": [
                            {"id": c.id, "tool_name": c.tool_name, "input": c.input}
                            for c in (step.calls or [])
                        ],
                        "usage": step.usage.__dict__ if step.usage else None,
                    })

                async def on_delta(delta):
                    if run_id != active_run_id:
                        return
                    await send_event("delta", delta)

                async def on_messages_changed(messages):
                    if run_id != active_run_id or not session:
                        return
                    session.messages = messages
                    save_session(SESSION_DIR, session)
                    for message in messages:
                        if message.role != "tool_result" or message.tool_name == "TodoWrite":
                            continue
                        result_id = message.tool_use_id or message.id or ""
                        if not result_id or result_id in sent_tool_result_ids:
                            continue
                        sent_tool_result_ids.add(result_id)
                        await send_event("tool_result", {
                            "tool_use_id": message.tool_use_id,
                            "tool_name": message.tool_name,
                            "content": message.content,
                            "is_error": message.is_error,
                        })

                async def on_todos_changed(todos):
                    if run_id != active_run_id:
                        return
                    await send_event("todos_updated", {"session_id": session.meta.id if session else None, "todos": todos})

                async def on_mcp_servers_changed(servers):
                    if run_id != active_run_id:
                        return
                    await send_event("mcp_servers", servers)

                async def on_permission_request(request: PermissionRequest) -> PermissionResponse:
                    nonlocal permission_counter
                    permission_counter += 1
                    request_id = f"perm-{permission_counter}"
                    future = asyncio.get_running_loop().create_future()
                    pending_permissions[request_id] = future
                    await send_event("permission_request", {
                        "id": request_id,
                        "tool_name": request.tool_name,
                        "input": request.input,
                        "message": request.message,
                        "reason": request.reason,
                        "segments": request.segments,
                        "suggested_rules": request.suggested_rules,
                    })
                    response = await future
                    pending_permissions.pop(request_id, None)
                    return response

                async def run_chat_task():
                    nonlocal running, current_task, session, state
                    try:
                        result = await run_agent_loop(
                            user_input=message,
                            model_adapter=adapter,
                            config=config,
                            state=state,
                            on_step=on_step,
                            on_delta=on_delta,
                            on_messages_changed=on_messages_changed,
                            on_todos_changed=on_todos_changed,
                            on_mcp_servers_changed=on_mcp_servers_changed,
                            on_permission_request=on_permission_request,
                            tool_registry=tools,
                            user_already_appended=created_new_session,
                        )
                        if run_id != active_run_id:
                            return

                        session.messages = result.messages
                        if result.auto_compact_result:
                            retained = [
                                m for m in result.messages
                                if m is not result.auto_compact_result.summary
                            ]
                            append_compact_boundary(
                                SESSION_DIR,
                                session.meta.id,
                                result.auto_compact_result.summary.content,
                                "auto",
                                result.auto_compact_result.tokens_before,
                                result.auto_compact_result.tokens_after,
                                retained,
                                workspace=session.meta.workspace,
                            )
                            session = load_session(SESSION_DIR, session.meta.id) or session
                            state = AgentLoopState(messages=session.messages)
                            await send_event("compact", {
                                "trigger": "auto",
                                "tokens_before": result.auto_compact_result.tokens_before,
                                "tokens_after": result.auto_compact_result.tokens_after,
                            })
                        else:
                            save_session(SESSION_DIR, session)

                        await send_event("done", {
                            "stop_reason": result.stop_reason,
                            "turn": result.turn,
                            "session_id": session.meta.id,
                            "auto_name": should_auto_name,
                        })
                        await send_event("todos_updated", {"session_id": session.meta.id, "todos": _session_todos(session)})
                    except asyncio.CancelledError:
                        if run_id != active_run_id:
                            return
                        for future in pending_permissions.values():
                            if not future.done():
                                future.cancel()
                        pending_permissions.clear()
                        if session and state:
                            _close_open_tool_calls(state.messages)
                            session.messages = state.messages
                            save_session(SESSION_DIR, session)
                        await send_event("todos_updated", {"session_id": session.meta.id if session else None, "todos": _session_todos(session)})
                        await send_event("stopped", {"session_id": session.meta.id if session else None})
                    except Exception as e:
                        if run_id != active_run_id:
                            return
                        for future in pending_permissions.values():
                            if not future.done():
                                future.cancel()
                        pending_permissions.clear()
                        if session and state:
                            _close_open_tool_calls(state.messages)
                            session.messages = state.messages
                            save_session(SESSION_DIR, session)
                        await send_event("todos_updated", {"session_id": session.meta.id if session else None, "todos": _session_todos(session)})
                        _log_error(e)
                        await send_event("error", _error_payload(e))
                    finally:
                        if run_id == active_run_id:
                            running = False
                            current_task = None

                running = True
                current_task = asyncio.create_task(run_chat_task())

            elif action == "permission_response":
                request_id = req.get("request_id", "")
                future = pending_permissions.get(request_id)
                if not future or future.done():
                    await send_event("error", f"Unknown permission request: {request_id}")
                    continue
                raw_decision = req.get("decision", "deny")
                decision = PermissionDecision(raw_decision)
                scope = str(req.get("scope") or "session")
                tool_name = req.get("tool_name", "")
                raw_rules = req.get("rules", [])
                rules = [rule for rule in raw_rules if isinstance(rule, str)] if isinstance(raw_rules, list) else []
                if session and tool_name:
                    if scope == "character" and decision in (PermissionDecision.ALWAYS, PermissionDecision.NEVER):
                        character = next(
                            (item for item in load_characters() if item.name == session.meta.character_name),
                            None,
                        )
                        if character:
                            for rule in rules or [tool_name]:
                                if decision == PermissionDecision.ALWAYS:
                                    character.permissions.allow_tool(rule)
                                else:
                                    character.permissions.deny_tool(rule)
                            save_character(character)
                    elif scope == "session" and decision in (PermissionDecision.ALWAYS, PermissionDecision.NEVER):
                        for rule in rules or [tool_name]:
                            if decision == PermissionDecision.ALWAYS:
                                session.permissions.allow_tool(rule)
                            else:
                                session.permissions.deny_tool(rule)
                        update_session_permissions(SESSION_DIR, session)
                future.set_result(PermissionResponse(
                    decision=decision,
                    reason=req.get("reason", ""),
                    apply_to_session=decision in (PermissionDecision.ALWAYS, PermissionDecision.NEVER),
                    rules=rules,
                ))

            elif action == "stop":
                if current_task and not current_task.done():
                    active_run_id += 1
                    for future in pending_permissions.values():
                        if not future.done():
                            future.cancel()
                    pending_permissions.clear()
                    current_task.cancel()
                    if session and state:
                        _close_open_tool_calls(state.messages)
                        session.messages = state.messages
                        save_session(SESSION_DIR, session)
                    await send_event("todos_updated", {"session_id": session.meta.id if session else None, "todos": _session_todos(session)})
                    running = False
                    current_task = None
                    await send_event("stopped", {"session_id": session.meta.id if session else None})
                else:
                    await send_event("error", "No running agent")

            elif action == "compact":
                if not adapter or not session:
                    await send_event("error", "No active session")
                    continue
                result = await run_manual_compact(session.messages, adapter)
                if result:
                    retained = [m for m in result.messages if m is not result.summary]
                    append_compact_boundary(
                        SESSION_DIR,
                        session.meta.id,
                        result.summary.content,
                        "manual",
                        result.tokens_before,
                        result.tokens_after,
                        retained,
                        workspace=session.meta.workspace,
                    )
                    session = load_session(SESSION_DIR, session.meta.id)
                    if session:
                        state = AgentLoopState(messages=session.messages)
                    await send_event("compact", {"tokens_before": result.tokens_before, "tokens_after": result.tokens_after})
                else:
                    await send_event("compact", {"message": "No compaction needed"})

            elif action == "rename_session":
                sid = req.get("session_id")
                title = req.get("title", "")
                if sid and title:
                    session = load_session(SESSION_DIR, sid)
                    workspace = session.meta.workspace if session else ""
                    if rename_session(SESSION_DIR, sid, title, workspace=workspace):
                        await send_event("session_renamed", {"id": sid, "title": title})

            elif action == "delete_session":
                sid = req.get("session_id")
                if sid and delete_session(SESSION_DIR, sid):
                    await send_event("session_deleted", {"id": sid})
                    sessions = list_sessions(SESSION_DIR)
                    await send_event("sessions", [{"id": s.id, "title": s.title, "updated_at": s.updated_at} for s in sessions])

            elif action == "auto_name_session":
                sid = req.get("session_id")
                if sid:
                    session = load_session(SESSION_DIR, sid)
                    if session and session.messages:
                        adapter = await _get_model_adapter()
                        try:
                            user_msgs = [m.content for m in session.messages if m.role == "user"]
                            prompt_text = " ".join(user_msgs[:2]) if user_msgs else ""
                            name_prompt = [
                                ChatMessage.system(
                                    "Generate a short session title. "
                                    "Use at most 30 characters, regardless of language. "
                                    "Reply with ONLY the title text, no quotes, no explanation."
                                ),
                                ChatMessage.user(f"Conversation: {prompt_text[:200]}" if prompt_text else "New conversation"),
                            ]
                            title = ""
                            title_saved = False
                            for _ in range(3):
                                step = await adapter.next(name_prompt)
                                title = (step.content or "").strip()
                                validation_error = _validate_ai_session_title(title)
                                if validation_error is None:
                                    if rename_session(SESSION_DIR, sid, title, workspace=session.meta.workspace):
                                        await send_event("session_renamed", {"id": sid, "title": title})
                                        title_saved = True
                                    break
                                name_prompt.append(ChatMessage.assistant(title or "(empty title)"))
                                name_prompt.append(ChatMessage.user(
                                    f"Invalid title: {validation_error}. Generate a new title that follows all rules."
                                ))
                            if not title_saved and title:
                                if rename_session(SESSION_DIR, sid, title, workspace=session.meta.workspace):
                                    await send_event("session_renamed", {"id": sid, "title": title})
                        except Exception as e:
                            _log_error(e)
                            await send_event("error", _error_payload(e))

            elif action == "load_session":
                sid = req.get("session_id")
                if sid:
                    session = load_session(SESSION_DIR, sid)
                    transcript = load_transcript(SESSION_DIR, sid)
                    if session and transcript is not None:
                        state = AgentLoopState(messages=session.messages)
                        await send_event("session_loaded", {
                            "id": session.meta.id,
                            "title": session.meta.title,
                            "message_count": len(transcript),
                            "todos": extract_todos_from_messages(session.messages),
                            "messages": [
                                {
                                    "role": m.role,
                                    "content": m.content,
                                    "blocks": m.blocks,
                                    "tool_use_id": m.tool_use_id,
                                    "tool_name": m.tool_name,
                                    "input": m.input,
                                    "is_error": m.is_error,
                                }
                                for m in transcript
                            ],
                        })

            elif action == "list_subsessions":
                parent_id = req.get("parent_session_id", "")
                if parent_id:
                    await send_event("subsessions", {
                        "parent_session_id": parent_id,
                        "children": _subsession_payload(app_state.subsession_runtime, parent_id),
                    })

            elif action == "load_subsession":
                parent_id = req.get("parent_session_id", "")
                child_id = req.get("session_id", "")
                if parent_id and child_id:
                    child = load_session(
                        SESSION_DIR,
                        child_id,
                        root_session_id=parent_id,
                        parent_session_id=parent_id,
                    )
                    transcript = load_transcript(
                        SESSION_DIR,
                        child_id,
                        root_session_id=parent_id,
                        parent_session_id=parent_id,
                    )
                    if child and transcript is not None:
                        await send_event("session_loaded", {
                            "id": child.meta.id,
                            "title": child.meta.title,
                            "message_count": len(transcript),
                            "todos": extract_todos_from_messages(child.messages),
                            "read_only": True,
                            "parent_session_id": parent_id,
                            "messages": [
                                {
                                    "role": m.role,
                                    "content": m.content,
                                    "blocks": m.blocks,
                                    "tool_use_id": m.tool_use_id,
                                    "tool_name": m.tool_name,
                                    "input": m.input,
                                    "is_error": m.is_error,
                                }
                                for m in transcript
                            ],
                        })
                    else:
                        await send_event("error", "SubSession not found")

            elif action == "new_session":
                session = create_session(SESSION_DIR, workspace=APP_WORKSPACE, title="New Session")
                state = AgentLoopState(messages=[])
                await send_event("session_created", {"id": session.meta.id, "title": session.meta.title})
                await send_event("todos_updated", {"session_id": session.meta.id, "todos": []})
                sessions = list_sessions(SESSION_DIR)
                await send_event("sessions", [{"id": s.id, "title": s.title, "updated_at": s.updated_at} for s in sessions])

            elif action == "fork_session":
                sid = req.get("session_id")
                if sid:
                    forked = fork_session(SESSION_DIR, sid)
                    if forked:
                        session = forked
                        state = AgentLoopState(messages=forked.messages)
                        await send_event("todos_updated", {"session_id": forked.meta.id, "todos": extract_todos_from_messages(forked.messages)})
                        await send_event("session_forked", {
                            "id": forked.meta.id,
                            "title": forked.meta.title,
                            "message_count": len(forked.messages),
                        })
                        sessions = list_sessions(SESSION_DIR)
                        await send_event("sessions", [{"id": s.id, "title": s.title, "updated_at": s.updated_at} for s in sessions])
                    else:
                        await send_event("error", "Session not found or empty")

            elif action == "list_sessions":
                cleanup_expired_sessions(SESSION_DIR)
                sessions = list_sessions(SESSION_DIR)
                await send_event("sessions", [{"id": s.id, "title": s.title, "updated_at": s.updated_at} for s in sessions])

            else:
                await send_event("error", f"Unknown action: {action}")

    except WebSocketDisconnect:
        pass
    except Exception as e:
        _log_error(e)
        try:
            await send_event("error", _error_payload(e))
        except Exception:
            pass
    finally:
        await app_state.close()


if os.path.isdir(STATIC_DIR):
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")


def main():
    import uvicorn
    port = int(os.environ.get("PORT", "8080"))
    host = os.environ.get("HOST", "127.0.0.1")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
