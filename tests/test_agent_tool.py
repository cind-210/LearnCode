from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional
from unittest.mock import patch

from src.loop.messages import AgentStep, ChatMessage
from src.loop.runner import AgentLoopConfig, _update_character_experiences
from src.sessions.characters import Character, ensure_general_purpose_character, load_characters
from src.sessions.subsessions import SubSessionRuntime
from src.tools.permissions import PermissionRules
from src.tools.subsession_tools import (
    FORK_SUBSESSION_TOOL_NAME,
    LIST_CHARACTERS_TOOL_NAME,
    LIST_SUBSESSIONS_TOOL_NAME,
    NEW_SUBSESSION_TOOL_NAME,
    SEND_MESSAGE_TOOL_NAME,
    build_subsession_tools,
)
from src.tools.builtin import build_builtin_registry
from src.tools.registry import ToolRegistry
from src.sessions.store import (
    create_session,
    list_sessions,
    load_session,
    load_transcript,
    save_session,
    update_session_permissions,
    validate_session_name,
)


class CapturingModelAdapter:
    calls = 0

    def __init__(self, tools: ToolRegistry):
        self.tools = tools

    async def next(
        self,
        messages: list[ChatMessage],
        on_delta: Optional[Callable[[dict[str, Any]], Awaitable[None]]] = None,
    ) -> AgentStep:
        CapturingModelAdapter.calls += 1
        tool_names = [tool.name for tool in self.tools.list()]
        user_messages = [message.content for message in messages if message.role == "user"]
        return AgentStep(type="assistant", content="last=" + user_messages[-1] + "\ntools=" + ",".join(tool_names))


class AgentToolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        asyncio.get_running_loop().set_debug(False)
        CapturingModelAdapter.calls = 0

    async def test_subsession_tools_create_send_and_resume_child_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = str(Path(tmp) / ".sessions")
            characters = [Character(name="general-purpose", description="General", prompt="General", tools=["*"])]
            registry = build_builtin_registry()
            registry.add_tools(build_subsession_tools(characters))
            runtime = SubSessionRuntime()

            async def model_factory(tools: ToolRegistry) -> CapturingModelAdapter:
                return CapturingModelAdapter(tools)

            context = {
                "workspace": tmp,
                "session_id": "parent-1",
                "tool_registry": registry,
                "model_adapter_factory": model_factory,
                "loop_config": AgentLoopConfig(workspace=tmp, session_id="parent-1", subsession_runtime=runtime, session_dir=session_dir),
                "characters": characters,
                "subsession_runtime": runtime,
            }

            listed = await registry.execute(LIST_CHARACTERS_TOOL_NAME, {}, context)
            self.assertTrue(listed.ok)
            self.assertIn("general-purpose: General", listed.output)

            result = await registry.execute(
                NEW_SUBSESSION_TOOL_NAME,
                {
                    "name": "worker",
                    "description": "Check tools",
                    "prompt": "System hint for worker.",
                    "message": "initial message",
                    "character": "general-purpose",
                },
                context,
            )

            self.assertTrue(result.ok)
            self.assertIn("SubSession created: worker", result.output)
            self.assertIn("last=initial message", result.output)
            self.assertNotIn("NewSubSession,", result.output)
            self.assertNotIn(",NewSubSession", result.output)
            self.assertEqual(len(runtime.list_sessions("parent-1")), 1)
            loaded_child = runtime.list_sessions("parent-1")[0]
            child_id = loaded_child.link.id
            self.assertEqual(loaded_child.link.prompt_addition, "System hint for worker.")
            persisted = load_session(
                session_dir,
                child_id,
                root_session_id="parent-1",
                parent_session_id="parent-1",
            )
            self.assertIsNotNone(persisted)
            self.assertTrue(any(message.content == "initial message" for message in persisted.messages))
            self.assertTrue((Path(session_dir) / "parent-1" / "subsessions" / "index.json").is_file())
            self.assertTrue((Path(session_dir) / "parent-1" / "subsessions" / f"{child_id}.jsonl").is_file())

            follow_up = await registry.execute(
                SEND_MESSAGE_TOOL_NAME,
                {"to": "worker", "message": "follow up"},
                context,
            )

            self.assertTrue(follow_up.ok)
            self.assertIn("last=follow up", follow_up.output)

            fresh_runtime = SubSessionRuntime()
            context["subsession_runtime"] = fresh_runtime
            context["loop_config"] = AgentLoopConfig(
                workspace=tmp,
                session_id="parent-1",
                subsession_runtime=fresh_runtime,
                session_dir=session_dir,
            )
            resumed = await registry.execute(
                SEND_MESSAGE_TOOL_NAME,
                {"to": "worker", "message": "after reload"},
                context,
            )

            self.assertTrue(resumed.ok)
            self.assertIn("last=after reload", resumed.output)
            self.assertEqual(len(fresh_runtime.list_sessions("parent-1")), 1)
            self.assertIsNotNone(load_session(
                session_dir,
                child_id,
                root_session_id="parent-1",
                parent_session_id="parent-1",
            ))

    async def test_subsession_without_character_uses_input_permissions_as_complete_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = str(Path(tmp) / ".sessions")
            registry = build_builtin_registry()
            registry.add_tools(build_subsession_tools([]))
            runtime = SubSessionRuntime()

            async def model_factory(tools: ToolRegistry) -> CapturingModelAdapter:
                return CapturingModelAdapter(tools)

            context = {
                "workspace": tmp,
                "session_id": "parent-no-char",
                "tool_registry": registry,
                "model_adapter_factory": model_factory,
                "loop_config": AgentLoopConfig(workspace=tmp, session_id="parent-no-char", subsession_runtime=runtime, session_dir=session_dir),
                "characters": [],
                "subsession_runtime": runtime,
            }

            result = await registry.execute(
                NEW_SUBSESSION_TOOL_NAME,
                {
                    "name": "plain",
                    "permissions": {
                        "allow": ["read_file"],
                        "ask": ["run_command(*)"],
                        "deny": ["write_file"],
                    },
                },
                context,
            )

            self.assertTrue(result.ok)
            loaded = runtime.get_session("parent-no-char", "plain")
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.link.description, "")
            self.assertEqual(loaded.link.character, "")
            self.assertEqual(loaded.status, "idle")
            self.assertEqual(loaded.session.messages, [])
            self.assertEqual(CapturingModelAdapter.calls, 0)
            self.assertEqual(loaded.session.permissions.allow, ["read_file"])
            self.assertEqual(loaded.session.permissions.ask, ["run_command(*)"])
            self.assertEqual(loaded.session.permissions.deny, ["write_file"])

    async def test_list_subsessions_includes_loaded_and_disk_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = str(Path(tmp) / ".sessions")
            registry = build_builtin_registry()
            registry.add_tools(build_subsession_tools([]))
            runtime = SubSessionRuntime()

            async def model_factory(tools: ToolRegistry) -> CapturingModelAdapter:
                return CapturingModelAdapter(tools)

            context = {
                "workspace": tmp,
                "session_id": "parent-list",
                "tool_registry": registry,
                "model_adapter_factory": model_factory,
                "loop_config": AgentLoopConfig(
                    workspace=tmp,
                    session_id="parent-list",
                    subsession_runtime=runtime,
                    session_dir=session_dir,
                    max_loaded_subsessions=1,
                ),
                "characters": [],
                "subsession_runtime": runtime,
            }

            first = await registry.execute(
                NEW_SUBSESSION_TOOL_NAME,
                {
                    "name": "alpha",
                    "description": "First child task",
                    "message": "alpha prompt",
                },
                context,
            )
            second = await registry.execute(
                NEW_SUBSESSION_TOOL_NAME,
                {
                    "name": "beta",
                    "description": "Second child task",
                    "message": "beta prompt",
                },
                context,
            )

            self.assertTrue(first.ok)
            self.assertTrue(second.ok)
            listed = await registry.execute(LIST_SUBSESSIONS_TOOL_NAME, {}, context)

            self.assertTrue(listed.ok)
            self.assertIn("alpha", listed.output)
            self.assertIn("beta", listed.output)
            self.assertIn("First child task", listed.output)
            self.assertIn("Second child task", listed.output)
            self.assertIn("idle-on-disk", listed.output)

    async def test_subsession_names_must_be_unique_within_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = str(Path(tmp) / ".sessions")
            registry = build_builtin_registry()
            registry.add_tools(build_subsession_tools([]))
            runtime = SubSessionRuntime()

            async def model_factory(tools: ToolRegistry) -> CapturingModelAdapter:
                return CapturingModelAdapter(tools)

            context = {
                "workspace": tmp,
                "session_id": "parent-unique",
                "tool_registry": registry,
                "model_adapter_factory": model_factory,
                "loop_config": AgentLoopConfig(workspace=tmp, session_id="parent-unique", subsession_runtime=runtime, session_dir=session_dir),
                "characters": [],
                "subsession_runtime": runtime,
            }

            first = await registry.execute(
                NEW_SUBSESSION_TOOL_NAME,
                {"name": "same", "message": "first"},
                context,
            )
            second = await registry.execute(
                NEW_SUBSESSION_TOOL_NAME,
                {"name": "same", "message": "second"},
                context,
            )

            self.assertTrue(first.ok)
            self.assertFalse(second.ok)
            self.assertIn("already exists", second.output)

            context["session_id"] = "other-parent"
            context["loop_config"] = AgentLoopConfig(
                workspace=tmp,
                session_id="other-parent",
                subsession_runtime=runtime,
                session_dir=session_dir,
            )
            other_parent = await registry.execute(
                NEW_SUBSESSION_TOOL_NAME,
                {"name": "same", "message": "other parent"},
                context,
            )
            self.assertTrue(other_parent.ok)

    async def test_empty_subsession_creation_does_not_start_conversation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = str(Path(tmp) / ".sessions")
            runtime = SubSessionRuntime()
            character = Character(name="helper", description="Helps", prompt="Help")
            parent = create_session(session_dir, workspace=tmp, title="Parent")
            save_session(session_dir, parent)

            loaded = runtime.create_empty_session(
                parent_session_id=parent.id,
                name="helper",
                description=character.description,
                character=character,
                workspace=tmp,
                config=AgentLoopConfig(workspace=tmp, session_id=parent.id, subsession_runtime=runtime, session_dir=session_dir),
                permissions=character.permissions,
            )

            self.assertEqual(loaded.link.name, "helper")
            self.assertEqual(loaded.link.description, "Helps")
            persisted = load_session(
                session_dir,
                loaded.link.id,
                root_session_id=parent.id,
                parent_session_id=parent.id,
            )
            self.assertIsNotNone(persisted)
            self.assertEqual([message.role for message in persisted.messages], [])

    async def test_subsession_name_uses_shared_session_name_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = str(Path(tmp) / ".sessions")
            registry = build_builtin_registry()
            registry.add_tools(build_subsession_tools([]))
            runtime = SubSessionRuntime()

            async def model_factory(tools: ToolRegistry) -> CapturingModelAdapter:
                return CapturingModelAdapter(tools)

            result = await registry.execute(
                NEW_SUBSESSION_TOOL_NAME,
                {
                    "name": "a" * 31,
                    "message": "too long",
                },
                {
                    "workspace": tmp,
                    "session_id": "parent-name-rule",
                    "tool_registry": registry,
                    "model_adapter_factory": model_factory,
                    "loop_config": AgentLoopConfig(workspace=tmp, session_id="parent-name-rule", subsession_runtime=runtime, session_dir=session_dir),
                    "characters": [],
                    "subsession_runtime": runtime,
                },
            )

            self.assertFalse(result.ok)
            self.assertIn("at most 30 characters", result.output)

    async def test_fork_subsession_copies_parent_context_then_sends_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = str(Path(tmp) / ".sessions")
            registry = build_builtin_registry()
            registry.add_tools(build_subsession_tools([]))
            runtime = SubSessionRuntime()
            parent_messages = [
                ChatMessage.system("parent system"),
                ChatMessage.user("parent request", timestamp=1),
                ChatMessage.assistant("parent answer", timestamp=2),
            ]

            async def model_factory(tools: ToolRegistry) -> CapturingModelAdapter:
                return CapturingModelAdapter(tools)

            context = {
                "workspace": tmp,
                "session_id": "parent-fork",
                "tool_registry": registry,
                "model_adapter_factory": model_factory,
                "loop_config": AgentLoopConfig(workspace=tmp, session_id="parent-fork", subsession_runtime=runtime, session_dir=session_dir),
                "characters": [],
                "subsession_runtime": runtime,
                "fork_source_messages": parent_messages,
            }

            result = await registry.execute(
                FORK_SUBSESSION_TOOL_NAME,
                {
                    "name": "forked",
                    "description": "Fork context",
                    "prompt": "Fork system hint.",
                    "message": "continue from fork",
                    "permissions": {"allow": ["read_file"]},
                },
                context,
            )

            self.assertTrue(result.ok)
            self.assertIn("SubSession forked: forked", result.output)
            self.assertIn("last=continue from fork", result.output)
            loaded = runtime.get_session("parent-fork", "forked")
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.link.prompt_addition, "Fork system hint.")
            contents = [message.content for message in loaded.session.messages]
            self.assertIn("parent request", contents)
            self.assertIn("parent answer", contents)
            self.assertIn("continue from fork", contents)
            self.assertEqual(loaded.session.permissions.allow, ["read_file"])

            persisted = load_session(
                session_dir,
                loaded.link.id,
                root_session_id="parent-fork",
                parent_session_id="parent-fork",
            )
            self.assertIsNotNone(persisted)
            persisted_contents = [message.content for message in persisted.messages]
            self.assertIn("parent request", persisted_contents)
            self.assertIn("continue from fork", persisted_contents)

    async def test_input_permissions_take_priority_over_character_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = str(Path(tmp) / ".sessions")
            characters = [
                Character(
                    name="worker",
                    description="Worker",
                    prompt="Worker",
                    permissions=PermissionRules(
                        allow=["run_command(*)", "read_file"],
                        ask=["write_file"],
                        deny=["grep_files"],
                    ),
                )
            ]
            registry = build_builtin_registry()
            registry.add_tools(build_subsession_tools(characters))
            runtime = SubSessionRuntime()

            async def model_factory(tools: ToolRegistry) -> CapturingModelAdapter:
                return CapturingModelAdapter(tools)

            context = {
                "workspace": tmp,
                "session_id": "parent-priority",
                "tool_registry": registry,
                "model_adapter_factory": model_factory,
                "loop_config": AgentLoopConfig(workspace=tmp, session_id="parent-priority", subsession_runtime=runtime, session_dir=session_dir),
                "characters": characters,
                "subsession_runtime": runtime,
            }

            result = await registry.execute(
                NEW_SUBSESSION_TOOL_NAME,
                {
                    "name": "priority",
                    "description": "Priority",
                    "message": "priority prompt",
                    "character": "worker",
                    "permissions": {
                        "allow": ["write_file"],
                        "ask": ["grep_files"],
                        "deny": ["run_command(*)"],
                    },
                },
                context,
            )

            self.assertTrue(result.ok)
            loaded = runtime.get_session("parent-priority", "priority")
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.session.permissions.allow, ["read_file", "write_file"])
            self.assertEqual(loaded.session.permissions.ask, ["grep_files"])
            self.assertEqual(loaded.session.permissions.deny, ["run_command(*)"])

    async def test_subsession_runtime_lru_unloads_idle_sessions_only_from_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = str(Path(tmp) / ".sessions")
            characters = [Character(name="general-purpose", description="General", prompt="General", tools=["*"])]
            registry = build_builtin_registry()
            registry.add_tools(build_subsession_tools(characters))
            runtime = SubSessionRuntime()

            async def model_factory(tools: ToolRegistry) -> CapturingModelAdapter:
                return CapturingModelAdapter(tools)

            context = {
                "workspace": tmp,
                "session_id": "parent-lru",
                "tool_registry": registry,
                "model_adapter_factory": model_factory,
                "loop_config": AgentLoopConfig(
                    workspace=tmp,
                    session_id="parent-lru",
                    subsession_runtime=runtime,
                    session_dir=session_dir,
                    max_loaded_subsessions=2,
                ),
                "characters": characters,
                "subsession_runtime": runtime,
            }

            child_ids = []
            for name in ("one", "two", "three"):
                result = await registry.execute(
                    NEW_SUBSESSION_TOOL_NAME,
                    {
                        "name": name,
                        "description": name,
                        "message": f"prompt {name}",
                        "character": "general-purpose",
                    },
                    context,
                )
                self.assertTrue(result.ok)
                child_ids.append(runtime.ensure_loaded_session(
                    parent_session_id="parent-lru",
                    target=name,
                    config=context["loop_config"],
                ).link.id)

            self.assertLessEqual(len(runtime.list_sessions("parent-lru")), 2)
            self.assertIsNone(runtime.get_session("parent-lru", "one"))
            for child_id in child_ids:
                self.assertIsNotNone(load_session(
                    session_dir,
                    child_id,
                    root_session_id="parent-lru",
                    parent_session_id="parent-lru",
                ))

            resumed = await registry.execute(
                SEND_MESSAGE_TOOL_NAME,
                {"to": "one", "message": "wake one"},
                context,
            )

            self.assertTrue(resumed.ok)
            self.assertIn("last=wake one", resumed.output)
            self.assertLessEqual(len(runtime.list_sessions("parent-lru")), 2)
            self.assertIsNotNone(runtime.get_session("parent-lru", "one"))

    async def test_child_session_tool_filter_uses_character_definition(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = str(Path(tmp) / ".sessions")
            characters = [
                Character(
                    name="reader",
                    description="Read only",
                    prompt="Read only agent",
                    tools=["read_file", "grep_files", NEW_SUBSESSION_TOOL_NAME],
                    disallowed_tools=["grep_files"],
                )
            ]
            registry = build_builtin_registry()
            registry.add_tools(build_subsession_tools(characters))
            runtime = SubSessionRuntime()

            async def model_factory(tools: ToolRegistry) -> CapturingModelAdapter:
                return CapturingModelAdapter(tools)

            result = await registry.execute(
                NEW_SUBSESSION_TOOL_NAME,
                {
                    "name": "readerone",
                    "description": "Read only",
                    "message": "show tools",
                    "character": "reader",
                },
                {
                    "workspace": tmp,
                    "session_id": "parent-2",
                    "tool_registry": registry,
                    "model_adapter_factory": model_factory,
                    "loop_config": AgentLoopConfig(workspace=tmp, session_id="parent-2", subsession_runtime=runtime, session_dir=session_dir),
                    "characters": characters,
                    "subsession_runtime": runtime,
                },
            )

            self.assertTrue(result.ok)
            self.assertIn("tools=read_file", result.output)
            self.assertNotIn("grep_files", result.output)
            self.assertNotIn("NewSubSession", result.output.split("tools=", 1)[1])


class SessionStoreTests(unittest.TestCase):
    def test_main_session_persists_as_folder_with_main_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = str(Path(tmp) / ".sessions")
            session = create_session(session_dir, workspace=tmp, title="Main")
            session.messages = [ChatMessage.user("hello", timestamp=1)]
            save_session(session_dir, session)

            self.assertTrue((Path(session_dir) / session.id / "main.jsonl").is_file())
            self.assertTrue((Path(session_dir) / session.id / "session.json").is_file())
            loaded = load_session(session_dir, session.id)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.meta.root_session_id, session.id)
            self.assertEqual(loaded.meta.parent_session_id, "")
            self.assertEqual([meta.id for meta in list_sessions(session_dir)], [session.id])

    def test_session_permissions_persist_in_config_not_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = str(Path(tmp) / ".sessions")
            session = create_session(session_dir, workspace=tmp, title="Main")
            session.permissions.allow_tool("read_file")
            update_session_permissions(session_dir, session)
            session.messages = [ChatMessage.user("hello", timestamp=1)]
            save_session(session_dir, session)

            history = (Path(session_dir) / session.id / "main.jsonl").read_text(encoding="utf-8")
            config = json.loads((Path(session_dir) / session.id / "session.json").read_text(encoding="utf-8"))
            loaded = load_session(session_dir, session.id)

            self.assertNotIn('"type":"permissions"', history)
            self.assertEqual(config["permissions"]["allow"], ["read_file"])
            self.assertEqual(loaded.permissions.allow, ["read_file"])

    def test_save_session_uses_tail_without_duplicate_history_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = str(Path(tmp) / ".sessions")
            session = create_session(session_dir, workspace=tmp, title="Main")
            session.messages = [ChatMessage.user("one", timestamp=1)]
            save_session(session_dir, session)
            session.messages.append(ChatMessage.assistant("two", timestamp=2))
            save_session(session_dir, session)

            history_lines = (Path(session_dir) / session.id / "main.jsonl").read_text(encoding="utf-8").splitlines()

            self.assertEqual(len(history_lines), 2)

    def test_session_name_validation_uses_single_30_character_rule(self) -> None:
        self.assertIsNone(validate_session_name("a" * 30, "title"))
        self.assertIsNone(validate_session_name("中文" * 15, "title"))
        self.assertIsNone(validate_session_name("hello!-1", "title"))
        self.assertEqual(validate_session_name("a" * 31, "title"), "title must be at most 30 characters")

    def test_load_transcript_supports_child_session_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = str(Path(tmp) / ".sessions")
            child = create_session(
                session_dir,
                workspace=tmp,
                title="Child",
                parent_session_id="parent-transcript",
                root_session_id="parent-transcript",
            )
            child.messages = [ChatMessage.user("child message", timestamp=1)]
            save_session(session_dir, child)

            transcript = load_transcript(
                session_dir,
                child.id,
                root_session_id="parent-transcript",
                parent_session_id="parent-transcript",
            )

            self.assertIsNotNone(transcript)
            self.assertEqual(transcript[0].content, "child message")


class CharacterTests(unittest.IsolatedAsyncioTestCase):
    async def test_loads_global_character_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "characters"
            char_dir = root / "reviewer"
            char_dir.mkdir(parents=True)
            (char_dir / "character.json").write_text(
                json.dumps({
                    "name": "reviewer",
                    "description": "Reviews code",
                    "prompt": "Review carefully.",
                    "skills": ["python"],
                    "tools": ["read_file"],
                    "permissions": {"deny": ["run_command(Remove-Item:*)"]},
                    "experiences": ["Prefer focused findings."],
                }),
                encoding="utf-8",
            )

            with patch("src.sessions.characters.LEARN_CODE_CHARACTERS_DIR", root):
                characters = load_characters()

            reviewer = next(character for character in characters if character.name == "reviewer")
            self.assertEqual(reviewer.description, "Reviews code")
            self.assertEqual(reviewer.skills, ["python"])
            self.assertEqual(reviewer.tools, ["read_file"])
            self.assertEqual(reviewer.permissions.deny, ["run_command(Remove-Item:*)"])
            self.assertIn("Prefer focused findings.", reviewer.system_prompt())

    async def test_ensure_general_purpose_character_creates_character_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "characters"

            with patch("src.sessions.characters.LEARN_CODE_CHARACTERS_DIR", root):
                ensure_general_purpose_character()
                characters = load_characters()

            path = root / "general-purpose" / "character.json"
            data = json.loads(path.read_text(encoding="utf-8"))

            self.assertTrue(path.is_file())
            self.assertTrue(any(character.name == "general-purpose" for character in characters))
            self.assertEqual(data["skills"], ["*"])
            self.assertEqual(data["tools"], ["*"])

    async def test_character_experience_update_appends_to_character_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "character.json"
            character = Character(
                name="worker",
                description="Worker",
                prompt="Use existing experiences.",
                experiences=["Existing experience."],
                source=str(source),
            )

            class ExperienceAdapter:
                async def next(
                    self,
                    messages: list[ChatMessage],
                    on_delta: Optional[Callable[[dict[str, Any]], Awaitable[None]]] = None,
                ) -> AgentStep:
                    return AgentStep(type="assistant", content="- New reusable experience.")

            with patch("src.loop.runner.load_characters", lambda: [character]):
                await _update_character_experiences(
                    character_name="worker",
                    removed_messages=[ChatMessage.user("Important old context", timestamp=1)],
                    model_adapter=ExperienceAdapter(),
                )

            data = json.loads(source.read_text(encoding="utf-8"))
            self.assertEqual(data["experiences"], ["Existing experience.", "New reusable experience."])


if __name__ == "__main__":
    unittest.main()
