"""
Append-only session event log.

This mirrors the TypeScript MiniCode session model closely enough for the web
runtime: messages are appended as events, compact boundaries reset the active
context on resume, and rename/fork/delete operate on the event log.
"""
from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..loop.messages import ChatMessage
from ..context.token_estimator import CLEAR_MARKER
from ..tools.permissions import PermissionRules


MAX_SESSION_NAME_LENGTH = 30
MAX_TITLE_LENGTH = MAX_SESSION_NAME_LENGTH
SESSION_RETENTION_MS = 30 * 24 * 60 * 60 * 1000


def validate_session_name(name: str, label: str = "name") -> Optional[str]:
    if not name:
        return f"{label} is empty"
    compact_name = "".join(ch for ch in name.strip() if not ch.isspace())
    if not compact_name:
        return f"{label} is empty"
    if len(compact_name) > MAX_SESSION_NAME_LENGTH:
        return f"{label} must be at most {MAX_SESSION_NAME_LENGTH} characters"
    return None


@dataclass
class SessionMeta:
    id: str
    title: str = ""
    created_at: int = 0
    updated_at: int = 0
    message_count: int = 0
    workspace: str = ""
    parent_session_id: str = ""
    root_session_id: str = ""
    character_name: str = ""


@dataclass
class Session:
    meta: SessionMeta
    messages: list[ChatMessage] = field(default_factory=list)
    permissions: PermissionRules = field(default_factory=PermissionRules)
    saved_event_tail: dict[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return self.meta.id


@dataclass
class TranscriptItem:
    role: str
    content: str = ""
    blocks: list[dict[str, Any]] = field(default_factory=list)
    tool_use_id: str = ""
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


def _session_folder_path(session_dir: str, session_id: str) -> str:
    return os.path.join(session_dir, session_id)


def _subsessions_folder_path(session_dir: str, root_session_id: str) -> str:
    return os.path.join(_session_folder_path(session_dir, root_session_id), "subsessions")


def _session_path(
    session_dir: str,
    session_id: str,
    root_session_id: str = "",
    parent_session_id: str = "",
) -> str:
    if parent_session_id or (root_session_id and root_session_id != session_id):
        root_id = root_session_id or parent_session_id
        return os.path.join(_subsessions_folder_path(session_dir, root_id), f"{session_id}.jsonl")
    return os.path.join(_session_folder_path(session_dir, session_id), "main.jsonl")


def _session_config_path(
    session_dir: str,
    session_id: str,
    root_session_id: str = "",
    parent_session_id: str = "",
) -> str:
    if parent_session_id or (root_session_id and root_session_id != session_id):
        root_id = root_session_id or parent_session_id
        return os.path.join(_subsessions_folder_path(session_dir, root_id), f"{session_id}.json")
    return os.path.join(_session_folder_path(session_dir, session_id), "session.json")


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
        "todo_reminder": "todo_reminder",
        "loop_end": "loop_end",
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


def _permissions_from_events(events: list[dict[str, Any]]) -> PermissionRules:
    permissions = PermissionRules()
    for event in events:
        if event.get("type") == "permissions":
            raw = event.get("permissions", {})
            if isinstance(raw, dict):
                permissions = PermissionRules.from_dict(raw)
    return permissions


def _write_event(path: str, event: dict[str, Any]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")


def _append_events(session_dir: str, session: Session, events: list[dict[str, Any]]) -> None:
    if not events:
        return
    path = _session_path(
        session_dir,
        session.meta.id,
        root_session_id=session.meta.root_session_id,
        parent_session_id=session.meta.parent_session_id,
    )
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        for event in events:
            f.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")


def _touch_session_log(session_dir: str, session: Session) -> None:
    path = _session_path(
        session_dir,
        session.meta.id,
        root_session_id=session.meta.root_session_id,
        parent_session_id=session.meta.parent_session_id,
    )
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).touch(exist_ok=True)


def _event_tail(events: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "message_ids": {event.get("uuid") for event in events if event.get("uuid")},
        "last_uuid": _last_event_uuid(events),
        "has_events": bool(events),
    }


def _read_session_config(
    session_dir: str,
    session_id: str,
    root_session_id: str = "",
    parent_session_id: str = "",
) -> dict[str, Any]:
    path = _session_config_path(
        session_dir,
        session_id,
        root_session_id=root_session_id,
        parent_session_id=parent_session_id,
    )
    if not os.path.isfile(path):
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def save_session_config(session_dir: str, session: Session) -> None:
    path = _session_config_path(
        session_dir,
        session.meta.id,
        root_session_id=session.meta.root_session_id,
        parent_session_id=session.meta.parent_session_id,
    )
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    data = {
        "id": session.meta.id,
        "title": session.meta.title,
        "workspace": session.meta.workspace,
        "parent_session_id": session.meta.parent_session_id,
        "root_session_id": session.meta.root_session_id,
        "character_name": session.meta.character_name,
        "permissions": session.permissions.to_dict(),
        "created_at": session.meta.created_at,
        "updated_at": session.meta.updated_at,
    }
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _sync_index_meta(session_dir, session.meta)


def update_session_permissions(session_dir: str, session: Session) -> None:
    session.meta.updated_at = _now_ms()
    save_session_config(session_dir, session)


def _meta_to_dict(meta: SessionMeta) -> dict[str, Any]:
    return {
        "id": meta.id,
        "title": meta.title,
        "created_at": meta.created_at,
        "updated_at": meta.updated_at,
        "message_count": meta.message_count,
        "workspace": meta.workspace,
        "parent_session_id": meta.parent_session_id,
        "root_session_id": meta.root_session_id,
        "character_name": meta.character_name,
    }


def _meta_from_dict(data: dict[str, Any]) -> SessionMeta:
    return SessionMeta(
        id=str(data.get("id", "")),
        title=str(data.get("title", "")),
        created_at=int(data.get("created_at", 0) or 0),
        updated_at=int(data.get("updated_at", 0) or 0),
        message_count=int(data.get("message_count", 0) or 0),
        workspace=str(data.get("workspace", "")),
        parent_session_id=str(data.get("parent_session_id", "")),
        root_session_id=str(data.get("root_session_id", "")),
        character_name=str(data.get("character_name", "")),
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
    if meta.parent_session_id:
        return
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
        if entry.is_dir() and os.path.isfile(os.path.join(entry.path, "main.jsonl")):
            session_id = entry.name
            metas[session_id] = _session_meta_from_file(session_dir, session_id)
    _write_session_index(session_dir, metas)
    return metas


def _last_event_uuid(events: list[dict[str, Any]]) -> Optional[str]:
    if not events:
        return None
    return events[-1].get("uuid")


def _message_event(
    message: ChatMessage,
    session_id: str,
    workspace: str,
    parent_uuid: Optional[str],
    root_session_id: str = "",
    parent_session_id: str = "",
    character_name: str = "",
) -> dict[str, Any]:
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
    if root_session_id:
        event["root_session_id"] = root_session_id
    if parent_session_id:
        event["parent_session_id"] = parent_session_id
    if character_name:
        event["character_name"] = character_name
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


def _session_meta_from_file(
    session_dir: str,
    session_id: str,
    root_session_id: str = "",
    parent_session_id: str = "",
) -> SessionMeta:
    path = _session_path(
        session_dir,
        session_id,
        root_session_id=root_session_id,
        parent_session_id=parent_session_id,
    )
    config = _read_session_config(
        session_dir,
        session_id,
        root_session_id=root_session_id,
        parent_session_id=parent_session_id,
    )
    events = _read_events(path)
    stat = os.stat(path) if os.path.isfile(path) else None
    workspace = ""
    legacy_title = ""
    character_name = ""
    for event in events:
        if event.get("cwd"):
            workspace = str(event["cwd"])
        if event.get("character_name"):
            character_name = str(event["character_name"])
        if workspace and character_name:
            break
    meta_path = _legacy_meta_path(session_dir, session_id)
    if os.path.isfile(meta_path):
        meta_data = json.loads(Path(meta_path).read_text(encoding="utf-8"))
        workspace = workspace or str(meta_data.get("workspace", ""))
        legacy_title = str(meta_data.get("title", ""))
    created_at = int(config.get("created_at", 0) or 0) or (int(stat.st_ctime * 1000) if stat else 0)
    config_updated = int(config.get("updated_at", 0) or 0)
    log_updated = int(stat.st_mtime * 1000) if stat else 0
    updated_at = max(config_updated, log_updated)
    return SessionMeta(
        id=session_id,
        title=str(config.get("title") or _title_from_events(events) or legacy_title or f"Session {session_id}"),
        created_at=created_at,
        updated_at=updated_at,
        message_count=sum(
            1 for event in events
            if isinstance(event.get("message"), dict)
            and event.get("type") != "todo_reminder"
        ),
        workspace=str(config.get("workspace") or workspace),
        parent_session_id=parent_session_id,
        root_session_id=root_session_id or session_id,
        character_name=str(config.get("character_name") or character_name or "general-purpose"),
    )


def create_session(
    session_dir: str,
    workspace: str = "",
    title: str = "",
    parent_session_id: str = "",
    root_session_id: str = "",
    character_name: str = "",
) -> Session:
    session_id = _generate_id()
    now = _now_ms()
    root_id = root_session_id or parent_session_id or session_id
    char_name = character_name or "general-purpose"
    session = Session(
        meta=SessionMeta(
            id=session_id,
            title=title or f"Session {session_id}",
            created_at=now,
            updated_at=now,
            workspace=workspace,
            parent_session_id=parent_session_id,
            root_session_id=root_id,
            character_name=char_name,
        ),
        permissions=PermissionRules(),
    )
    _touch_session_log(session_dir, session)
    save_session_config(session_dir, session)
    return session


def list_sessions(session_dir: str) -> list[SessionMeta]:
    _ensure_dir(session_dir)
    index_path = _index_path(session_dir)
    if os.path.isfile(index_path):
        metas = _read_session_index(session_dir)
    else:
        metas = _rebuild_session_index(session_dir)
    existing_ids = {
        entry.name
        for entry in os.scandir(session_dir)
        if entry.is_dir() and os.path.isfile(os.path.join(entry.path, "main.jsonl"))
    }
    if set(metas) != existing_ids:
        metas = _rebuild_session_index(session_dir)
    sessions = list(metas.values())
    sessions.sort(key=lambda s: s.updated_at, reverse=True)
    return sessions


def load_session(
    session_dir: str,
    session_id: str,
    root_session_id: str = "",
    parent_session_id: str = "",
) -> Optional[Session]:
    path = _session_path(
        session_dir,
        session_id,
        root_session_id=root_session_id,
        parent_session_id=parent_session_id,
    )
    if not os.path.isfile(path):
        return None
    events = _read_events(path)
    config = _read_session_config(
        session_dir,
        session_id,
        root_session_id=root_session_id,
        parent_session_id=parent_session_id,
    )
    active_events = _active_events(events)
    reconstructed_events = _reconstruct_microcompacted_events(_reconstruct_snipped_events(active_events))
    messages = [
        message
        for message in (_unwrap_message(event) for event in reconstructed_events)
        if message is not None
    ]
    permissions = PermissionRules.from_dict(config.get("permissions", {}))
    if not config:
        permissions = _permissions_from_events(events)
    return Session(
        meta=_session_meta_from_file(
            session_dir,
            session_id,
            root_session_id=root_session_id,
            parent_session_id=parent_session_id,
        ),
        messages=messages,
        permissions=permissions,
        saved_event_tail=_event_tail(events),
    )


def load_transcript(
    session_dir: str,
    session_id: str,
    root_session_id: str = "",
    parent_session_id: str = "",
) -> Optional[list[TranscriptItem]]:
    path = _session_path(
        session_dir,
        session_id,
        root_session_id=root_session_id,
        parent_session_id=parent_session_id,
    )
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
        if chat_message.role == "todo_reminder":
            continue
        items.append(TranscriptItem(
            role=chat_message.role,
            content=chat_message.content,
            blocks=[b.__dict__ for b in (chat_message.blocks or [])],
            tool_use_id=chat_message.tool_use_id,
            tool_name=chat_message.tool_name,
            input=chat_message.input,
            is_error=chat_message.is_error,
            timestamp=chat_message.timestamp,
        ))
    return items


def save_session(session_dir: str, session: Session, already_saved_count: int = 0) -> None:
    _ensure_dir(session_dir)
    _touch_session_log(session_dir, session)
    tail = session.saved_event_tail
    existing_ids = tail.setdefault("message_ids", set())
    if not isinstance(existing_ids, set):
        existing_ids = set(existing_ids)
        tail["message_ids"] = existing_ids
    parent_uuid = tail.get("last_uuid")
    messages = session.messages[1:] if session.messages and session.messages[0].role == "system" else session.messages
    events: list[dict[str, Any]] = []

    for index, message in enumerate(messages):
        if message.id and message.id in existing_ids:
            continue
        if not message.id and index < already_saved_count:
            continue
        event = _message_event(
            message,
            session.meta.id,
            session.meta.workspace,
            parent_uuid,
            root_session_id=session.meta.root_session_id,
            parent_session_id=session.meta.parent_session_id,
            character_name=session.meta.character_name,
        )
        parent_uuid = event["uuid"]
        events.append(event)
        existing_ids.add(event["uuid"])

    _append_events(session_dir, session, events)
    if events:
        tail["last_uuid"] = events[-1]["uuid"]
        tail["has_events"] = True
    _sync_index_meta(
        session_dir,
        _session_meta_from_file(
            session_dir,
            session.meta.id,
            root_session_id=session.meta.root_session_id,
            parent_session_id=session.meta.parent_session_id,
        ),
    )


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
        "root_session_id": session_id,
        "logical_parent_uuid": _last_event_uuid(existing_events),
        "compact_metadata": {
            "trigger": trigger,
            "pre_tokens": pre_tokens,
            "post_tokens": post_tokens,
        },
    }
    summary = ChatMessage.user(summary_text)
    summary_event = _message_event(summary, session_id, workspace, boundary_uuid, root_session_id=session_id)
    parent_uuid = summary_event["uuid"]
    events = [boundary, summary_event]
    for message in retained_messages:
        if message.role == "system":
            continue
        event = _message_event(message, session_id, workspace, parent_uuid, root_session_id=session_id)
        parent_uuid = event["uuid"]
        events.append(event)
    session = Session(meta=SessionMeta(id=session_id, workspace=workspace, root_session_id=session_id))
    _append_events(session_dir, session, events)
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
    session = load_session(session_dir, session_id)
    if session is None:
        session = Session(
            meta=SessionMeta(
                id=session_id,
                title=new_title,
                workspace=workspace,
                root_session_id=session_id,
                character_name="general-purpose",
            ),
            permissions=PermissionRules(),
        )
        _touch_session_log(session_dir, session)
    session.meta.title = new_title
    if workspace:
        session.meta.workspace = workspace
    session.meta.updated_at = _now_ms()
    save_session_config(session_dir, session)
    return True


def fork_session(session_dir: str, session_id: str) -> Optional[Session]:
    source = load_session(session_dir, session_id)
    if source is None or not source.messages:
        return None

    new_session = create_session(session_dir, workspace=source.meta.workspace)
    new_session.permissions = source.permissions.copy()
    new_session.messages = [ChatMessage.system("")] + source.messages
    save_session_config(session_dir, new_session)
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
    folder = _session_folder_path(session_dir, session_id)
    deleted = False
    if os.path.isdir(folder):
        shutil.rmtree(folder)
        deleted = True
    meta_path = _legacy_meta_path(session_dir, session_id)
    if os.path.isfile(meta_path):
        os.remove(meta_path)
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
        if not entry.is_dir():
            continue
        main_path = os.path.join(entry.path, "main.jsonl")
        if not os.path.isfile(main_path):
            continue
        updated_at = int(os.stat(main_path).st_mtime * 1000)
        if now - updated_at > max_age_ms:
            shutil.rmtree(entry.path)
            removed += 1
            _remove_index_meta(session_dir, entry.name)
            meta_path = _legacy_meta_path(session_dir, entry.name)
            if os.path.isfile(meta_path):
                os.remove(meta_path)
    return removed
