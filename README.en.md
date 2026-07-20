<p align="center">
  <img src="asset/learncode.png" alt="LearnCode" width="300">
</p>

LearnCode is a self-evolving agentic programming assistant system, and also a Python project for learning how agents work.

The word "Learn" has two meanings here: agents with the same role share configuration, permissions, visible skills, and experience, and keep updating that experience during real conversations so the role can evolve through use; the project also keeps the agent loop, tool execution, context compaction, session persistence, and multi-agent orchestration paths visible for study.

The browser sends user messages to the Python backend, the backend calls a model, executes tools when requested, saves the session, and streams intermediate steps, tool results, and replies back to the page through WebSocket.

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

Type a message in the web page and send it. LearnCode will create or reuse a session, call the configured model, show thinking, tool calls, and tool results in the Agent Loop block, and save the conversation under `.sessions`.

Agents with the same role share role configuration and experience. A parent session can create, fork, inspect, and send messages to subsessions, allowing tasks to be split across multiple agents. The sidebar shows the session tree, and streaming updates are routed to the currently opened session.

The Compact button summarizes earlier context in the current session so later model requests can carry a shorter conversation. It is only an optional manual action; even if you do not click it, the backend can still compress context automatically according to its context-length rules.

## Changelog

<details>
<summary>Show details</summary>

### 2026-07-20

- Reworked session streaming so only the currently opened session/subsession receives deltas, and a running session continues streaming after it is loaded.
- Agent Loop blocks now end with explicit `loop_end` markers instead of relying on a separate `assistant_final` role.
- Stop and normal completion both persist loop end markers, allowing the frontend to restore Agent Loop blocks consistently.
- Fixed persistence and frontend loading for messages sent from a parent session to a child session through `SendMessage`.
- Improved Agent Loop auto-scroll so only the loop block follows new content, without forcing the whole page to the bottom.

### 2026-07-19

- Added multi-agent, character, and role-permission systems: roles can share configuration, visible skills, permissions, and experience.
- Added support for creating, forking, naming, viewing, and messaging subsessions.
- Added regex and workspace-level permission rules, and improved run-command permissions, sandbox permissions, and parallel tool execution.
- Agent Loop now supports attached tool results, collapsed result display, and duplicate streamed-step prevention.
- Unified automatic naming from the first user message, including manually created sessions.

### 2026-07-18

- Added sandbox permission configuration and parallel tool execution.
- Improved `run_command` permission handling.

### 2026-07-13

- Thinking blocks are saved as context and displayed in the frontend Agent Loop.
- Stop now terminates the current turn and keeps tool calls structurally closed with matching tool results.
- Anthropic model requests now use streaming to make network stalls easier to detect than with non-streaming requests.

</details>

## Implemented Features

- Web chat interface served by FastAPI.
- WebSocket communication for session events, agent steps, deltas, tool calls, tool results, and replies.
- Anthropic-style and OpenAI-compatible model adapters.
- Built-in coding tools for reading files, listing files, searching files, editing files, writing files, and running commands.
- Agent loop with thinking/progress messages, tool calls, tool results, streamed replies, and explicit loop end markers.
- Character system where agents with the same role share role configuration, permissions, visible skills, and experience, and can update experience during use.
- Multi-agent/subsession support: create subsessions, fork subsessions, browse the parent-child session tree, inspect child sessions, and send messages to child sessions.
- Session system based on JSONL event logs, with list, load, save, delete, rename, fork, first-message title generation, compact boundaries, and expired-session cleanup.
- Context management with tool-result limiting, microcompact, snip compact, context collapse, auto compact, and Compact button summarization.
- Permission system with allow/ask/deny, workspace rules, regex rules, session/character-level permission configuration, and sandboxed command control.
- Todo management and parallel tool execution.
- Local skill discovery through `SKILL.md` files.
- Basic MCP server loading and MCP tool execution.
- Workspace detection from the launch directory or `WORKSPACE`.

## Acknowledgements

[LiuMengxuan04/MiniCode](https://github.com/LiuMengxuan04/MiniCode)   

[QUSETIONS/MiniCode-Python](https://github.com/QUSETIONS/MiniCode-Python).
