# 会话保存和恢复

会话代码在 `src/sessions/store.py`。每个 session 对应一个 JSONL 文件：

```text
LearnCode/.sessions/<session_id>.jsonl
```

JSONL 是一行一个 JSON。LearnCode 不把整个会话重写成一个大 JSON，而是把消息、改名、压缩边界这些动作追加成事件。

## 创建 session

前端没有 `session_id` 就直接发送聊天消息时，后端会先创建 session。

新 session 文件里会先写一条 `rename` 事件，用来保存标题 `New Session`：

```json
{
  "type": "rename",
  "title": "New Session",
  "uuid": "event id",
  "timestamp": "2026-06-26T00:00:00.000Z",
  "session_id": "session id",
  "cwd": "workspace",
  "parent_uuid": null
}
```

然后后端马上发 `session_created` 给前端，所以左侧列表可以立刻出现新 session。

## 消息事件

用户消息、模型回复、工具调用、工具结果都会写成事件。

一条普通用户消息大致是：

```json
{
  "type": "user",
  "message": {
    "role": "user",
    "content": "你好"
  },
  "uuid": "message id",
  "timestamp": "2026-06-26T00:00:00.000Z",
  "session_id": "session id",
  "cwd": "workspace",
  "parent_uuid": "上一条事件 id"
}
```

工具调用消息大致是：

```json
{
  "type": "tool_call",
  "message": {
    "role": "assistant_tool_call",
    "tool_use_id": "call_xxx",
    "tool_name": "read_file",
    "input": {"path": "README.md"}
  },
  "uuid": "message id",
  "timestamp": "2026-06-26T00:00:00.000Z",
  "session_id": "session id",
  "cwd": "workspace",
  "parent_uuid": "上一条事件 id"
}
```

工具结果消息大致是：

```json
{
  "type": "tool_result",
  "message": {
    "role": "tool_result",
    "tool_use_id": "call_xxx",
    "tool_name": "read_file",
    "content": "工具输出",
    "is_error": false
  },
  "uuid": "message id",
  "timestamp": "2026-06-26T00:00:00.000Z",
  "session_id": "session id",
  "cwd": "workspace",
  "parent_uuid": "上一条事件 id"
}
```

## 保存消息

本次用户输入处理完后，后端把这次处理期间产生的消息追加到文件末尾。保存前会读取已有事件的 `uuid`，已经保存过的消息不会重复写。

开头的 system message 不写入 session 文件，因为它可以在下次需要时重新生成。

## 加载 session

用户点击左侧 session 时，后端读取对应 JSONL 文件。

加载时会：

1. 读取所有事件。
2. 如果有 `compact_boundary`，只取文件里最后一个 `compact_boundary` 之后的事件。
3. 把事件里的 `message` 转回聊天消息。
4. 返回给前端显示，也交给下一次聊天继续使用。

返回给前端的消息大致是：

```json
{
  "role": "assistant",
  "content": "模型回复",
  "tool_name": "",
  "input": null,
  "is_error": false
}
```

## 标题

标题不是文件名。标题来自最新一条 `rename` 事件。

手动改名和 AI 自动命名都会追加 `rename`：

```json
{
  "type": "rename",
  "title": "新标题",
  "uuid": "event id",
  "timestamp": "2026-06-26T00:00:00.000Z",
  "session_id": "session id",
  "cwd": "workspace",
  "parent_uuid": "上一条事件 id"
}
```

列出 session 时，代码扫描文件，使用最后一个 `rename.title`。

## 手动 Compact

手动压缩成功后，文件里会追加 `compact_boundary`。加载 session 时，最后一个 `compact_boundary` 之前的事件不会恢复成消息列表。

`compact_boundary` 大致是：

```json
{
  "type": "compact_boundary",
  "subtype": "compact_boundary",
  "uuid": "boundary id",
  "timestamp": "2026-06-26T00:00:00.000Z",
  "session_id": "session id",
  "cwd": "workspace",
  "logical_parent_uuid": "压缩前最后一条事件 id",
  "compact_metadata": {
    "trigger": "manual",
    "pre_tokens": 10000,
    "post_tokens": 3000
  }
}
```

`compact_boundary` 后面会继续写摘要消息和压缩后保留的消息。

## Snip 边界

自动压缩里的 snip compact 可能会产生 `snip_boundary` 消息。它不会物理删除 JSONL 里的原事件，只影响下次加载出来的消息列表。

文件里会保留原消息，同时在文件末尾追加一条边界事件，记录哪些消息不会再进入后续模型上下文：

```json
{
  "type": "snip_boundary",
  "subtype": "snip_boundary",
  "message": {
    "role": "snip_boundary",
    "content": "中间一段消息被裁掉",
    "removed_message_ids": ["message id"],
    "removed_count": 12,
    "tokens_freed": 5000
  },
  "snip_metadata": {
    "type": "snip_boundary",
    "removed_message_ids": ["message id"],
    "removed_count": 12,
    "tokens_freed": 5000
  }
}
```

加载时，`uuid` 在 `removed_message_ids` 里的原消息不会进入恢复后的消息列表，而是由这条 `snip_boundary` 代替。消息太长时的触发顺序看 [context.md](context.md)。

## Fork

`fork_session()` 会复制一个 session 按上面加载规则恢复出来的消息。

步骤是：

1. 加载源 session。
2. 创建新 session。
3. 把源 session 恢复出来的消息保存到新 session。
4. 给新 session 追加标题，形如 `<原标题>_fork1`。

如果源 session 找不到或没有消息，fork 会失败。

## 删除和清理

`delete_session()` 删除 `.jsonl` 文件。如果旧的 `.meta.json` 存在，也会删除。

`cleanup_expired_sessions()` 会删除 30 天以前没有更新的 `.jsonl` 文件。

## 旧格式读取

读取时仍兼容旧文件：如果某行直接是 `{"role":"user","content":"..."}`，没有外层事件，代码会临时包装成事件再处理。

保存时只写事件格式。

## 当前边界

- session 保存在项目目录的 `.sessions`，不是全局 per-project 目录。
- `compact_boundary` 只来自手动 Compact；snip compact 会保存 `snip_boundary` 这类消息事件。
- 没有单独的 transcript 渲染模块。
