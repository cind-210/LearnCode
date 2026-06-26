# API 与 WebSocket

`src/main.py` 暴露少量 HTTP API，以及主要的 WebSocket 运行时。

## HTTP 接口

### `GET /api/health`

返回：

```json
{"status":"ok"}
```

### `GET /api/sessions`

列出 `.sessions` 中的会话。执行前会先调用 `cleanup_expired_sessions()`。

返回项字段：

- `id`
- `title`
- `created_at`
- `updated_at`
- `message_count`
- `workspace`

### `POST /api/sessions`

创建会话。

输入：

```json
{"title":"New Session","workspace":"C:/path/to/workspace"}
```

返回：

```json
{"id":"...","title":"..."}
```

### `GET /api/sessions/{session_id}`

加载一个会话，并返回可见的 active messages。

### `DELETE /api/sessions/{session_id}`

删除会话 JSONL 文件；如果旧 `.meta.json` 存在，也会删除。

## WebSocket: `/ws`

浏览器工作流都使用 JSON 消息：

```json
{"action":"chat","message":"hello","session_id":"optional"}
```

### 客户端 Actions

#### `chat`

运行一次 agent turn。

字段：

- `message`：用户输入
- `workspace`：可选工作目录，默认是服务进程 cwd
- `session_id`：可选已有会话

服务端事件：

- `step`：当前 turn 的模型内容或工具调用
- `done`：最终 turn 状态和 session id
- `error`：运行时错误文本

#### `compact`

对当前活动会话执行手动压缩。如果压缩成功，`append_compact_boundary()` 会记录 compact boundary 和保留消息。

#### `rename_session`

追加一个 rename 事件。

字段：

- `session_id`
- `title`

#### `delete_session`

删除一个会话并刷新会话列表。

#### `auto_name_session`

请求当前配置的模型生成短标题，然后追加 rename 事件。

#### `load_session`

从会话加载 active messages，并发送 `session_loaded`。

#### `new_session`

创建一个标题为 `New Session` 的持久空会话。

#### `fork_session`

基于已有会话的 active context 创建新会话。

#### `list_sessions`

发送会话列表。

## 服务端事件

- `step`：`{type, content, kind, calls, usage}`
- `done`：`{stop_reason, turn, session_id}`
- `error`：字符串
- `sessions`：`{id,title,updated_at}` 列表
- `session_loaded`
- `session_created`
- `session_renamed`
- `session_deleted`
- `session_forked`
- `compact`

## 当前限制

- 部分 not-found 情况会返回 error 对象，而不是 HTTP 状态码。
- WebSocket 错误处理会捕获宽泛异常并把文本返回 UI。
- 没有认证层。
