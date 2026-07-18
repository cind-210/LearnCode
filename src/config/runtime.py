"""
Configuration management for MiniCode.

Mirrors src/config.ts from the TypeScript version.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Union

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

LEARN_CODE_DIR = Path(
    os.environ.get("LEARN_CODE_HOME")
    or os.path.join(os.path.expanduser("~"), ".learncode")
).resolve()

LEARN_CODE_SETTINGS_PATH = LEARN_CODE_DIR / "settings.json"
LEARN_CODE_HISTORY_PATH = LEARN_CODE_DIR / "history.jsonl"
LEARN_CODE_PERMISSIONS_PATH = LEARN_CODE_DIR / "permissions.json"
LEARN_CODE_MCP_PATH = LEARN_CODE_DIR / "mcp.json"
LEARN_CODE_MCP_TOKENS_PATH = LEARN_CODE_DIR / "mcp-tokens.json"
LEARN_CODE_PROJECTS_DIR = LEARN_CODE_DIR / "projects"
CLAUDE_SETTINGS_PATH = Path(os.path.expanduser("~"), ".claude", "settings.json")
PROJECT_MCP_PATH = Path.cwd() / ".mcp.json"

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


@dataclass
class McpServerConfig:
    command: str = ""
    args: Optional[list[str]] = None
    env: Optional[dict[str, Union[str, int]]] = None
    url: Optional[str] = None
    headers: Optional[dict[str, Union[str, int]]] = None
    cwd: Optional[str] = None
    enabled: bool = True
    protocol: Optional[str] = None  # 'auto' | 'content-length' | 'newline-json' | 'streamable-http'


@dataclass
class MiniCodeSettings:
    env: Optional[dict[str, Union[str, int]]] = None
    model: Optional[str] = None
    max_output_tokens: Optional[int] = None
    mcp_servers: dict[str, McpServerConfig] = field(default_factory=dict)


@dataclass
class RuntimeConfig:
    model: str
    base_url: str
    provider: str = "anthropic"  # "anthropic" or "openai"
    auth_token: Optional[str] = None
    api_key: Optional[str] = None
    max_output_tokens: Optional[int] = None
    mcp_servers: dict[str, McpServerConfig] = field(default_factory=dict)
    source_summary: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_enoent(error: Exception) -> bool:
    return isinstance(error, (FileNotFoundError, OSError))


def _read_json_file(file_path: Path) -> dict[str, Any]:
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        if _is_enoent(e):
            return {}
        raise


def _write_json_file(file_path: Path, data: dict[str, Any]) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


# ---------------------------------------------------------------------------
# MCP tokens
# ---------------------------------------------------------------------------


async def read_mcp_tokens_file(file_path: Optional[Path] = None) -> dict[str, str]:
    path = file_path or LEARN_CODE_MCP_TOKENS_PATH
    try:
        data = _read_json_file(path)
        return {k: str(v) for k, v in data.items()}
    except Exception:
        return {}


async def save_mcp_tokens_file(tokens: dict[str, str], file_path: Optional[Path] = None) -> None:
    path = file_path or LEARN_CODE_MCP_TOKENS_PATH
    _write_json_file(path, tokens)


# ---------------------------------------------------------------------------
# Settings reading
# ---------------------------------------------------------------------------


async def read_settings_file(file_path: Path) -> MiniCodeSettings:
    try:
        data = _read_json_file(file_path)
        mcp = data.get("mcpServers", {})
        servers = {
            name: McpServerConfig(
                command=cfg.get("command", ""),
                args=cfg.get("args"),
                env=cfg.get("env"),
                url=cfg.get("url"),
                headers=cfg.get("headers"),
                cwd=cfg.get("cwd"),
                enabled=cfg.get("enabled", True),
                protocol=cfg.get("protocol") or cfg.get("type"),
            )
            for name, cfg in mcp.items()
        }
        return MiniCodeSettings(
            env=data.get("env"),
            model=data.get("model"),
            max_output_tokens=data.get("maxOutputTokens"),
            mcp_servers=servers,
        )
    except Exception as e:
        if _is_enoent(e):
            return MiniCodeSettings()
        raise


async def read_mcp_config_file(file_path: Path) -> dict[str, McpServerConfig]:
    try:
        data = _read_json_file(file_path)
        mcp = data.get("mcpServers", {})
        return {
            name: McpServerConfig(
                command=cfg.get("command", ""),
                args=cfg.get("args"),
                env=cfg.get("env"),
                url=cfg.get("url"),
                headers=cfg.get("headers"),
                cwd=cfg.get("cwd"),
                enabled=cfg.get("enabled", True),
                protocol=cfg.get("protocol") or cfg.get("type"),
            )
            for name, cfg in mcp.items()
        }
    except Exception as e:
        if _is_enoent(e):
            return {}
        raise


def get_mcp_config_path(scope: str, cwd: Optional[Path] = None) -> Path:
    if scope == "project":
        return (cwd or Path.cwd()) / ".mcp.json"
    return LEARN_CODE_MCP_PATH


async def load_scoped_mcp_servers(scope: str, cwd: Optional[Path] = None) -> dict[str, McpServerConfig]:
    return await read_mcp_config_file(get_mcp_config_path(scope, cwd))


async def save_scoped_mcp_servers(scope: str, servers: dict[str, McpServerConfig], cwd: Optional[Path] = None) -> None:
    target = get_mcp_config_path(scope, cwd)
    target.parent.mkdir(parents=True, exist_ok=True)
    _write_json_file(target, {"mcpServers": {name: cfg.__dict__ for name, cfg in servers.items()}})


# ---------------------------------------------------------------------------
# Settings merging
# ---------------------------------------------------------------------------


def merge_settings(base: MiniCodeSettings, override: MiniCodeSettings) -> MiniCodeSettings:
    merged_mcp: dict[str, McpServerConfig] = {**base.mcp_servers}
    for name, server in override.mcp_servers.items():
        existing = merged_mcp.get(name, McpServerConfig())
        merged_mcp[name] = McpServerConfig(
            command=server.command or existing.command,
            args=server.args if server.args is not None else existing.args,
            env={**(existing.env or {}), **(server.env or {})} if server.env else existing.env,
            url=server.url or existing.url,
            headers={**(existing.headers or {}), **(server.headers or {})} if server.headers else existing.headers,
            cwd=server.cwd or existing.cwd,
            enabled=server.enabled,
            protocol=server.protocol or existing.protocol,
        )

    return MiniCodeSettings(
        env={**(base.env or {}), **(override.env or {})},
        model=override.model or base.model,
        max_output_tokens=override.max_output_tokens if override.max_output_tokens is not None else base.max_output_tokens,
        mcp_servers=merged_mcp,
    )


async def load_effective_settings() -> MiniCodeSettings:
    claude = await read_settings_file(CLAUDE_SETTINGS_PATH)
    global_mcp = await read_mcp_config_file(LEARN_CODE_MCP_PATH)
    project_mcp = await read_mcp_config_file(PROJECT_MCP_PATH)
    mini_code = await read_settings_file(LEARN_CODE_SETTINGS_PATH)

    s1 = merge_settings(claude, MiniCodeSettings(mcp_servers=global_mcp))
    s2 = merge_settings(s1, MiniCodeSettings(mcp_servers=project_mcp))
    return merge_settings(s2, mini_code)


async def save_mini_code_settings(updates: MiniCodeSettings) -> None:
    LEARN_CODE_DIR.mkdir(parents=True, exist_ok=True)
    existing = await read_settings_file(LEARN_CODE_SETTINGS_PATH)
    merged = merge_settings(existing, updates)
    _write_json_file(LEARN_CODE_SETTINGS_PATH, {
        "env": merged.env,
        "model": merged.model,
        "maxOutputTokens": merged.max_output_tokens,
        "mcpServers": {
            name: {
                "command": cfg.command,
                "args": cfg.args,
                "env": cfg.env,
                "url": cfg.url,
                "headers": cfg.headers,
                "cwd": cfg.cwd,
                "enabled": cfg.enabled,
                "protocol": cfg.protocol,
            }
            for name, cfg in merged.mcp_servers.items()
        },
    })


# ---------------------------------------------------------------------------
# Runtime config
# ---------------------------------------------------------------------------


async def load_runtime_config() -> RuntimeConfig:
    settings = await load_effective_settings()
    env = {**(settings.env or {}), **os.environ}

    model = (
        os.environ.get("LEARN_CODE_MODEL")
        or settings.model
        or str(env.get("ANTHROPIC_MODEL", "")).strip()
    )

    base_url = (
        os.environ.get("LEARN_CODE_ANTHROPIC_BASE_URL")
        or os.environ.get("LEARN_CODE_OPENAI_BASE_URL")
        or str(env.get("ANTHROPIC_BASE_URL", "")).strip()
    ) or "https://api.anthropic.com/v1/messages"

    provider = "anthropic"
    if (os.environ.get("LEARN_CODE_OPENAI_BASE_URL", "") and
            not os.environ.get("LEARN_CODE_ANTHROPIC_BASE_URL", "")):
        provider = "openai"

    auth_token = (
        os.environ.get("LEARN_CODE_AUTH_TOKEN")
        or str(env.get("ANTHROPIC_AUTH_TOKEN", "")).strip()
    ) or None

    api_key = (
        os.environ.get("LEARN_CODE_API_KEY")
        or str(env.get("ANTHROPIC_API_KEY", "")).strip()
    ) or None

    raw_max = (
        os.environ.get("LEARN_CODE_MAX_OUTPUT_TOKENS")
        or settings.max_output_tokens
        or env.get("LEARN_CODE_MAX_OUTPUT_TOKENS")
    )
    max_output_tokens = None
    if raw_max is not None:
        try:
            val = int(raw_max)
            if val > 0:
                max_output_tokens = val
        except (ValueError, TypeError):
            pass

    if not model:
        raise RuntimeError("No model configured. Set ~/.learncode/settings.json or LEARN_CODE_MODEL env.")

    if not auth_token and not api_key:
        raise RuntimeError("No auth configured. Set ANTHROPIC_AUTH_TOKEN or ANTHROPIC_API_KEY.")

    return RuntimeConfig(
        model=model,
        base_url=base_url,
        provider=provider,
        auth_token=auth_token,
        api_key=api_key,
        max_output_tokens=max_output_tokens,
        mcp_servers=settings.mcp_servers,
        source_summary=f"config: {LEARN_CODE_SETTINGS_PATH} > {CLAUDE_SETTINGS_PATH} > process.env",
    )
