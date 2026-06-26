# 上下文管理

上下文管理分布在 `src/compact` 和 `src/utils`。

## Token 统计

`src/utils/token_estimator.py` 负责估算 token 和计算上下文状态。

重要函数：

- `estimate_message_tokens`
- `estimate_messages_tokens`
- `token_count_with_estimation`
- `mark_provider_usage_stale`
- `compute_context_stats`

当模型 adapter 返回 usage 时，provider usage 会存到 assistant/progress/tool-call 消息上。

## 模型上下文窗口

`src/utils/model_context.py` 把模型名映射到上下文长度限制。

`get_model_context_window(model)` 返回压缩逻辑使用的上下文窗口配置。

## 大型工具结果

`src/utils/tool_result_storage.py` 会把大型工具输出持久化到：

```text
~/.mini-code/tool-results
```

核心行为：

- 超过 `50_000` 字符的输出会落盘
- 可见上下文中只保留短预览和文件路径
- 批量结果会被压缩到可见预算附近

## Microcompact

`src/compact/microcompact.py` 会清理较旧的、可压缩工具结果，同时保留最近的结果。

可压缩工具定义在 `src/utils/context.py`。

## 手动 Compact

`src/compact/manual_compact.py` 调用 `compact_conversation()`。

在 Web 运行时中，成功的手动 compact 会通过 `append_compact_boundary()` 持久化。

## LLM Compact

`src/compact/compact.py` 会：

1. 选择保留边界。
2. 把较旧消息转换成文本。
3. 向模型发送摘要 prompt。
4. 构造 `context_summary` 消息。
5. 保留 system messages、summary 和最近消息。

## Auto Compact

`src/compact/auto_compact.py` 根据上下文压力决定是否 compact。

当前状态是进程内状态。

## Snip Compact

`src/compact/snip_compact.py` 会移除安全的中间历史区间，同时保护：

- 文件修改工具
- 重要错误
- 未闭合的 tool-call group
- 最近上下文

它会插入一个 `snip_boundary` 消息。

## Context Collapse

`src/compact/context_collapse.py` 为长会话创建 projection-layer collapsed view。Agent loop 在模型请求前调用 `project_collapsed_view()`。

## 当前限制

- 手动 compact 是唯一会显式持久化 session compact boundary 的压缩路径。
- 大工具结果存储仍使用 `.mini-code` 命名。
- Context collapse 状态存在于当前 loop 的内存中。
