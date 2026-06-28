<p align="center">
  <img src="asset/learncode.png" alt="LearnCode" width="300">
</p>

LearnCode is a small Python coding-agent project with a web frontend.

The project is intended for learning how an agent loop works: the browser sends a message to the Python backend, the backend calls a model, executes tools when requested, saves the session, and sends intermediate steps back to the page through WebSocket.

![LearnCode web interface](asset/example.png)

![Agent loop and tool calls](asset/example-2.png)

## Start

- Python 3.10 or newer.
- A model endpoint and API key.

Install dependencies:

```powershell
cd LearnCode
pip install -r requirements.txt
```

Configure the model in `.env`.

Anthropic-style API:

```env
LEARN_CODE_MODEL=claude-3-5-sonnet-latest
LEARN_CODE_ANTHROPIC_BASE_URL=https://api.anthropic.com/v1/messages
LEARN_CODE_API_KEY=your-api-key
```

OpenAI-compatible API:

```env
LEARN_CODE_MODEL=deepseek-chat
LEARN_CODE_OPENAI_BASE_URL=https://api.deepseek.com/v1/chat/completions
LEARN_CODE_API_KEY=your-api-key
```

Optional variables:

```env
LEARN_CODE_MAX_OUTPUT_TOKENS=4096
LEARN_CODE_MAX_RETRIES=4
WORKSPACE=C:\path\to\your\workspace
HOST=127.0.0.1
PORT=8080
```

If `WORKSPACE` is not set, LearnCode uses the directory where the server is launched.

Run the server:

```powershell
python -m src.main
```

Open:

```text
http://127.0.0.1:8080
```

## Usage

Type a message in the web page and send it. LearnCode will create or reuse a session, call the configured model, show tool calls in the Agent Loop block, and save the conversation under `.sessions`.

The Compact button summarizes earlier context in the current session so later model requests can carry a shorter conversation. It is only an optional manual action; even if you do not click it, the backend can still compress context automatically according to its context-length rules.

## Implemented Features

- Web chat interface served by FastAPI.
- WebSocket communication for session events, agent steps, tool calls, and final replies.
- Anthropic-style and OpenAI-compatible model adapters.
- Built-in coding tools for reading files, listing files, searching files, editing files, writing files, and running commands.
- Agent loop with assistant progress messages, tool calls, tool results, and final assistant replies.
- Session system based on JSONL event logs, with list, load, save, delete, rename, fork, automatic title generation, compact boundaries, and expired-session cleanup.
- Context management with tool-result limiting, microcompact, snip compact, context collapse, auto compact, and Compact button summarization.
- Local skill discovery through `SKILL.md` files.
- Basic MCP server loading and MCP tool execution.
- Workspace detection from the launch directory or `WORKSPACE`.

## Acknowledgements

[LiuMengxuan04/MiniCode](https://github.com/LiuMengxuan04/MiniCode)   

[QUSETIONS/MiniCode-Python](https://github.com/QUSETIONS/MiniCode-Python).
