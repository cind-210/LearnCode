# Agent Loop

The core loop is implemented in `src/agent_loop.py`.

## Main Entry

```python
run_agent_loop(user_input, model_adapter, config, state, on_step, on_permission_request)
```

Inputs:

- `user_input`: current user text.
- `model_adapter`: Anthropic, OpenAI, or mock adapter implementing `next(messages)`.
- `config`: `AgentLoopConfig`.
- `state`: optional `AgentLoopState`, usually loaded from session messages.
- `on_step`: callback used by `main.py` to stream step data to the browser.

## Turn Flow

1. If this is a new state, build a system message with `build_system_prompt()`.
2. Append `ChatMessage.user(user_input)`.
3. Build built-in tools with `build_builtin_registry()`.
4. Load runtime config with `load_runtime_config()`.
5. If MCP servers are configured, merge MCP tools into the registry.
6. Create permission config and tool context.
7. Call `model_adapter.next(model_view)`.
8. If the model returns an assistant answer, append it and stop.
9. If the model returns tool calls, execute each tool, append tool call/result messages, run compression checks, then continue.
10. Stop if no valid response is produced or `max_turns` is exceeded.

## AgentLoopState

Fields:

- `messages`: active context messages.
- `turn`: current turn counter.
- `should_stop`
- `stop_reason`
- `collapse_state`: context-collapse projection state.
- `auto_compact_failures`
- `_runtime_config`

## Tool Call Handling

`_handle_tool_calls()`:

- Builds a `PermissionRequest`.
- Checks the `PermissionResolver`.
- Appends `assistant_tool_call`.
- Executes the tool through `ToolRegistry.execute()`.
- Replaces large output with persisted previews via `replace_large_tool_result()`.
- Applies batch result budget with `apply_tool_result_budget()`.
- Appends `tool_result`.

## Compression Hook

After tool execution, `_apply_compression()` may run:

- `microcompact`
- `auto_compact`
- `snip_compact_conversation`
- `apply_context_collapse_if_needed`

## Current Limitations

- `memory = get_default_memory_store()` is initialized but not actively used in this loop.
- Auto compact boundaries are applied in-memory but only manual compact currently appends an explicit session compact boundary from `main.py`.
- Permission handling exists but the web UI does not yet present a full approval prompt flow.
