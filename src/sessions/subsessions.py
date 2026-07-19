"""Loaded child sessions and parent-child session links."""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from ..loop.messages import ChatMessage
from ..loop.runner import AgentLoopConfig, AgentLoopState, run_agent_loop
from ..tools.permissions import PermissionRules
from ..tools.registry import ToolRegistry
from .store import Session, create_session, load_session, save_session
from .characters import Character


SUBSESSION_DIR = "subsessions"
SUBSESSION_INDEX = "index.json"


@dataclass
class SubSessionLink:
    id: str
    name: str
    description: str
    character: str
    prompt_addition: str
    parent_session_id: str
    workspace: str
    created_at: int
    updated_at: int


@dataclass
class LoadedSubSession:
    link: SubSessionLink
    session: Session
    status: str = "idle"
    task: asyncio.Task | None = None
    state: AgentLoopState | None = None


class SubSessionRuntime:
    def __init__(self) -> None:
        self._loaded: dict[str, LoadedSubSession] = {}
        self._names: dict[tuple[str, str], str] = {}

    def list_sessions(self, parent_session_id: str = "") -> list[LoadedSubSession]:
        return [
            loaded for loaded in self._loaded.values()
            if not parent_session_id or loaded.link.parent_session_id == parent_session_id
        ]

    def list_links(self, session_dir: str, parent_session_id: str) -> list[SubSessionLink]:
        links = []
        for raw in self._read_index(session_dir, parent_session_id).values():
            if isinstance(raw, dict):
                links.append(_link_from_dict(raw, parent_session_id))
        links.sort(key=lambda item: item.updated_at, reverse=True)
        return links

    def name_exists(self, session_dir: str, parent_session_id: str, name: str) -> bool:
        target = name.strip()
        if not target:
            return False
        return any(link.name == target for link in self.list_links(session_dir, parent_session_id))

    def get_session(self, parent_session_id: str, target: str) -> LoadedSubSession | None:
        if target in self._loaded:
            loaded = self._loaded[target]
            if not parent_session_id or loaded.link.parent_session_id == parent_session_id:
                return loaded
            return None
        session_id = self._names.get((parent_session_id, target))
        return self._loaded.get(session_id or "")

    async def create_session(
        self,
        *,
        parent_session_id: str,
        name: str,
        description: str,
        character: Character | None,
        prompt: str,
        message: str = "",
        workspace: str,
        config: AgentLoopConfig,
        registry: ToolRegistry,
        model_factory: Any,
        permissions: PermissionRules,
    ) -> tuple[LoadedSubSession, str]:
        child = create_session(
            config.session_dir,
            workspace=workspace,
            title=name or (character.name if character else "SubSession"),
            parent_session_id=parent_session_id,
            root_session_id=parent_session_id,
            character_name=character.name if character else "",
        )
        child.permissions = permissions
        save_session(config.session_dir, child)
        now = int(time.time() * 1000)
        link = SubSessionLink(
            id=child.meta.id,
            name=name or child.meta.id,
            description=description,
            character=character.name if character else "",
            prompt_addition=prompt,
            parent_session_id=parent_session_id,
            workspace=workspace,
            created_at=now,
            updated_at=now,
        )
        self._add_link(config.session_dir, link)
        loaded = self._load_into_memory(link, child)
        output = "Created idle child session without running the loop."
        if message.strip():
            output = await self.send_message(
                parent_session_id=parent_session_id,
                target=child.meta.id,
                message=message,
                character=character,
                config=config,
                registry=registry,
                model_factory=model_factory,
            )
        return loaded, output

    def create_empty_session(
        self,
        *,
        parent_session_id: str,
        name: str,
        description: str,
        character: Character | None,
        prompt_addition: str = "",
        workspace: str,
        config: AgentLoopConfig,
        permissions: PermissionRules,
    ) -> LoadedSubSession:
        child = create_session(
            config.session_dir,
            workspace=workspace,
            title=name or (character.name if character else "SubSession"),
            parent_session_id=parent_session_id,
            root_session_id=parent_session_id,
            character_name=character.name if character else "",
        )
        child.permissions = permissions
        save_session(config.session_dir, child)
        now = int(time.time() * 1000)
        link = SubSessionLink(
            id=child.meta.id,
            name=name or child.meta.id,
            description=description,
            character=character.name if character else "",
            prompt_addition=prompt_addition,
            parent_session_id=parent_session_id,
            workspace=workspace,
            created_at=now,
            updated_at=now,
        )
        self._add_link(config.session_dir, link)
        loaded = self._load_into_memory(link, child)
        self._enforce_loaded_limit(parent_session_id, config, keep_session_id=loaded.link.id)
        return loaded

    async def fork_session(
        self,
        *,
        parent_session_id: str,
        name: str,
        description: str,
        character: Character | None,
        prompt: str,
        message: str = "",
        workspace: str,
        source_messages: list[ChatMessage],
        config: AgentLoopConfig,
        registry: ToolRegistry,
        model_factory: Any,
        permissions: PermissionRules,
    ) -> tuple[LoadedSubSession, str]:
        child = create_session(
            config.session_dir,
            workspace=workspace,
            title=name or (character.name if character else "SubSession"),
            parent_session_id=parent_session_id,
            root_session_id=parent_session_id,
            character_name=character.name if character else "",
        )
        child.permissions = permissions
        child.messages = _clone_messages(source_messages)
        save_session(config.session_dir, child)
        now = int(time.time() * 1000)
        link = SubSessionLink(
            id=child.meta.id,
            name=name or child.meta.id,
            description=description,
            character=character.name if character else "",
            prompt_addition=prompt,
            parent_session_id=parent_session_id,
            workspace=workspace,
            created_at=now,
            updated_at=now,
        )
        self._add_link(config.session_dir, link)
        loaded = self._load_into_memory(link, child)
        output = "Created idle forked child session without running the loop."
        if message.strip():
            output = await self.send_message(
                parent_session_id=parent_session_id,
                target=child.meta.id,
                message=message,
                character=character,
                config=config,
                registry=registry,
                model_factory=model_factory,
            )
        return loaded, output

    def ensure_loaded_session(
        self,
        *,
        parent_session_id: str,
        target: str,
        config: AgentLoopConfig,
    ) -> LoadedSubSession | None:
        existing = self.get_session(parent_session_id, target)
        if existing is not None:
            self._enforce_loaded_limit(parent_session_id, config, keep_session_id=existing.link.id)
            return existing
        link = self._find_link(config.session_dir, parent_session_id, target)
        if link is None:
            return None
        session = load_session(
            config.session_dir,
            link.id,
            root_session_id=link.parent_session_id,
            parent_session_id=link.parent_session_id,
        )
        if session is None:
            return None
        loaded = self._load_into_memory(link, session)
        self._enforce_loaded_limit(parent_session_id, config, keep_session_id=loaded.link.id)
        return loaded

    async def send_message(
        self,
        *,
        parent_session_id: str,
        target: str,
        message: str,
        character: Character | None,
        config: AgentLoopConfig,
        registry: ToolRegistry,
        model_factory: Any,
    ) -> str:
        loaded = self.ensure_loaded_session(
            parent_session_id=parent_session_id,
            target=target,
            config=config,
        )
        if loaded is None:
            return f"SubSession not found on disk: {target}"
        if loaded.status == "running":
            return f"SubSession is already running: {loaded.link.name}"
        if character is None and loaded.link.character:
            return f"Character not found for existing child session: {loaded.link.character}"

        loaded.status = "running"
        self._sync_link_activity(loaded)
        self._add_link(config.session_dir, loaded.link)
        child_config = self._child_config(config, character, loaded)
        model_adapter = await model_factory(registry)
        state = AgentLoopState(messages=loaded.session.messages)
        loaded.state = state

        async def on_messages_changed(messages: list[ChatMessage]) -> None:
            loaded.session.messages = messages
            self._sync_link_activity(loaded)
            self._add_link(config.session_dir, loaded.link)
            save_session(config.session_dir, loaded.session)

        task = asyncio.create_task(run_agent_loop(
            user_input=message,
            model_adapter=model_adapter,
            config=child_config,
            state=state,
            tool_registry=registry,
            on_messages_changed=on_messages_changed,
        ))
        loaded.task = task
        try:
            result = await task
        except asyncio.CancelledError:
            self._close_open_tool_calls(state.messages)
            loaded.session.messages = state.messages
            save_session(config.session_dir, loaded.session)
            loaded.status = "idle"
            loaded.task = None
            loaded.state = None
            raise

        loaded.session.messages = result.messages
        loaded.session.permissions = child_config.permissions or loaded.session.permissions
        self._sync_link_activity(loaded)
        self._add_link(config.session_dir, loaded.link)
        save_session(config.session_dir, loaded.session)
        loaded.status = "idle"
        loaded.task = None
        loaded.state = None
        self._enforce_loaded_limit(parent_session_id, config)
        return _last_assistant_content(result.messages) or "(no output)"

    async def unload_session(self, parent_session_id: str, target: str, config: AgentLoopConfig) -> str:
        loaded = self.get_session(parent_session_id, target)
        if loaded is None:
            return f"SubSession not loaded: {target}"
        if loaded.task and not loaded.task.done():
            loaded.task.cancel()
            try:
                await loaded.task
            except asyncio.CancelledError:
                pass
        if loaded.state is not None:
            self._close_open_tool_calls(loaded.state.messages)
            loaded.session.messages = loaded.state.messages
        save_session(config.session_dir, loaded.session)
        self._unload_loaded(loaded.link.id)
        return f"SubSession unloaded: {loaded.link.name} ({loaded.link.id})"

    def _child_config(
        self,
        config: AgentLoopConfig,
        character: Character | None,
        loaded: LoadedSubSession,
    ) -> AgentLoopConfig:
        from dataclasses import replace

        return replace(
            config,
            custom_system_prompt=_system_prompt(character, loaded.link.workspace, loaded.link.prompt_addition),
            max_turns=(character.max_turns if character and character.max_turns else min(config.max_turns, 20)),
            permission_mode=(character.permission_mode if character and character.permission_mode else config.permission_mode),
            permissions=loaded.session.permissions,
            session_id=loaded.session.meta.id,
            character_name=character.name if character else "",
        )

    def _load_into_memory(self, link: SubSessionLink, session: Session) -> LoadedSubSession:
        loaded = LoadedSubSession(link=link, session=session)
        self._loaded[link.id] = loaded
        if link.name:
            self._names[(link.parent_session_id, link.name)] = link.id
        self._sync_link_activity(loaded)
        return loaded

    def _sync_link_activity(self, loaded: LoadedSubSession) -> None:
        last_timestamp = _last_message_timestamp(loaded.session.messages)
        if last_timestamp:
            loaded.link.updated_at = last_timestamp
        if loaded.link.name:
            self._names[(loaded.link.parent_session_id, loaded.link.name)] = loaded.link.id

    def _unload_loaded(self, session_id: str) -> None:
        loaded = self._loaded.pop(session_id, None)
        if loaded and loaded.link.name:
            self._names.pop((loaded.link.parent_session_id, loaded.link.name), None)

    def _enforce_loaded_limit(
        self,
        parent_session_id: str,
        config: AgentLoopConfig,
        keep_session_id: str = "",
    ) -> None:
        limit = config.max_loaded_subsessions if config.max_loaded_subsessions > 0 else 10
        if limit <= 0:
            limit = 10
        loaded = [
            session for session in self._loaded.values()
            if session.link.parent_session_id == parent_session_id
        ]
        if len(loaded) <= limit:
            return

        loaded.sort(key=lambda item: item.link.updated_at)
        for candidate in loaded:
            if len([session for session in self._loaded.values() if session.link.parent_session_id == parent_session_id]) <= limit:
                break
            if candidate.link.id == keep_session_id:
                continue
            if candidate.status != "idle" or candidate.task is not None:
                continue
            if candidate.state is not None:
                self._close_open_tool_calls(candidate.state.messages)
                candidate.session.messages = candidate.state.messages
                save_session(config.session_dir, candidate.session)
            self._unload_loaded(candidate.link.id)

    def _add_link(self, session_dir: str, link: SubSessionLink) -> None:
        index = self._read_index(session_dir, link.parent_session_id)
        index[link.name or link.id] = link.__dict__
        self._write_index(session_dir, link.parent_session_id, index)

    def _find_link(self, session_dir: str, parent_session_id: str, target: str) -> SubSessionLink | None:
        links = self._read_index(session_dir, parent_session_id)
        raw = links.get(target)
        if raw is None:
            for candidate in links.values():
                if candidate.get("id") == target or candidate.get("name") == target:
                    raw = candidate
                    break
        if not isinstance(raw, dict):
            return None
        return _link_from_dict(raw, parent_session_id)

    def _read_index(self, session_dir: str, parent_session_id: str) -> dict[str, dict[str, Any]]:
        path = Path(session_dir) / parent_session_id / SUBSESSION_DIR / SUBSESSION_INDEX
        if not path.is_file():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}

    def _write_index(self, session_dir: str, parent_session_id: str, data: dict[str, dict[str, Any]]) -> None:
        path = Path(session_dir) / parent_session_id / SUBSESSION_DIR / SUBSESSION_INDEX
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _close_open_tool_calls(self, messages: list[ChatMessage]) -> None:
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


def _last_assistant_content(messages: list[ChatMessage]) -> str:
    for message in reversed(messages):
        if message.role in ("assistant", "assistant_progress") and message.content:
            return message.content
    return ""


def _last_message_timestamp(messages: list[ChatMessage]) -> int:
    for message in reversed(messages):
        if message.timestamp > 0:
            return message.timestamp
    return 0


def _clone_messages(messages: list[ChatMessage]) -> list[ChatMessage]:
    return [
        replace(
            message,
            id=None,
            removed_message_ids=list(message.removed_message_ids),
            cleared_message_ids=list(message.cleared_message_ids),
        )
        for message in messages
    ]


def _link_from_dict(raw: dict[str, Any], parent_session_id: str) -> SubSessionLink:
    return SubSessionLink(
        id=str(raw.get("id", "")),
        name=str(raw.get("name", "")),
        description=str(raw.get("description", "")),
        character=str(raw.get("character", "")),
        prompt_addition=str(raw.get("prompt_addition", "")),
        parent_session_id=str(raw.get("parent_session_id", parent_session_id)),
        workspace=str(raw.get("workspace", "")),
        created_at=int(raw.get("created_at", 0) or 0),
        updated_at=int(raw.get("updated_at", 0) or 0),
    )


def _system_prompt(character: Character | None, workspace: str, prompt_addition: str = "") -> str:
    prompt = character.system_prompt() if character else ""
    prompt = prompt or "You are a focused LearnCode sub session."
    if prompt_addition.strip():
        prompt = f"{prompt}\n\n{prompt_addition.strip()}"
    return (
        f"{prompt}\n\n"
        f"Workspace: {workspace}\n\n"
        "You are running as a child session. Do not assume access to the parent conversation. "
        "Return concise messages for the parent session."
    )
