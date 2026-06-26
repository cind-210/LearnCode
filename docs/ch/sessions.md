# 会话系统

会话实现在 `src/session.py`，存储在 `LearnCode/.sessions`。

## 存储格式

每个会话是一个 JSONL 文件：

```text
.sessions/<session_id>.jsonl
```

每一行是一个 event object。消息事件包含：

- `type`
- `message`
- `uuid`
- `timestamp`
- `session_id`
- `cwd`
- `parent_uuid`

消息 role 到 event type 的映射：

- `user` -> `user`
- `assistant` -> `assistant`
- `assistant_progress` -> `progress`
- `assistant_thinking` -> `thinking`
- `assistant_tool_call` -> `tool_call`
- `tool_result` -> `tool_result`
- `context_summary` -> `summary`
- `snip_boundary` -> `snip_boundary`

## Append-Only 保存

`save_session()` 会读取已有 event UUID，只追加新消息。正常聊天上下文保存时会跳过开头的 `system` message。

## 加载 Active Context

`load_session()` 读取所有事件，找到最后一个 `compact_boundary`，只恢复该 boundary 之后的事件。

这与 MiniCode 的 resume 行为一致：compact 前的旧历史仍留在事件日志里，但恢复给模型的 active context 从最近的 compact 状态开始。

## Compact Boundary

`append_compact_boundary()` 会追加：

1. 一个 `compact_boundary` 事件
2. 一个 summary user-message 事件
3. retained messages

`main.py` 中的手动压缩会在 `run_manual_compact()` 之后调用这个函数。

## 重命名

`rename_session()` 会追加一个 `rename` 事件。列出会话时会使用最新 rename 事件作为标题。

## Fork

`fork_session()` 会：

1. 从源会话加载 active context。
2. 创建新会话。
3. 把源 active messages 保存到新会话。
4. 添加类似 `<source_title>_fork1` 的标题。

## 清理

`cleanup_expired_sessions()` 默认删除 30 天以前的会话。

## 旧格式兼容

`_read_events()` 可以读取旧 JSONL 文件：这种文件里每行是带 `role` 字段的原始 `ChatMessage`，没有 event wrapper。新的保存都会使用事件格式。

## 当前限制

- 会话只按本地 `.sessions` 目录区分，不像 MiniCode 那样存到全局 per-project 目录 `~/.mini-code/projects`。
- 没有单独的 transcript renderer 模块。
- auto compact 还不会追加 compact boundary event；manual compact 会。
