# Architecture

LearnCode has four main layers:

1. Web entrypoint: `src/main.py`
2. Agent runtime: `src/loop/runner.py`
3. Tool/model/session infrastructure: `src/tools`, `src/tools/registry.py`, `src/models/*.py`, `src/sessions/store.py`
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
- `src/loop/runner.py`: multi-step `model -> tools -> model` loop.
- `src/prompt.py`: system prompt construction.
- `src/tools/registry.py`: tool registry, metadata, execution.
- `src/tools/builtin.py`: built-in file, command, ask-user, and web tools.
- `src/sessions/store.py`: append-only session persistence.
- `src/config/runtime.py`: settings, env vars, MCP config, provider selection.
- `src/models/anthropic.py`: Anthropic-style API adapter.
- `src/models/openai.py`: OpenAI-style API adapter.
- `src/mcp/client.py`: stdio newline-json MCP tool loading.
- `src/skills/registry.py`: simple local skill registry.
- `src/context/compact/*`: context compression strategies.
- `static/index.html`: single-file web UI.

## Runtime State

- Sessions are stored in `LearnCode/.sessions`.
- Large tool outputs are stored under `~/.learncode/tool-results` by `context/tool_result_storage.py`.
- Global settings are read from `~/.learncode/settings.json`, `~/.learncode/mcp.json`, and environment variables.

## Current Limitations

- The web UI is static HTML without a build system.
- There is no CLI/TUI entrypoint equivalent to MiniCode's terminal UI.
- Environment variables use `LEARN_CODE_*`.
