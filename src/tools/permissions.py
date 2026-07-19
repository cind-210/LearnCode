"""
Permission system for tool execution.

Mirrors src/permissions.ts from the TypeScript version.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING, Union

from .command_permissions import (
    RUN_COMMAND_TOOL,
    command_prefix_rule,
    command_rule,
    parse_command,
    run_command_rule_matches_segment,
)

if TYPE_CHECKING:
    from ..sessions.templates import SessionTemplate


class PermissionMode(str, Enum):
    DEFAULT = "default"
    ACCEPT_EDITS = "acceptEdits"
    BYPASS_PERMISSIONS = "bypassPermissions"
    PLAN = "plan"


class PermissionOption(str, Enum):
    NEVER = "never"
    ALWAYS = "always"
    ASK = "ask"


class PermissionDecision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    ALWAYS = "always"
    NEVER = "never"
    ASK = "ask"


@dataclass
class PermissionRule:
    tool_name: str
    option: PermissionOption = PermissionOption.ASK


@dataclass
class PermissionRules:
    allow: list[str] = field(default_factory=list)
    deny: list[str] = field(default_factory=list)
    ask: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PermissionRules:
        return cls(
            allow=_string_list(data.get("allow", [])),
            deny=_string_list(data.get("deny", [])),
            ask=_string_list(data.get("ask", [])),
        )

    def to_dict(self) -> dict[str, list[str]]:
        return {
            "allow": list(self.allow),
            "deny": list(self.deny),
            "ask": list(self.ask),
        }

    def copy(self) -> PermissionRules:
        return PermissionRules(
            allow=list(self.allow),
            deny=list(self.deny),
            ask=list(self.ask),
        )

    def allow_tool(self, tool_name: str) -> None:
        _remove_rule(self.deny, tool_name)
        _remove_rule(self.ask, tool_name)
        _add_rule(self.allow, tool_name)

    def deny_tool(self, tool_name: str) -> None:
        _remove_rule(self.allow, tool_name)
        _remove_rule(self.ask, tool_name)
        _add_rule(self.deny, tool_name)


@dataclass
class ToolPermissionContext:
    mode: PermissionMode = PermissionMode.DEFAULT
    additional_directories: list[str] = field(default_factory=list)
    rules: PermissionRules = field(default_factory=PermissionRules)


def _string_list(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, str)]


def _add_rule(
    rules: list[str],
    rule: str,
) -> None:
    if rule not in rules:
        rules.append(rule)


def _remove_rule(rules: list[str], rule: str) -> None:
    while rule in rules:
        rules.remove(rule)


def _rule_matches(tool_name: str, rule: str) -> bool:
    if rule == tool_name or rule == "*":
        return True
    if rule.endswith("__*"):
        return tool_name.startswith(rule[:-1])
    if rule.startswith("Agent(") and rule.endswith(")"):
        return tool_name == "Agent"
    if rule.startswith("mcp__"):
        parts = rule.split("__")
        if len(parts) == 2:
            return tool_name == rule or tool_name.startswith(f"{rule}__")
    return False


def _has_matching_rule(rules: list[str], tool_name: str) -> bool:
    for rule in rules:
        if _rule_matches(tool_name, rule):
            return True
    return False


@dataclass
class PermissionConfig:
    mode: PermissionMode = PermissionMode.DEFAULT
    additional_directories: list[str] = field(default_factory=list)
    rules: list[PermissionRule] = field(default_factory=list)
    deny_commands: list[str] = field(default_factory=list)
    ask_commands: list[str] = field(default_factory=list)
    permission_rules: PermissionRules = field(default_factory=PermissionRules)

    @classmethod
    def from_runtime_config(cls, config: dict[str, Any]) -> PermissionConfig:
        permission_config = PermissionConfig(
            mode=PermissionMode(config.get("mode", PermissionMode.DEFAULT.value)),
            additional_directories=config.get("additionalDirectories", []),
            rules=[PermissionRule(tool_name=r["toolName"], option=PermissionOption(r.get("option", "ask"))) for r in config.get("rules", [])],
            deny_commands=config.get("denyCommands", []),
            ask_commands=config.get("askCommands", []),
            permission_rules=PermissionRules.from_dict(config),
        )
        return permission_config


def load_permission_config(path: Union[str, Path]) -> PermissionConfig:
    file_path = Path(path)
    if not file_path.exists():
        return PermissionConfig()
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        return PermissionConfig()
    return PermissionConfig.from_runtime_config(data)


def load_permission_rules(path: Union[str, Path]) -> PermissionRules:
    return load_permission_config(path).permission_rules


@dataclass
class PermissionRequest:
    tool_name: str
    input: Any
    message: str
    reason: str = ""
    suggested_rules: list[str] = field(default_factory=list)
    segments: list[str] = field(default_factory=list)


@dataclass
class PermissionResponse:
    decision: PermissionDecision
    reason: str = ""
    apply_to_session: bool = False
    rules: list[str] = field(default_factory=list)


PermissionCallback = Any  # async (request: PermissionRequest) -> PermissionResponse


@dataclass
class PermissionResolver:
    config: PermissionConfig
    callback: Optional[PermissionCallback] = None
    permission_context: Optional[ToolPermissionContext] = None
    _session_allow: set[str] = field(default_factory=set)
    _session_deny: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        if self.permission_context is None:
            self.permission_context = ToolPermissionContext(
                mode=self.config.mode,
                additional_directories=list(self.config.additional_directories),
                rules=self.config.permission_rules.copy(),
            )
            for rule in self.config.rules:
                if rule.option == PermissionOption.ALWAYS:
                    _add_rule(self.permission_context.rules.allow, rule.tool_name)
                elif rule.option == PermissionOption.NEVER:
                    _add_rule(self.permission_context.rules.deny, rule.tool_name)
                else:
                    _add_rule(self.permission_context.rules.ask, rule.tool_name)

    def set_callback(self, callback: PermissionCallback) -> None:
        self.callback = callback

    def _check_rule(self, tool_name: str) -> Optional[PermissionDecision]:
        if self.permission_context:
            if _has_matching_rule(self.permission_context.rules.deny, tool_name):
                return PermissionDecision.DENY
            if _has_matching_rule(self.permission_context.rules.allow, tool_name):
                return PermissionDecision.ALLOW
            if _has_matching_rule(self.permission_context.rules.ask, tool_name):
                return PermissionDecision.ASK
        for rule in self.config.rules:
            if rule.tool_name == tool_name:
                if rule.option == PermissionOption.ALWAYS:
                    return PermissionDecision.ALLOW
                elif rule.option == PermissionOption.NEVER:
                    return PermissionDecision.DENY
                return None
        return None

    def _check_run_command_segment(self, segment: str) -> Optional[PermissionDecision]:
        if not self.permission_context:
            return None
        rules = self.permission_context.rules
        for rule in rules.deny:
            if run_command_rule_matches_segment(rule, segment):
                return PermissionDecision.DENY
        for rule in rules.ask:
            if run_command_rule_matches_segment(rule, segment):
                return PermissionDecision.ASK
        for rule in rules.allow:
            if run_command_rule_matches_segment(rule, segment):
                return PermissionDecision.ALLOW
        return None

    async def _check_run_command(self, request: PermissionRequest) -> Optional[PermissionResponse]:
        if request.tool_name != RUN_COMMAND_TOOL:
            return None
        command = ""
        if isinstance(request.input, dict):
            command = str(request.input.get("command", ""))
        if not command.strip():
            return PermissionResponse(decision=PermissionDecision.DENY, reason="Command is empty")

        parsed = await parse_command(command)
        request.segments = parsed.segments
        if not parsed.valid:
            return PermissionResponse(
                decision=PermissionDecision.DENY,
                reason=f"Command parse failed for {parsed.shell}: {parsed.reason}",
            )

        if not parsed.segments:
            return PermissionResponse(decision=PermissionDecision.ALLOW)

        decisions = [self._check_run_command_segment(segment) for segment in parsed.segments]
        if PermissionDecision.DENY in decisions:
            denied = parsed.segments[decisions.index(PermissionDecision.DENY)]
            return PermissionResponse(decision=PermissionDecision.DENY, reason=f"Command denied by rule: {denied}")
        if PermissionDecision.ASK not in decisions:
            return PermissionResponse(decision=PermissionDecision.ALLOW)

        request.reason = "Command requires permission for one or more shell segments."
        request.suggested_rules = [
            command_prefix_rule(segment) for segment, decision in zip(parsed.segments, decisions)
            if decision == PermissionDecision.ASK
        ]
        if self.callback:
            return await self.callback(request)
        return PermissionResponse(decision=PermissionDecision.DENY, reason="Permission required but no permission handler is available")

    def _check_session(self, tool_name: str) -> Optional[PermissionDecision]:
        if tool_name in self._session_allow:
            return PermissionDecision.ALLOW
        if tool_name in self._session_deny:
            return PermissionDecision.DENY
        return None

    async def check(self, request: PermissionRequest) -> PermissionResponse:
        session = self._check_session(request.tool_name)
        if session is not None:
            return PermissionResponse(decision=session)

        if self.config.mode == PermissionMode.BYPASS_PERMISSIONS:
            return PermissionResponse(decision=PermissionDecision.ALLOW)

        if self.config.mode == PermissionMode.PLAN:
            return PermissionResponse(decision=PermissionDecision.DENY, reason="目前处于计划模式，只能读取数据")

        rule = self._check_rule(request.tool_name)
        if rule is not None:
            if rule == PermissionDecision.ASK and self.callback:
                return await self.callback(request)
            if rule == PermissionDecision.ASK:
                return PermissionResponse(decision=PermissionDecision.DENY, reason="Permission required but no permission handler is available")
            return PermissionResponse(decision=rule)

        run_command = await self._check_run_command(request)
        if run_command is not None:
            return run_command

        if self.callback:
            return await self.callback(request)

        return PermissionResponse(decision=PermissionDecision.ALLOW)

    def apply_session_decision(
        self,
        tool_name: str,
        decision: PermissionDecision,
        rules: Optional[list[str]] = None,
    ) -> None:
        effective_rules = rules or [tool_name]
        if decision == PermissionDecision.ALWAYS:
            if self.permission_context:
                for rule in effective_rules:
                    self._session_allow.add(rule)
                    _add_rule(self.permission_context.rules.allow, rule)
        elif decision == PermissionDecision.NEVER:
            if self.permission_context:
                for rule in effective_rules:
                    self._session_deny.add(rule)
                    _add_rule(self.permission_context.rules.deny, rule)


@dataclass
class Sandbox:
    permission_context: ToolPermissionContext
    workspace: str
    agent_id: str = "main"
    session_id: Optional[str] = None
    app_state: Any = None

    @classmethod
    def from_config(
        cls,
        config: PermissionConfig,
        workspace: str,
        agent_id: str = "main",
        session_id: Optional[str] = None,
        app_state: Any = None,
    ) -> Sandbox:
        resolver = PermissionResolver(config=config)
        return cls(
            permission_context=resolver.permission_context or ToolPermissionContext(mode=config.mode),
            workspace=workspace,
            agent_id=agent_id,
            session_id=session_id,
            app_state=app_state,
        )

    def to_tool_context(self) -> dict[str, Any]:
        return {
            "workspace": self.workspace,
            "sandbox": self,
            "agent_id": self.agent_id,
            "session_id": self.session_id,
            "app_state": self.app_state,
        }

    def allows_tool_definition(self, tool_name: str) -> bool:
        return not _has_matching_rule(self.permission_context.rules.deny, tool_name)

    def fork_for_agent(
        self,
        agent_id: str,
        allow_rules: Optional[list[str]] = None,
        deny_rules: Optional[list[str]] = None,
        permission_mode: Optional[PermissionMode] = None,
    ) -> Sandbox:
        permission_context = ToolPermissionContext(
            mode=permission_mode or self.permission_context.mode,
            additional_directories=list(self.permission_context.additional_directories),
            rules=self.permission_context.rules.copy(),
        )
        for rule in allow_rules or []:
            _add_rule(permission_context.rules.allow, rule)
        for rule in deny_rules or []:
            _add_rule(permission_context.rules.deny, rule)
        return Sandbox(
            permission_context=permission_context,
            workspace=self.workspace,
            agent_id=agent_id,
            session_id=self.session_id,
            app_state=self.app_state,
        )

    def fork_for_session_template(
        self,
        template: SessionTemplate,
        session_id: Optional[str] = None,
    ) -> Sandbox:
        allow_rules = template.tools if template.tools and template.tools != ["*"] else None
        return self.fork_for_agent(
            agent_id=session_id or template.name,
            allow_rules=allow_rules,
            deny_rules=template.disallowed_tools,
            permission_mode=template.permission_mode,
        )


DEFAULT_PERMISSION_RESOLVER = PermissionResolver(config=PermissionConfig())


def get_default_permission_resolver() -> PermissionResolver:
    return DEFAULT_PERMISSION_RESOLVER


def reset_default_permission_resolver() -> None:
    global DEFAULT_PERMISSION_RESOLVER
    DEFAULT_PERMISSION_RESOLVER = PermissionResolver(config=PermissionConfig())
