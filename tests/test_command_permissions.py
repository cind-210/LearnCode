from __future__ import annotations

import unittest
import asyncio
from unittest.mock import patch

from src.tools.command_permissions import ParsedCommand, parse_command, powershell_executable
from src.tools.permissions import (
    PermissionConfig,
    PermissionDecision,
    PermissionRequest,
    PermissionResolver,
    PermissionResponse,
    PermissionRules,
)


def _request(command: str) -> PermissionRequest:
    return PermissionRequest(
        tool_name="run_command",
        input={"command": command},
        message="run command",
    )


def _resolver(rules: PermissionRules) -> PermissionResolver:
    return PermissionResolver(PermissionConfig(permission_rules=rules))


def _mock_parse(segments: list[str], valid: bool = True, reason: str = ""):
    async def parse(command: str) -> ParsedCommand:
        return ParsedCommand(shell="powershell", segments=segments, valid=valid, reason=reason)

    return patch("src.tools.permissions.parse_command", parse)


class RunCommandPermissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_allow_matches_single_command(self) -> None:
        resolver = _resolver(PermissionRules(allow=["run_command(Get-ChildItem)"]))
        with _mock_parse(["Get-ChildItem"]):
            response = await resolver.check(_request("Get-ChildItem"))

        self.assertEqual(response.decision, PermissionDecision.ALLOW)

    async def test_prefix_allow_matches_command_arguments(self) -> None:
        resolver = _resolver(PermissionRules(allow=["run_command(git status:*)"]))
        with _mock_parse(["git status --short"]):
            response = await resolver.check(_request("git status --short"))

        self.assertEqual(response.decision, PermissionDecision.ALLOW)

    async def test_prefix_does_not_match_partial_command_name_defaults_to_allow(self) -> None:
        calls: list[PermissionRequest] = []

        async def callback(request: PermissionRequest) -> PermissionResponse:
            calls.append(request)
            return PermissionResponse(decision=PermissionDecision.ASK)

        resolver = PermissionResolver(
            PermissionConfig(permission_rules=PermissionRules(allow=["run_command(git status:*)"])),
            callback=callback,
        )
        with _mock_parse(["git statusx --short"]):
            response = await resolver.check(_request("git statusx --short"))

        self.assertEqual(response.decision, PermissionDecision.ALLOW)
        self.assertEqual(calls, [])

    async def test_compound_command_all_segments_must_be_allowed(self) -> None:
        resolver = _resolver(PermissionRules(
            allow=[
                "run_command(Get-ChildItem)",
                "run_command(Write-Output:*)",
            ],
        ))
        with _mock_parse(["Get-ChildItem", "Write-Output ok"]):
            response = await resolver.check(_request("Get-ChildItem; Write-Output ok"))

        self.assertEqual(response.decision, PermissionDecision.ALLOW)

    async def test_compound_command_denies_if_any_segment_is_denied(self) -> None:
        resolver = _resolver(PermissionRules(
            allow=["run_command(Get-ChildItem)"],
            deny=["run_command(Remove-Item:*)"],
        ))
        with _mock_parse(["Get-ChildItem", "Remove-Item x"]):
            response = await resolver.check(_request("Get-ChildItem; Remove-Item x"))

        self.assertEqual(response.decision, PermissionDecision.DENY)
        self.assertIn("Remove-Item x", response.reason)

    async def test_compound_command_asks_for_ask_segments_only(self) -> None:
        calls: list[PermissionRequest] = []

        async def callback(request: PermissionRequest) -> PermissionResponse:
            calls.append(request)
            return PermissionResponse(decision=PermissionDecision.ALWAYS, rules=request.suggested_rules)

        resolver = PermissionResolver(
            PermissionConfig(permission_rules=PermissionRules(ask=["run_command(Write-Output:*)"])),
            callback=callback,
        )
        with _mock_parse(["Get-ChildItem", "Write-Output ok"]):
            response = await resolver.check(_request("Get-ChildItem; Write-Output ok"))

        self.assertEqual(response.decision, PermissionDecision.ALWAYS)
        self.assertEqual(calls[0].segments, ["Get-ChildItem", "Write-Output ok"])
        self.assertEqual(calls[0].suggested_rules, ["run_command(Write-Output ok:*)"])

    async def test_denied_segment_wins_over_allow_rules(self) -> None:
        resolver = _resolver(PermissionRules(
            allow=["run_command(*)"],
            deny=["run_command(Remove-Item:*)"],
        ))
        with _mock_parse(["Remove-Item x"]):
            response = await resolver.check(_request("Remove-Item x"))

        self.assertEqual(response.decision, PermissionDecision.DENY)

    async def test_windows_command_matching_is_case_insensitive(self) -> None:
        resolver = _resolver(PermissionRules(deny=["run_command(Remove-Item:*)"]))
        with _mock_parse(["remove-item x"]):
            response = await resolver.check(_request("remove-item x"))

        self.assertEqual(response.decision, PermissionDecision.DENY)

    async def test_unparseable_command_is_denied_with_parse_failure(self) -> None:
        calls: list[PermissionRequest] = []

        async def callback(request: PermissionRequest) -> PermissionResponse:
            calls.append(request)
            return PermissionResponse(decision=PermissionDecision.ASK)

        resolver = PermissionResolver(PermissionConfig(), callback=callback)
        with _mock_parse([], valid=False, reason="parser unavailable"):
            response = await resolver.check(_request("Get-ChildItem; Remove-Item x"))

        self.assertEqual(response.decision, PermissionDecision.DENY)
        self.assertIn("Command parse failed", response.reason)
        self.assertIn("parser unavailable", response.reason)
        self.assertEqual(calls, [])

    async def test_unparseable_command_does_not_request_ask_rule(self) -> None:
        calls: list[PermissionRequest] = []

        async def callback(request: PermissionRequest) -> PermissionResponse:
            calls.append(request)
            return PermissionResponse(decision=PermissionDecision.ASK)

        resolver = PermissionResolver(
            PermissionConfig(permission_rules=PermissionRules(ask=["run_command(Get-ChildItem; Remove-Item x)"])),
            callback=callback,
        )
        with _mock_parse([], valid=False, reason="parser unavailable"):
            response = await resolver.check(_request("Get-ChildItem; Remove-Item x"))

        self.assertEqual(response.decision, PermissionDecision.DENY)
        self.assertIn("Command parse failed", response.reason)
        self.assertEqual(calls, [])

    async def test_unparseable_command_fails_before_deny_rule_matching(self) -> None:
        resolver = _resolver(PermissionRules(deny=["run_command(Get-ChildItem; Remove-Item x)"]))
        with _mock_parse([], valid=False, reason="parser unavailable"):
            response = await resolver.check(_request("Get-ChildItem; Remove-Item x"))

        self.assertEqual(response.decision, PermissionDecision.DENY)
        self.assertIn("Command parse failed", response.reason)

    async def test_empty_command_is_denied(self) -> None:
        resolver = _resolver(PermissionRules())
        response = await resolver.check(_request("  "))

        self.assertEqual(response.decision, PermissionDecision.DENY)


@unittest.skipIf(powershell_executable() is None, "PowerShell parser is not available")
class PowerShellAstParserTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        asyncio.get_running_loop().set_debug(False)

    async def test_parses_semicolon_separated_commands(self) -> None:
        parsed = await parse_command("Get-ChildItem; Write-Output ok; Remove-Item x")

        self.assertTrue(parsed.valid, parsed.reason)
        self.assertEqual(parsed.shell, "powershell")
        self.assertEqual(parsed.segments, ["Get-ChildItem", "Write-Output ok", "Remove-Item x"])

    async def test_parses_newline_separated_long_command(self) -> None:
        command = "\n".join([
            "Write-Output one",
            "Write-Output two",
            "Write-Output three",
            "Write-Output four",
            "Write-Output five",
        ])

        parsed = await parse_command(command)

        self.assertTrue(parsed.valid, parsed.reason)
        self.assertEqual(parsed.segments, [
            "Write-Output one",
            "Write-Output two",
            "Write-Output three",
            "Write-Output four",
            "Write-Output five",
        ])

    async def test_parses_pipeline_segments(self) -> None:
        parsed = await parse_command("Get-ChildItem | Select-Object Name")

        self.assertTrue(parsed.valid, parsed.reason)
        self.assertEqual(parsed.segments, ["Get-ChildItem", "Select-Object Name"])

    async def test_parses_nested_commands_inside_control_flow(self) -> None:
        parsed = await parse_command("if ($true) { Get-ChildItem; Remove-Item x }")

        self.assertTrue(parsed.valid, parsed.reason)
        self.assertEqual(parsed.segments, ["Get-ChildItem", "Remove-Item x"])


if __name__ == "__main__":
    unittest.main()
