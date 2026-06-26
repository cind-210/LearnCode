# Models and Config

Runtime config is implemented in `src/config.py`.

## Config Sources

`load_effective_settings()` merges:

1. `~/.claude/settings.json`
2. `~/.mini-code/mcp.json`
3. project `.mcp.json`
4. `~/.mini-code/settings.json`
5. process environment variables

The final runtime config is produced by `load_runtime_config()`.

## Important Environment Variables

- `MINI_CODE_MODEL`
- `ANTHROPIC_MODEL`
- `MINI_CODE_ANTHROPIC_BASE_URL`
- `MINI_CODE_OPENAI_BASE_URL`
- `ANTHROPIC_BASE_URL`
- `MINI_CODE_AUTH_TOKEN`
- `ANTHROPIC_AUTH_TOKEN`
- `MINI_CODE_API_KEY`
- `ANTHROPIC_API_KEY`
- `MINI_CODE_MAX_OUTPUT_TOKENS`

## Provider Selection

Default provider is `anthropic`.

Provider switches to `openai` when `MINI_CODE_OPENAI_BASE_URL` is set and `MINI_CODE_ANTHROPIC_BASE_URL` is not set.

## Anthropic Adapter

`src/anthropic_adapter.py`:

- converts internal `ChatMessage` objects to Anthropic message blocks
- sends tools as Anthropic tool definitions
- parses text, thinking blocks, tool use, and usage
- supports retries based on retry env/config

## OpenAI Adapter

`src/openai_adapter.py`:

- converts internal messages to OpenAI-compatible chat messages
- sends built-in tool schemas
- parses assistant content and tool calls
- records usage when available

## Mock Adapter

`src/mock_model.py` is an offline/testing model.

Supported mock commands:

- `/tools`
- `/ls`
- `/grep`
- `/read`
- `/cmd`
- `/write`
- `/edit`

Normal free text returns a static help message.

## Current Limitations

- Config names still use `MINI_CODE_*`.
- No interactive installer is implemented in this Python web app.
- OpenAI/Anthropic compatibility depends on the configured gateway matching expected response shapes.
