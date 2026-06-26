# Agent Loop

核心循环实现在 `src/agent_loop.py`。

## 主入口

```python
run_agent_loop(user_input, model_adapter, config, state, on_step, on_permission_request)
```

输入：

- `user_input`：当前用户文本。
- `model_adapter`：Anthropic、OpenAI 或 mock adapter，实现 `next(messages)`。
- `config`：`AgentLoopConfig`。
- `state`：可选 `AgentLoopState`，通常由会话消息加载。
- `on_step`：`main.py` 用来向浏览器发送 step 数据的回调。

## Turn 流程

1. 如果是新状态，用 `build_system_prompt()` 构造 system message。
2. 追加 `ChatMessage.user(user_input)`。
3. 用 `build_builtin_registry()` 构造内置工具。
4. 用 `load_runtime_config()` 加载运行时配置。
5. 如果配置了 MCP server，把 MCP 工具合并进工具注册表。
6. 创建权限配置和工具上下文。
7. 调用 `model_adapter.next(model_view)`。
8. 如果模型返回 assistant answer，追加消息并停止。
9. 如果模型返回 tool calls，执行每个工具，追加 tool call/result messages，执行压缩检查，然后继续。
10. 如果没有有效响应或超过 `max_turns`，停止。

## AgentLoopState

字段：

- `messages`：当前 active context messages。
- `turn`：当前 turn 计数。
- `should_stop`
- `stop_reason`
- `collapse_state`：context-collapse projection 状态。
- `auto_compact_failures`
- `_runtime_config`

## 工具调用处理

`_handle_tool_calls()` 会：

- 构造 `PermissionRequest`。
- 检查 `PermissionResolver`。
- 追加 `assistant_tool_call`。
- 通过 `ToolRegistry.execute()` 执行工具。
- 用 `replace_large_tool_result()` 把大型输出替换为落盘预览。
- 用 `apply_tool_result_budget()` 应用批量结果预算。
- 追加 `tool_result`。

## 压缩钩子

工具执行后，`_apply_compression()` 可能运行：

- `microcompact`
- `auto_compact`
- `snip_compact_conversation`
- `apply_context_collapse_if_needed`

## 当前限制

- `memory = get_default_memory_store()` 已初始化，但在当前循环中没有实际使用。
- auto compact boundary 只在内存中应用；目前只有手动 compact 会从 `main.py` 追加显式 session compact boundary。
- 权限处理存在，但 Web UI 还没有完整审批弹窗流程。
