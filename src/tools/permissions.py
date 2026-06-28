"""
Permission system for tool execution.

Mirrors src/permissions.ts from the TypeScript version.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, Union


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


@dataclass
class PermissionRule:
    tool_name: str
    option: PermissionOption = PermissionOption.ASK


@dataclass
class PermissionConfig:
    mode: PermissionMode = PermissionMode.DEFAULT
    additional_directories: list[str] = field(default_factory=list)
    rules: list[PermissionRule] = field(default_factory=list)
    deny_commands: list[str] = field(default_factory=list)
    ask_commands: list[str] = field(default_factory=list)

    @classmethod
    def from_runtime_config(cls, config: dict[str, Any]) -> PermissionConfig:
        return PermissionConfig(
            mode=PermissionMode(config.get("mode", PermissionMode.DEFAULT.value)),
            additional_directories=config.get("additionalDirectories", []),
            rules=[PermissionRule(tool_name=r["toolName"], option=PermissionOption(r.get("option", "ask"))) for r in config.get("rules", [])],
            deny_commands=config.get("denyCommands", []),
            ask_commands=config.get("askCommands", []),
        )


@dataclass
class PermissionRequest:
    tool_name: str
    input: Any
    message: str
    reason: str = ""


@dataclass
class PermissionResponse:
    decision: PermissionDecision
    reason: str = ""
    apply_to_session: bool = False


PermissionCallback = Any  # async (request: PermissionRequest) -> PermissionResponse


@dataclass
class PermissionResolver:
    config: PermissionConfig
    callback: Optional[PermissionCallback] = None
    _session_allow: set[str] = field(default_factory=set)
    _session_deny: set[str] = field(default_factory=set)

    def set_callback(self, callback: PermissionCallback) -> None:
        self.callback = callback

    def _check_rule(self, tool_name: str) -> Optional[PermissionDecision]:
        for rule in self.config.rules:
            if rule.tool_name == tool_name:
                if rule.option == PermissionOption.ALWAYS:
                    return PermissionDecision.ALLOW
                elif rule.option == PermissionOption.NEVER:
                    return PermissionDecision.DENY
                return None
        return None

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
            return PermissionResponse(decision=rule)

        if self.callback:
            return await self.callback(request)

        return PermissionResponse(decision=PermissionDecision.ALLOW)

    def apply_session_decision(self, tool_name: str, decision: PermissionDecision) -> None:
        if decision == PermissionDecision.ALWAYS:
            self._session_allow.add(tool_name)
        elif decision == PermissionDecision.NEVER:
            self._session_deny.add(tool_name)


DEFAULT_PERMISSION_RESOLVER = PermissionResolver(config=PermissionConfig())


def get_default_permission_resolver() -> PermissionResolver:
    return DEFAULT_PERMISSION_RESOLVER


def reset_default_permission_resolver() -> None:
    global DEFAULT_PERMISSION_RESOLVER
    DEFAULT_PERMISSION_RESOLVER = PermissionResolver(config=PermissionConfig())