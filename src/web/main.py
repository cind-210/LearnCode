"""
FastAPI + WebSocket web application for LearnCode.

Provides the web frontend and API for the agent.
"""
from __future__ import annotations

import json
import os
from typing import Any, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from ..loop.runner import (
    AgentLoopConfig,
    AgentLoopState,
    run_agent_loop,
    run_manual_compact,
)
from ..models.anthropic import AnthropicModelAdapter
from ..models.openai import OpenAIModelAdapter
from ..config.runtime import load_runtime_config
from ..tools.permissions import PermissionMode
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
    Session,
)
from ..tools.builtin import build_builtin_registry
from ..loop.messages import ChatMessage, ModelAdapter

app = FastAPI(title="LearnCode", version="1.0.0")

PROJECT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
STATIC_DIR = os.path.join(PROJECT_DIR, "static")
SESSION_DIR = os.path.join(PROJECT_DIR, ".sessions")
APP_WORKSPACE = os.path.abspath(os.environ.get("WORKSPACE", os.getcwd()))


def _is_cjk_char(ch: str) -> bool:
    code = ord(ch)
    return (
        0x3400 <= code <= 0x4DBF
        or 0x4E00 <= code <= 0x9FFF
        or 0xF900 <= code <= 0xFAFF
        or 0x3040 <= code <= 0x30FF
        or 0xAC00 <= code <= 0xD7AF
    )


def _validate_ai_session_title(title: str) -> Optional[str]:
    if not title:
        return "title is empty"
    compact_title = "".join(ch for ch in title if not ch.isspace())
    if not compact_title:
        return "title is empty"
    if any(not (ch.isalnum() or ch.isspace()) for ch in title):
        return "title must not contain punctuation"
    if any(_is_cjk_char(ch) for ch in compact_title):
        if len(compact_title) > 10:
            return "Chinese title must be at most 10 characters"
    elif len(compact_title) > 20:
        return "English title must be at most 20 characters"
    return None


async def _get_model_adapter() -> ModelAdapter:
    tools = build_builtin_registry()
    runtime = await load_runtime_config()
    if runtime.provider == "openai":
        return OpenAIModelAdapter(tools)
    return AnthropicModelAdapter(tools)


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

    await send_event("workspace", {"workspace": APP_WORKSPACE})
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

                message = req.get("message", "")
                workspace = APP_WORKSPACE
                session_id = req.get("session_id")
                created_new_session = False

                if session_id:
                    session = load_session(SESSION_DIR, session_id)
                    if session:
                        state = AgentLoopState(messages=session.messages)
                    else:
                        session = create_session(SESSION_DIR, workspace=workspace, title="New Session")
                        created_new_session = True
                        state = AgentLoopState(messages=[])
                else:
                    session = create_session(SESSION_DIR, workspace=workspace, title="New Session")
                    created_new_session = True
                    state = AgentLoopState(messages=[])

                if created_new_session:
                    await send_event("session_created", {
                        "id": session.meta.id,
                        "title": session.meta.title,
                        "created_from_chat": True,
                        "naming": True,
                    })
                    sessions = list_sessions(SESSION_DIR)
                    await send_event("sessions", [{"id": s.id, "title": s.title, "updated_at": s.updated_at} for s in sessions])

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
                        "auto_name": created_new_session,
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
                                ChatMessage.system(
                                    "Generate a short session title. "
                                    "If the title is Chinese, use at most 10 Chinese characters. "
                                    "If the title is English, use at most 20 characters. "
                                    "Do not use punctuation. "
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
                        except Exception:
                            pass

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
                            "messages": [
                                {
                                    "role": m.role,
                                    "content": m.content,
                                    "tool_name": m.tool_name,
                                    "input": m.input,
                                    "is_error": m.is_error,
                                }
                                for m in transcript
                            ],
                        })

            elif action == "new_session":
                session = create_session(SESSION_DIR, workspace=APP_WORKSPACE, title="New Session")
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
