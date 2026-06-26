# Architecture

LearnCode has four main layers:

1. Web entrypoint: `src/main.py`
2. Agent runtime: `src/agent_loop.py`
3. Tool/model/session infrastructure: `src/tools`, `src/tool.py`, `src/*_adapter.py`, `src/session.py`
4. Browser UI: `static/index.html`

## Request Flow

1. Browser connects to `/ws`.
2. User sends a `chat` action with text and optional `session_id`.
3. `main.py` loads or creates a session from `.sessions`.
4. `_get_model_adapter()` builds a model adapter using runtime config.
5. `run_agent_loop()` appends the user message, calls the model, executes tools if requested, applies compression if needed, and returns updated messages.
6. `save_session()` appends new message events to the JSONL event log.
7. The WebSocket sends `step`, `done`, `error`, or session events back to the browser.

## Important Files

- `src/main.py`: FastAPI app, REST endpoints, WebSocket action dispatch.
- `src/agent_loop.py`: multi-step `model -> tools -> model` loop.
- `src/prompt.py`: system prompt construction.
- `src/tool.py`: tool registry, metadata, execution.
- `src/tools/base.py`: built-in file, command, ask-user, and web tools.
- `src/session.py`: append-only session persistence.
- `src/config.py`: settings, env vars, MCP config, provider selection.
- `src/anthropic_adapter.py`: Anthropic-style API adapter.
- `src/openai_adapter.py`: OpenAI-style API adapter.
- `src/mcp.py`: stdio newline-json MCP tool loading.
- `src/skills.py`: simple local skill registry.
- `src/compact/*`: context compression strategies.
- `static/index.html`: single-file web UI.

## Runtime State

- Sessions are stored in `LearnCode/.sessions`.
- Large tool outputs are stored under `~/.mini-code/tool-results` by `utils/tool_result_storage.py`.
- Global settings are read from `~/.mini-code/settings.json`, `~/.mini-code/mcp.json`, and environment variables.

## Current Limitations

- The web UI is static HTML without a build system.
- There is no CLI/TUI entrypoint equivalent to MiniCode's terminal UI.
- Several modules still use MiniCode-compatible path/env names such as `MINI_CODE_HOME`.
