"""
Shell command parsing for run_command permissions.

The permission layer uses AST-based parsing when available so deny/ask rules
can match individual shell segments. Commands that do not match deny/ask rules
are allowed by default.
"""
from __future__ import annotations

import asyncio
import base64
import importlib.util
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any, Optional


RUN_COMMAND_TOOL = "run_command"


@dataclass
class ParsedCommand:
    shell: str
    segments: list[str] = field(default_factory=list)
    valid: bool = True
    reason: str = ""


def active_shell_name() -> str:
    if os.name == "nt":
        return "powershell"
    return "bash"


def powershell_executable() -> Optional[str]:
    return shutil.which("pwsh") or shutil.which("powershell.exe") or shutil.which("powershell")


def powershell_args(command: str) -> list[str]:
    exe = powershell_executable()
    if not exe:
        return []
    return [exe, "-NoProfile", "-NonInteractive", "-Command", command]


async def parse_command(command: str) -> ParsedCommand:
    if active_shell_name() == "powershell":
        return await _parse_powershell_command(command)
    return _parse_bash_command(command)


def command_rule(command: str) -> str:
    return f"{RUN_COMMAND_TOOL}({command.strip()})"


def command_prefix_rule(command: str) -> str:
    prefix = _simple_prefix(command)
    return f"{RUN_COMMAND_TOOL}({prefix}:*)" if prefix else command_rule(command)


def run_command_rule_matches_segment(rule: str, segment: str) -> bool:
    regex_prefix = f"regex:{RUN_COMMAND_TOOL}:"
    if rule.startswith(regex_prefix):
        pattern = rule[len(regex_prefix):]
        flags = re.IGNORECASE if active_shell_name() == "powershell" else 0
        return re.search(pattern, segment.strip(), flags=flags) is not None

    inner = _run_command_rule_inner(rule)
    if inner is None:
        return False
    segment = segment.strip()
    if inner == "*":
        return True
    if active_shell_name() == "powershell":
        inner = inner.lower()
        segment = segment.lower()
    if inner.endswith(":*"):
        prefix = inner[:-2].strip()
        return segment == prefix or segment.startswith(prefix + " ")
    return segment == inner.strip()


def is_run_command_rule(rule: str) -> bool:
    return _run_command_rule_inner(rule) is not None


def _run_command_rule_inner(rule: str) -> Optional[str]:
    if not rule.startswith(f"{RUN_COMMAND_TOOL}(") or not rule.endswith(")"):
        return None
    return rule[len(RUN_COMMAND_TOOL) + 1:-1]


def _simple_prefix(command: str) -> str:
    parts = command.strip().split()
    if not parts:
        return ""
    if len(parts) >= 2 and parts[1].replace("-", "").isalnum() and parts[1][0].isalpha():
        return " ".join(parts[:2])
    return parts[0]


def _parse_bash_command(command: str) -> ParsedCommand:
    if importlib.util.find_spec("bashlex") is None:
        return ParsedCommand(shell="bash", valid=False, reason="bashlex is not installed")

    import bashlex

    trees = bashlex.parser.parse(command)
    segments: list[str] = []
    for tree in trees:
        _collect_bash_commands(tree, command, segments)

    segments = _clean_segments(segments)
    return ParsedCommand(shell="bash", segments=segments)


def _collect_bash_commands(node: Any, source: str, segments: list[str]) -> None:
    if getattr(node, "kind", "") == "command":
        segments.append(source[node.pos[0]:node.pos[1]])
        return
    for part in getattr(node, "parts", []) or []:
        _collect_bash_commands(part, source, segments)
    for list_node in getattr(node, "list", []) or []:
        _collect_bash_commands(list_node, source, segments)
    command = getattr(node, "command", None)
    if command is not None:
        _collect_bash_commands(command, source, segments)


async def _parse_powershell_command(command: str) -> ParsedCommand:
    exe = powershell_executable()
    if not exe:
        return ParsedCommand(shell="powershell", valid=False, reason="PowerShell is not available")

    script = _build_powershell_parse_script(command)
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    proc = await asyncio.create_subprocess_exec(
        exe,
        "-NoProfile",
        "-NonInteractive",
        "-EncodedCommand",
        encoded,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5)
    if proc.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace").strip()
        return ParsedCommand(shell="powershell", valid=False, reason=detail or "PowerShell parser failed")

    payload = json.loads(stdout.decode("utf-8-sig", errors="replace"))
    if not payload.get("valid", False):
        return ParsedCommand(
            shell="powershell",
            valid=False,
            reason="; ".join(payload.get("errors", [])) or "PowerShell parse failed",
        )

    segments = _clean_segments(payload.get("segments", []))
    return ParsedCommand(shell="powershell", segments=segments)


def _build_powershell_parse_script(command: str) -> str:
    command_json = json.dumps(command)
    return f"""
$code = ConvertFrom-Json @'
{command_json}
'@
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseInput($code, [ref]$tokens, [ref]$errors)
if ($errors.Count -gt 0) {{
  [pscustomobject]@{{
    valid = $false
    errors = @($errors | ForEach-Object {{ $_.Message }})
    segments = @()
  }} | ConvertTo-Json -Compress -Depth 5
  exit 0
}}
$commands = @($ast.FindAll({{ param($node) $node -is [System.Management.Automation.Language.CommandAst] }}, $true))
[pscustomobject]@{{
  valid = $true
  errors = @()
  segments = @($commands | ForEach-Object {{ $_.Extent.Text }})
}} | ConvertTo-Json -Compress -Depth 5
"""


def _clean_segments(raw_segments: Any) -> list[str]:
    if not isinstance(raw_segments, list):
        return []
    seen: set[str] = set()
    segments: list[str] = []
    for raw in raw_segments:
        if not isinstance(raw, str):
            continue
        segment = raw.strip()
        if not segment or segment in seen:
            continue
        seen.add(segment)
        segments.append(segment)
    return segments
