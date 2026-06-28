# LearnCode Docs

LearnCode is a Python/FastAPI web coding assistant. Its core runtime is split into a web interface, an agent loop, model adapters, tools, sessions, context compaction, MCP, and skills.

## Reading Order

Start with the big picture, then follow the request flow, and finally dive into tools, sessions, context, MCP, and skills.

1. [README.md](README.md)  
   Start with the docs overview and the responsibility of each module.

2. [architecture.md](architecture.md)  
   Build the mental model: web entrypoint, agent runtime, tool/model/session infrastructure, and browser UI.

3. [api.md](api.md)  
   Understand how the browser talks to the backend through HTTP endpoints, WebSocket actions, and server events.

4. [frontend.md](frontend.md)  
   Read this together with `static/index.html` to see how the frontend sends messages, renders messages, and manages sessions.

5. [agent-loop.md](agent-loop.md)  
   Understand the core execution path: how user input reaches the model, how the model triggers tools, and how tool results return to context.

6. [tools.md](tools.md)  
   Learn the current built-in tool boundary: file read/write, search, command execution, ask_user, web_fetch, and web_search.

7. [sessions.md](sessions.md)  
   Understand how sessions are saved, resumed, renamed, forked, and how compact boundaries affect active context.

8. [context.md](context.md)  
   Learn long-context handling: token estimation, large tool result persistence, microcompact, manual compact, auto compact, snip compact, and context collapse.

9. [models-config.md](models-config.md)  
   See how models and config are loaded: Anthropic/OpenAI adapters, environment variables, and config merge rules.

10. [mcp.md](mcp.md)  
    Understand the current MCP integration and what is still missing compared with the fuller MiniCode implementation.

11. [skills.md](skills.md)  
    Understand the current skill data model, parsing behavior, and limitations.

## Read by Goal

If you want to change the web interaction, start with:

- [api.md](api.md)
- [frontend.md](frontend.md)
- [sessions.md](sessions.md)

If you want to change agent behavior, start with:

- [agent-loop.md](agent-loop.md)
- [tools.md](tools.md)
- [models-config.md](models-config.md)

If you want to close gaps with MiniCode, start with:

- [sessions.md](sessions.md)
- [context.md](context.md)
- [mcp.md](mcp.md)
- [skills.md](skills.md)

If you want to debug why the model called a tool, start with:

- [agent-loop.md](agent-loop.md)
- [tools.md](tools.md)
- [models-config.md](models-config.md)

## Code-to-Docs Map

- `src/main.py` -> [api.md](api.md)
- `static/index.html` -> [frontend.md](frontend.md)
- `src/loop/runner.py` -> [agent-loop.md](agent-loop.md)
- `src/tools/builtin.py` -> [tools.md](tools.md)
- `src/sessions/store.py` -> [sessions.md](sessions.md)
- `src/context/compact/*` and `src/context/*` -> [context.md](context.md)
- `src/config/runtime.py`, `src/models/anthropic.py`, `src/models/openai.py` -> [models-config.md](models-config.md)
- `src/mcp/client.py` -> [mcp.md](mcp.md)
- `src/skills/registry.py` -> [skills.md](skills.md)

## Current Implementation Boundaries

These docs describe the code currently in `LearnCode/src` and `LearnCode/static`. Some modules intentionally mirror MiniCode naming, but not every MiniCode feature is fully implemented yet. Known gaps are called out in each module page.
