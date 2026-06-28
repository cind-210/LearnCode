# Models and Config

Runtime config is implemented in `src/config/runtime.py`.

## Config Sources

`load_effective_settings()` merges:

1. `~/.claude/settings.json`
2. `~/.learncode/mcp.json`
3. project `.mcp.json`
4. `~/.learncode/settings.json`
5. process environment variables

The final runtime config is produced by `load_runtime_config()`.

## Important Environment Variables

- `LEARN_CODE_HOME`
- `LEARN_CODE_MODEL`
- `ANTHROPIC_MODEL`
- `LEARN_CODE_ANTHROPIC_BASE_URL`
- `LEARN_CODE_OPENAI_BASE_URL`
- `ANTHROPIC_BASE_URL`
- `LEARN_CODE_AUTH_TOKEN`
- `ANTHROPIC_AUTH_TOKEN`
- `LEARN_CODE_API_KEY`
- `ANTHROPIC_API_KEY`
- `LEARN_CODE_MAX_OUTPUT_TOKENS`
- `LEARN_CODE_MAX_RETRIES`
- `LEARN_CODE_DEBUG_AUTOCOMPACT`

## Provider Selection

Default provider is `anthropic`.

Provider switches to `openai` when `LEARN_CODE_OPENAI_BASE_URL` is set and `LEARN_CODE_ANTHROPIC_BASE_URL` is not set.

## Anthropic Adapter

`src/models/anthropic.py`:

- converts internal `ChatMessage` objects to Anthropic message blocks
- sends tools as Anthropic tool definitions
- parses text, thinking blocks, tool use, and usage
- supports retries based on retry env/config

## OpenAI Adapter

`src/models/openai.py`:

- converts internal messages to OpenAI-compatible chat messages
- sends built-in tool schemas
- parses assistant content and tool calls
- records usage when available

## Mock Adapter

`src/models/mock.py` is an offline/testing model.

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

- Environment variables use `LEARN_CODE_*`.
- No interactive installer is implemented in this Python web app.
- OpenAI/Anthropic compatibility depends on the configured gateway matching expected response shapes.
