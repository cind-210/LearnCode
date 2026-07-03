"""
Append-only session event log.

This mirrors the TypeScript MiniCode session model closely enough for the web
runtime: messages are appended as events, compact boundaries reset the active
context on resume, and rename/fork/delete operate on the event log.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..loop.messages import ChatMessage
from ..context.token_estimator import CLEAR_MARKER


MAX_TITLE_LENGTH = 60
SESSION_RETENTION_MS = 30 * 24 * 60 * 60 * 1000


@dataclass
class SessionMeta:
    id: str
    title: str = ""
    created_at: int = 0
    updated_at: int = 0
    message_count: int = 0
    workspace: str = ""


@dataclass
class Session:
    meta: SessionMeta
    messages: list[ChatMessage] = field(default_factory=list)

    @property
    def id(self) -> str:
        return self.meta.id


@dataclass
class TranscriptItem:
    role: str
    content: str = ""
    tool_name: str = ""
    input: Any = None
    is_error: bool = False
    timestamp: int = 0


def _now_ms() -> int:
    return int(time.time() * 1000)


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + f".{int(time.time() * 1000) % 1000:03d}Z"


def _generate_id(length: int = 12) -> str:
    return uuid.uuid4().hex[:length]


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _session_path(session_dir: str, session_id: str) -> str:
    return os.path.join(session_dir, f"{session_id}.jsonl")


def _index_path(session_dir: str) -> str:
    return os.path.join(session_dir, "index.json")


def _legacy_meta_path(session_dir: str, session_id: str) -> str:
    return os.path.join(session_dir, f"{session_id}.meta.json")


def _serialize_message(message: ChatMessage) -> dict[str, Any]:
    data: dict[str, Any] = {"role": message.role}
    if message.content:
        data["content"] = message.content
    if message.blocks:
        data["blocks"] = [b.__dict__ for b in message.blocks]
    if message.tool_use_id:
        data["tool_use_id"] = message.tool_use_id
    if message.tool_name:
        data["tool_name"] = message.tool_name
    if message.input is not None:
        data["input"] = message.input
    if message.is_error:
        data["is_error"] = message.is_error
    if message.compressed_count:
        data["compressed_count"] = message.compressed_count
    if message.timestamp:
        data["timestamp"] = message.timestamp
    if message.removed_message_ids:
        data["removed_message_ids"] = message.removed_message_ids
    if message.cleared_message_ids:
        data["cleared_message_ids"] = message.cleared_message_ids
    if message.removed_count:
        data["removed_count"] = message.removed_count
    if message.tokens_freed:
        data["tokens_freed"] = message.tokens_freed
    if message.provider_usage:
        data["provider_usage"] = message.provider_usage.__dict__
    if message.usage_stale:
        data["usage_stale"] = True
    if message.usage_stale_reason:
        data["usage_stale_reason"] = message.usage_stale_reason
    if message.id:
        data["id"] = message.id
    return data


def _deserialize_message(data: dict[str, Any], event_uuid: str = "") -> ChatMessage:
    from ..loop.messages import ProviderThinkingBlock, ProviderUsage

    kwargs: dict[str, Any] = {"content": data.get("content", "")}
    for key in (
        "tool_use_id",
        "tool_name",
        "usage_stale_reason",
        "tokens_freed",
        "removed_count",
        "compressed_count",
        "timestamp",
        "id",
    ):
        if key in data:
            kwargs[key] = data[key]
    if event_uuid and not kwargs.get("id"):
        kwargs["id"] = event_uuid
    if "blocks" in data:
        kwargs["blocks"] = [ProviderThinkingBlock(**b) for b in data["blocks"]]
    if "input" in data:
        kwargs["input"] = data["input"]
    if "is_error" in data:
        kwargs["is_error"] = data["is_error"]
    if "removed_message_ids" in data:
        kwargs["removed_message_ids"] = data["removed_message_ids"]
    if "cleared_message_ids" in data:
        kwargs["cleared_message_ids"] = data["cleared_message_ids"]
    if "provider_usage" in data:
        kwargs["provider_usage"] = ProviderUsage(**data["provider_usage"])
    if "usage_stale" in data:
        kwargs["usage_stale"] = data["usage_stale"]
    return ChatMessage(role=data["role"], **kwargs)


def _role_to_event_type(role: str) -> str:
    return {
        "system": "system",
        "user": "user",
        "assistant": "assistant",
        "assistant_thinking": "thinking",
        "assistant_progress": "progress",
        "assistant_tool_call": "tool_call",
        "tool_result": "tool_result",
        "context_summary": "summary",
        "snip_boundary": "snip_boundary",
        "microcompact_boundary": "microcompact_boundary",
    }.get(role, "user")


def _ensure_message_id(message: ChatMessage) -> str:
    if message.id:
        return message.id
    message.id = str(uuid.uuid4())
    return message.id


def _read_events(path: str) -> list[dict[str, Any]]:
    if not os.path.isfile(path):
        return []
    content = Path(path).read_text(encoding="utf-8")
    events: list[dict[str, Any]] = []
    session_id = Path(path).stem
    parent_uuid: Optional[str] = None
    for line in content.splitlines():
        if not line.strip():
            continue
        data = json.loads(line)
        if "role" in data and "type" not in data:
            event_uuid = data.get("id") or str(uuid.uuid4())
            data["id"] = event_uuid
            event = {
                "type": _role_to_event_type(str(data["role"])),
                "message": data,
                "uuid": event_uuid,
                "timestamp": _now_iso(),
                "session_id": session_id,
                "cwd": "",
                "parent_uuid": parent_uuid,
            }
            parent_uuid = event_uuid
            events.append(event)
        else:
            parent_uuid = data.get("uuid", parent_uuid)
            events.append(data)
    return events


def _write_event(path: str, event: dict[str, Any]) -> None:
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")


def _append_events(session_dir: str, session_id: str, events: list[dict[str, Any]]) -> None:
    if not events:
        return
    _ensure_dir(session_dir)
    path = _session_path(session_dir, session_id)
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        for event in events:
            f.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")


def _meta_to_dict(meta: SessionMeta) -> dict[str, Any]:
    return {
        "id": meta.id,
        "title": meta.title,
        "created_at": meta.created_at,
        "updated_at": meta.updated_at,
        "message_count": meta.message_count,
        "workspace": meta.workspace,
    }


def _meta_from_dict(data: dict[str, Any]) -> SessionMeta:
    return SessionMeta(
        id=str(data.get("id", "")),
        title=str(data.get("title", "")),
        created_at=int(data.get("created_at", 0) or 0),
        updated_at=int(data.get("updated_at", 0) or 0),
        message_count=int(data.get("message_count", 0) or 0),
        workspace=str(data.get("workspace", "")),
    )


def _read_session_index(session_dir: str) -> dict[str, SessionMeta]:
    path = _index_path(session_dir)
    if not os.path.isfile(path):
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {}
    sessions = data.get("sessions", {})
    if not isinstance(sessions, dict):
        return {}
    return {
        session_id: meta
        for session_id, meta in (
            (str(key), _meta_from_dict(value))
            for key, value in sessions.items()
            if isinstance(value, dict)
        )
        if meta.id
    }


def _write_session_index(session_dir: str, metas: dict[str, SessionMeta]) -> None:
    _ensure_dir(session_dir)
    path = _index_path(session_dir)
    data = {
        "version": 1,
        "sessions": {
            session_id: _meta_to_dict(meta)
            for session_id, meta in sorted(
                metas.items(),
                key=lambda item: item[1].updated_at,
                reverse=True,
            )
        },
    }
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _sync_index_meta(session_dir: str, meta: SessionMeta) -> None:
    metas = _read_session_index(session_dir)
    metas[meta.id] = meta
    _write_session_index(session_dir, metas)


def _remove_index_meta(session_dir: str, session_id: str) -> None:
    metas = _read_session_index(session_dir)
    if session_id in metas:
        del metas[session_id]
        _write_session_index(session_dir, metas)


def _rebuild_session_index(session_dir: str) -> dict[str, SessionMeta]:
    _ensure_dir(session_dir)
    metas: dict[str, SessionMeta] = {}
    for entry in os.scandir(session_dir):
        if entry.is_file() and entry.name.endswith(".jsonl"):
            session_id = entry.name[:-len(".jsonl")]
            metas[session_id] = _session_meta_from_file(session_dir, session_id)
    _write_session_index(session_dir, metas)
    return metas


def _last_event_uuid(events: list[dict[str, Any]]) -> Optional[str]:
    if not events:
        return None
    return events[-1].get("uuid")


def _message_event(message: ChatMessage, session_id: str, workspace: str, parent_uuid: Optional[str]) -> dict[str, Any]:
    event_uuid = _ensure_message_id(message)
    event: dict[str, Any] = {
        "type": _role_to_event_type(message.role),
        "message": _serialize_message(message),
        "uuid": event_uuid,
        "timestamp": _now_iso(),
        "session_id": session_id,
        "cwd": workspace,
        "parent_uuid": parent_uuid,
    }
    if message.role == "snip_boundary":
        event["snip_metadata"] = {
            "type": "snip_boundary",
            "removed_message_ids": message.removed_message_ids,
            "removed_count": message.removed_count,
            "tokens_freed": message.tokens_freed,
            "timestamp": event["timestamp"],
            "created_at": event["timestamp"],
        }
    if message.role == "microcompact_boundary":
        event["microcompact_metadata"] = {
            "type": "microcompact_boundary",
            "cleared_message_ids": message.cleared_message_ids,
            "cleared_count": message.removed_count,
            "tokens_freed": message.tokens_freed,
            "timestamp": event["timestamp"],
            "created_at": event["timestamp"],
        }
    return event


def _unwrap_message(event: dict[str, Any]) -> Optional[ChatMessage]:
    message = event.get("message")
    if not isinstance(message, dict):
        return None
    return _deserialize_message(message, event_uuid=event.get("uuid", ""))


def _active_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    last_boundary = -1
    for index in range(len(events) - 1, -1, -1):
        if events[index].get("type") == "compact_boundary":
            last_boundary = index
            break
    return events[last_boundary + 1:]


def _reconstruct_snipped_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    snip_events = [
        event for event in events
        if event.get("type") == "snip_boundary"
        and event.get("snip_metadata", {}).get("removed_message_ids")
    ]
    if not snip_events:
        return events

    removed_to_snips: dict[str, list[dict[str, Any]]] = {}
    for snip in snip_events:
        for removed_id in snip["snip_metadata"]["removed_message_ids"]:
            removed_to_snips.setdefault(removed_id, []).append(snip)

    inserted: set[str] = set()
    result: list[dict[str, Any]] = []
    for event in events:
        if event.get("type") == "snip_boundary":
            continue
        snips = removed_to_snips.get(event.get("uuid"), [])
        if snips:
            for snip in snips:
                snip_id = snip.get("uuid", "")
                if snip_id not in inserted:
                    result.append(snip)
                    inserted.add(snip_id)
            continue
        result.append(event)
    return result


def _reconstruct_microcompacted_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleared_ids: set[str] = set()
    for event in events:
        if event.get("type") != "microcompact_boundary":
            continue
        metadata = event.get("microcompact_metadata", {})
        ids = metadata.get("cleared_message_ids")
        if isinstance(ids, list):
            cleared_ids.update(str(item) for item in ids)
    if not cleared_ids:
        return events

    result: list[dict[str, Any]] = []
    for event in events:
        message = event.get("message")
        message_id = event.get("uuid")
        tool_use_id = message.get("tool_use_id") if isinstance(message, dict) else None
        if event.get("type") != "tool_result" or (
            message_id not in cleared_ids and tool_use_id not in cleared_ids
        ):
            result.append(event)
            continue
        if not isinstance(message, dict):
            result.append(event)
            continue
        compacted = {
            **event,
            "message": {
                **message,
                "content": CLEAR_MARKER,
            },
        }
        result.append(compacted)
    return result


def _title_from_events(events: list[dict[str, Any]]) -> str:
    title = ""
    for event in events:
        if event.get("type") == "rename":
            title = str(event.get("title", "")).strip()
    if title:
        return title

    for event in events:
        if event.get("type") != "user":
            continue
        message = event.get("message", {})
        content = str(message.get("content", "")).strip() if isinstance(message, dict) else ""
        if content:
            return content[:MAX_TITLE_LENGTH] + ("..." if len(content) > MAX_TITLE_LENGTH else "")
    return ""


def _session_meta_from_file(session_dir: str, session_id: str) -> SessionMeta:
    path = _session_path(session_dir, session_id)
    events = _read_events(path)
    stat = os.stat(path)
    workspace = ""
    legacy_title = ""
    for event in events:
        if event.get("cwd"):
            workspace = str(event["cwd"])
            break
    meta_path = _legacy_meta_path(session_dir, session_id)
    if os.path.isfile(meta_path):
        meta_data = json.loads(Path(meta_path).read_text(encoding="utf-8"))
        workspace = workspace or str(meta_data.get("workspace", ""))
        legacy_title = str(meta_data.get("title", ""))
    return SessionMeta(
        id=session_id,
        title=_title_from_events(events) or legacy_title or f"Session {session_id}",
        created_at=int(stat.st_ctime * 1000),
        updated_at=int(stat.st_mtime * 1000),
        message_count=sum(1 for event in events if isinstance(event.get("message"), dict)),
        workspace=workspace,
    )


def create_session(session_dir: str, workspace: str = "", title: str = "") -> Session:
    session_id = _generate_id()
    now = _now_ms()
    session = Session(
        meta=SessionMeta(
            id=session_id,
            title=title or f"Session {session_id}",
            created_at=now,
            updated_at=now,
            workspace=workspace,
        )
    )
    if title:
        rename_session(session_dir, session_id, title, workspace=workspace, create_if_missing=True)
    else:
        _sync_index_meta(session_dir, session.meta)
    return session


def list_sessions(session_dir: str) -> list[SessionMeta]:
    _ensure_dir(session_dir)
    index_path = _index_path(session_dir)
    if os.path.isfile(index_path):
        metas = _read_session_index(session_dir)
    else:
        metas = _rebuild_session_index(session_dir)
    existing_ids = {
        entry.name[:-len(".jsonl")]
        for entry in os.scandir(session_dir)
        if entry.is_file() and entry.name.endswith(".jsonl")
    }
    if set(metas) != existing_ids:
        metas = _rebuild_session_index(session_dir)
    sessions = list(metas.values())
    sessions.sort(key=lambda s: s.updated_at, reverse=True)
    return sessions


def load_session(session_dir: str, session_id: str) -> Optional[Session]:
    path = _session_path(session_dir, session_id)
    if not os.path.isfile(path):
        return None
    events = _read_events(path)
    active_events = _active_events(events)
    reconstructed_events = _reconstruct_microcompacted_events(_reconstruct_snipped_events(active_events))
    messages = [
        message
        for message in (_unwrap_message(event) for event in reconstructed_events)
        if message is not None
    ]
    return Session(meta=_session_meta_from_file(session_dir, session_id), messages=messages)


def load_transcript(session_dir: str, session_id: str) -> Optional[list[TranscriptItem]]:
    path = _session_path(session_dir, session_id)
    if not os.path.isfile(path):
        return None
    events = _read_events(path)
    items: list[TranscriptItem] = []
    for event in events:
        event_type = event.get("type")
        message = event.get("message")
        if event_type == "compact_boundary":
            metadata = event.get("compact_metadata", {})
            pre_tokens = metadata.get("pre_tokens", "?")
            post_tokens = metadata.get("post_tokens", "?")
            trigger = metadata.get("trigger", "unknown")
            items.append(TranscriptItem(
                role="compact_boundary",
                content=f"Context compacted ({trigger}): {pre_tokens} -> {post_tokens} tokens",
            ))
            continue
        if not isinstance(message, dict):
            continue
        chat_message = _deserialize_message(message, event_uuid=event.get("uuid", ""))
        items.append(TranscriptItem(
            role=chat_message.role,
            content=chat_message.content,
            tool_name=chat_message.tool_name,
            input=chat_message.input,
            is_error=chat_message.is_error,
            timestamp=chat_message.timestamp,
        ))
    return items


def save_session(session_dir: str, session: Session, already_saved_count: int = 0) -> None:
    _ensure_dir(session_dir)
    path = _session_path(session_dir, session.meta.id)
    existing_events = _read_events(path)
    existing_ids = {event.get("uuid") for event in existing_events if event.get("uuid")}
    parent_uuid = _last_event_uuid(existing_events)
    messages = session.messages[1:] if session.messages and session.messages[0].role == "system" else session.messages
    events: list[dict[str, Any]] = []

    for index, message in enumerate(messages):
        if message.id and message.id in existing_ids:
            continue
        if not message.id and index < already_saved_count:
            continue
        event = _message_event(message, session.meta.id, session.meta.workspace, parent_uuid)
        parent_uuid = event["uuid"]
        events.append(event)

    _append_events(session_dir, session.meta.id, events)
    _sync_index_meta(session_dir, _session_meta_from_file(session_dir, session.meta.id))


def append_compact_boundary(
    session_dir: str,
    session_id: str,
    summary_text: str,
    trigger: str,
    pre_tokens: int,
    post_tokens: int,
    retained_messages: list[ChatMessage],
    workspace: str = "",
) -> None:
    existing_events = _read_events(_session_path(session_dir, session_id))
    now = _now_iso()
    boundary_uuid = str(uuid.uuid4())
    boundary = {
        "type": "compact_boundary",
        "subtype": "compact_boundary",
        "uuid": boundary_uuid,
        "timestamp": now,
        "session_id": session_id,
        "cwd": workspace,
        "parent_uuid": None,
        "logical_parent_uuid": _last_event_uuid(existing_events),
        "compact_metadata": {
            "trigger": trigger,
            "pre_tokens": pre_tokens,
            "post_tokens": post_tokens,
        },
    }
    summary = ChatMessage.user(summary_text)
    summary_event = _message_event(summary, session_id, workspace, boundary_uuid)
    parent_uuid = summary_event["uuid"]
    events = [boundary, summary_event]
    for message in retained_messages:
        if message.role == "system":
            continue
        event = _message_event(message, session_id, workspace, parent_uuid)
        parent_uuid = event["uuid"]
        events.append(event)
    _append_events(session_dir, session_id, events)
    _sync_index_meta(session_dir, _session_meta_from_file(session_dir, session_id))


def rename_session(
    session_dir: str,
    session_id: str,
    new_title: str,
    workspace: str = "",
    create_if_missing: bool = False,
) -> bool:
    path = _session_path(session_dir, session_id)
    if not create_if_missing and not os.path.isfile(path):
        return False
    _ensure_dir(session_dir)
    event = {
        "type": "rename",
        "title": new_title,
        "uuid": str(uuid.uuid4()),
        "timestamp": _now_iso(),
        "session_id": session_id,
        "cwd": workspace,
        "parent_uuid": _last_event_uuid(_read_events(path)),
    }
    _write_event(path, event)
    _sync_index_meta(session_dir, _session_meta_from_file(session_dir, session_id))
    return True


def fork_session(session_dir: str, session_id: str) -> Optional[Session]:
    source = load_session(session_dir, session_id)
    if source is None or not source.messages:
        return None

    new_session = create_session(session_dir, workspace=source.meta.workspace)
    new_session.messages = [ChatMessage.system("")] + source.messages
    save_session(session_dir, new_session)

    existing_titles = [s.title for s in list_sessions(session_dir)]
    base_title = source.meta.title or "session"
    prefix = f"{base_title}_fork"
    next_number = 1
    while f"{prefix}{next_number}" in existing_titles:
        next_number += 1
    rename_session(session_dir, new_session.meta.id, f"{prefix}{next_number}", workspace=source.meta.workspace)
    loaded = load_session(session_dir, new_session.meta.id)
    return loaded or new_session


def delete_session(session_dir: str, session_id: str) -> bool:
    deleted = False
    for path in (_session_path(session_dir, session_id), _legacy_meta_path(session_dir, session_id)):
        if os.path.isfile(path):
            os.remove(path)
            deleted = True
    if deleted:
        _remove_index_meta(session_dir, session_id)
    return deleted


def cleanup_expired_sessions(session_dir: str, max_age_ms: int = SESSION_RETENTION_MS) -> int:
    if not os.path.isdir(session_dir):
        return 0
    now = _now_ms()
    removed = 0
    for entry in os.scandir(session_dir):
        if not entry.is_file() or not entry.name.endswith(".jsonl"):
            continue
        updated_at = int(entry.stat().st_mtime * 1000)
        if now - updated_at > max_age_ms:
            os.remove(entry.path)
            removed += 1
            _remove_index_meta(session_dir, entry.name[:-len(".jsonl")])
            meta_path = _legacy_meta_path(session_dir, entry.name[:-len(".jsonl")])
            if os.path.isfile(meta_path):
                os.remove(meta_path)
    return removed
