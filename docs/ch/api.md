# 浏览器和后端通信

后端接口在 `src/web/main.py`。页面加载来自静态文件，聊天主要走 WebSocket，HTTP 接口只做健康检查、workspace 和 session 的简单读写。

## HTTP 接口

### `GET /api/health`

检查后端是否启动。

返回：

```json
{"status": "ok"}
```

### `GET /api/workspace`

返回后端当前 workspace。默认是启动服务时所在目录，也可以用 `WORKSPACE` 环境变量覆盖。

返回：

```json
{"workspace": "C:/path/to/project"}
```

### `GET /api/sessions`

读取 `.sessions` 目录，返回 session 列表。

返回项大致是：

```json
{
  "id": "session id",
  "title": "标题",
  "created_at": 1710000000000,
  "updated_at": 1710000000000,
  "message_count": 3,
  "workspace": "C:/path/to/project"
}
```

### `POST /api/sessions`

创建一个空 session。

请求：

```json
{"title": "New Session"}
```

返回：

```json
{"id": "session id", "title": "New Session"}
```

### `GET /api/sessions/{session_id}`

读取一个 session 的消息。

返回：

```json
{
  "id": "session id",
  "title": "标题",
  "messages": [
    {
      "role": "user",
      "content": "你好",
      "tool_name": "",
      "is_error": false,
      "timestamp": 0
    }
  ]
}
```

如果找不到 session，返回：

```json
{"error": "Session not found"}
```

### `DELETE /api/sessions/{session_id}`

删除对应的 `.jsonl` 文件。如果旧的 `.meta.json` 文件存在，也会一起删除。

返回：

```json
{"ok": true}
```

## WebSocket 连接

浏览器打开后连接 `/ws`。连接建立后，后端先主动发两个事件。

当前 workspace：

```json
{
  "type": "workspace",
  "data": {"workspace": "C:/path/to/project"}
}
```

当前 session 列表：

```json
{
  "type": "sessions",
  "data": [
    {
      "id": "session id",
      "title": "标题",
      "updated_at": 1710000000000
    }
  ]
}
```

之后前端继续通过 WebSocket 发送 action。

## `chat`

用户点 Send 时发送。

```json
{
  "action": "chat",
  "message": "你好",
  "session_id": "可选"
}
```

后端收到后：

1. 如果已有一条消息正在处理，返回 `error`。
2. 没有 `session_id` 时创建 `New Session`，并立刻发 `session_created` 和创建后的完整 `sessions` 列表。
3. 加载历史消息。
4. 调用 agent loop。
5. 模型每返回一步，发 `step`。
6. 模型给出最终回答或循环停止后保存 session，发 `done`。

这个 action 会改变 session 文件：没有 `session_id` 时会创建文件；模型给出最终回答或循环停止后，会追加本次用户输入产生的消息。

`step` 事件大致是：

```json
{
  "type": "step",
  "data": {
    "type": "tool_calls",
    "content": "我先读取文件。",
    "kind": "progress",
    "calls": [
      {
        "id": "call_xxx",
        "tool_name": "read_file",
        "input": {"path": "README.md"}
      }
    ],
    "usage": null
  }
}
```

`done` 事件大致是：

```json
{
  "type": "done",
  "data": {
    "stop_reason": "final",
    "turn": 1,
    "session_id": "session id",
    "auto_name": true
  }
}
```

## `new_session`

用户点 New Session 时发送。

```json
{"action": "new_session"}
```

后端创建标题为 `New Session` 的空 session，返回 `session_created`，再返回创建后的完整 `sessions` 列表。

## `load_session`

用户点击左侧 session 时发送。

```json
{
  "action": "load_session",
  "session_id": "session id"
}
```

后端返回：

```json
{
  "type": "session_loaded",
  "data": {
    "id": "session id",
    "title": "标题",
    "message_count": 2,
    "messages": [
      {
        "role": "user",
        "content": "你好",
        "tool_name": "",
        "input": null,
        "is_error": false
      }
    ]
  }
}
```

这个 action 只读取 session，不会修改 session 文件。

## `rename_session`

用户手动改标题时发送。用户手动标题不走 AI 标题长度校验。

```json
{
  "action": "rename_session",
  "session_id": "session id",
  "title": "新标题"
}
```

后端追加一条 `rename` 事件，然后发：

```json
{
  "type": "session_renamed",
  "data": {"id": "session id", "title": "新标题"}
}
```

## `auto_name_session`

用户点 AI，或者新 session 第一次回答完成后，前端发送：

```json
{
  "action": "auto_name_session",
  "session_id": "session id"
}
```

后端把前两条用户消息拼成标题生成输入，请模型只返回标题。AI 标题校验规则是：

- 可以有空格。
- 不能有标点。
- 中文标题去掉空格后最多 10 个字。
- 英文标题去掉空格后最多 20 个字符。
- 最多让模型重试 3 次，3 次都失败就用最后一次生成的标题。

这个 action 只改标题，会追加 `rename` 事件，不会改聊天消息。

## 删除、复制、压缩和刷新列表

`delete_session` 删除 session：

```json
{"action": "delete_session", "session_id": "session id"}
```

`fork_session` 复制当前能加载出来的消息，创建新 session：

```json
{"action": "fork_session", "session_id": "session id"}
```

`compact` 对当前 session 做手动压缩：

```json
{"action": "compact"}
```

这个 action 会追加 `compact_boundary` 和压缩后的保留消息。压缩规则看 [context.md](context.md)，文件结构看 [sessions.md](sessions.md)。

`list_sessions` 让后端重新发送 session 列表：

```json
{"action": "list_sessions"}
```

## 错误事件

后端遇到可返回给前端的问题时，会发：

```json
{
  "type": "error",
  "data": "错误文本"
}
```

前端收到后会显示错误，并恢复发送按钮。

## 当前边界

- 没有鉴权。
- HTTP 和 WebSocket 有部分 session 功能重复，前端主要用 WebSocket。
- WebSocket 一次只允许处理一条聊天消息。
