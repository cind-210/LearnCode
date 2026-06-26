"""
FastAPI + WebSocket web application for LearnCode.

Provides the web frontend and API for the agent.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse

from .agent_loop import (
    AgentLoopConfig,
    AgentLoopState,
    run_agent_loop,
    run_manual_compact,
)
from .anthropic_adapter import AnthropicModelAdapter
from .openai_adapter import OpenAIModelAdapter
from .config import load_runtime_config
from .permissions import PermissionMode
from .session import (
    append_compact_boundary,
    cleanup_expired_sessions,
    create_session,
    delete_session,
    fork_session,
    list_sessions,
    load_session,
    rename_session,
    save_session,
    Session,
    SessionMeta,
)
from .tool import ToolRegistry
from .tools.base import build_builtin_registry
from .types import ChatMessage, ModelAdapter

app = FastAPI(title="LearnCode", version="1.0.0")

STATIC_DIR = os.path.join(os.path.dirname(__file__), "..", "static")
SESSION_DIR = os.path.join(os.path.dirname(__file__), "..", ".sessions")


async def _get_model_adapter() -> ModelAdapter:
    tools = build_builtin_registry()
    runtime = await load_runtime_config()
    if runtime.provider == "openai":
        return OpenAIModelAdapter(tools)
    return AnthropicModelAdapter(tools)


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/sessions")
async def api_list_sessions():
    cleanup_expired_sessions(SESSION_DIR)
    sessions = list_sessions(SESSION_DIR)
    return [{"id": s.id, "title": s.title, "created_at": s.created_at, "updated_at": s.updated_at, "message_count": s.message_count, "workspace": s.workspace} for s in sessions]


@app.post("/api/sessions")
async def api_create_session(data: dict[str, Any]):
    title = data.get("title", "New Session")
    workspace = data.get("workspace", os.getcwd())
    session = create_session(SESSION_DIR, workspace=workspace, title=title)
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

    async def send_event(event_type: str, data: Any):
        await ws.send_text(json.dumps({"type": event_type, "data": data}, ensure_ascii=False, default=str))

    try:
        while True:
            raw = await ws.receive_text()
            req = json.loads(raw)
            action = req.get("action", "")

            if action == "chat":
                if running:
                    await send_event("error", "Agent is already running")
                    continue

                message = req.get("message", "")
                workspace = req.get("workspace", os.getcwd())
                session_id = req.get("session_id")

                if session_id:
                    session = load_session(SESSION_DIR, session_id)
                    if session:
                        state = AgentLoopState(messages=session.messages)
                        workspace = session.meta.workspace or workspace
                    else:
                        session = create_session(SESSION_DIR, workspace=workspace)
                        state = AgentLoopState(messages=[])
                else:
                    session = create_session(SESSION_DIR, workspace=workspace)
                    state = AgentLoopState(messages=[])

                adapter = await _get_model_adapter()

                config = AgentLoopConfig(
                    workspace=workspace,
                    permission_mode=PermissionMode.DEFAULT,
                )

                running = True

                async def on_step(step):
                    await send_event("step", {
                        "type": step.type,
                        "content": step.content,
                        "kind": getattr(step, "kind", None),
                        "calls": [
                            {"id": c.id, "tool_name": c.tool_name, "input": c.input}
                            for c in (step.calls or [])
                        ],
                        "usage": step.usage.__dict__ if step.usage else None,
                    })

                try:
                    result = await run_agent_loop(
                        user_input=message,
                        model_adapter=adapter,
                        config=config,
                        state=state,
                        on_step=on_step,
                    )

                    session.messages = result.messages
                    save_session(SESSION_DIR, session)

                    await send_event("done", {
                        "stop_reason": result.stop_reason,
                        "turn": result.turn,
                        "session_id": session.meta.id,
                    })
                except Exception as e:
                    await send_event("error", str(e))
                finally:
                    running = False

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
                                ChatMessage.system("Generate a short title (max 20 chars) for a conversation. Reply with ONLY the title text, no quotes, no explanation."),
                                ChatMessage.user(f"Conversation: {prompt_text[:200]}" if prompt_text else "New conversation"),
                            ]
                            step = await adapter.next(name_prompt)
                            title = (step.content or "Untitled").strip()[:50]
                            if rename_session(SESSION_DIR, sid, title, workspace=session.meta.workspace):
                                await send_event("session_renamed", {"id": sid, "title": title})
                        except Exception:
                            pass

            elif action == "load_session":
                sid = req.get("session_id")
                if sid:
                    session = load_session(SESSION_DIR, sid)
                    if session:
                        state = AgentLoopState(messages=session.messages)
                        await send_event("session_loaded", {
                            "id": session.meta.id,
                            "title": session.meta.title,
                            "message_count": len(session.messages),
                            "messages": [
                                {
                                    "role": m.role,
                                    "content": m.content,
                                    "tool_name": m.tool_name,
                                    "input": m.input,
                                    "is_error": m.is_error,
                                }
                                for m in session.messages
                            ],
                        })

            elif action == "new_session":
                workspace = req.get("workspace", os.getcwd())
                session = create_session(SESSION_DIR, workspace=workspace, title="New Session")
                state = AgentLoopState(messages=[])
                await send_event("session_created", {"id": session.meta.id, "title": session.meta.title})
                sessions = list_sessions(SESSION_DIR)
                await send_event("sessions", [{"id": s.id, "title": s.title, "updated_at": s.updated_at} for s in sessions])

            elif action == "fork_session":
                sid = req.get("session_id")
                if sid:
                    forked = fork_session(SESSION_DIR, sid)
                    if forked:
                        session = forked
                        state = AgentLoopState(messages=forked.messages)
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
        try:
            await send_event("error", str(e))
        except Exception:
            pass


if os.path.isdir(STATIC_DIR):
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")


def main():
    import uvicorn
    port = int(os.environ.get("PORT", "8080"))
    host = os.environ.get("HOST", "127.0.0.1")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
