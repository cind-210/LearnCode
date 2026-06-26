# 前端

前端是 `static/index.html`。

它是一个单文件 HTML/CSS/JavaScript 应用，由 FastAPI 静态文件服务提供。

## 布局

- Header：应用标题和 Compact 按钮。
- Sidebar：New Session 和会话列表。
- Chat area：消息区。
- Status bar：连接/处理状态。
- Textarea input 和 Send 按钮。

## WebSocket

浏览器连接：

```javascript
new WebSocket(`${protocol}//${location.host}/ws`)
```

连接打开时：

- 状态变为 connected
- 请求会话列表
- 更新发送按钮状态

连接关闭时：

- 状态变为 disconnected
- 2 秒后重连

## 发送聊天

`sendMessage()`：

1. 读取 textarea。
2. 本地添加一个 user message bubble。
3. 设置 processing 状态。
4. 发送：

```json
{"action":"chat","message":"...","session_id":"..."}
```

## 会话 UI 操作

- `newSession()` 发送 `new_session`。
- `selectSession(id)` 发送 `load_session`。
- `startRename()` 内联编辑标题。
- `finishRename()` 发送 `rename_session`。
- `autoNameSession()` 发送 `auto_name_session`。
- `forkSession()` 发送 `fork_session`。
- `deleteSession()` 发送 `delete_session`。
- `compactSession()` 发送 `compact`。

## 消息渲染

消息由 `addMessage(role, content)` 渲染，支持角色：

- `user`
- `assistant`
- `tool`
- `error`
- `compact`

加载会话时，后端 roles 会转换为：

- `user` -> user bubble
- `assistant` / `assistant_progress` -> assistant bubble
- `assistant_tool_call` -> tool bubble
- `tool_result` -> tool bubble
- `system` -> 跳过

## 当前限制

- 没有 Markdown 渲染。
- 没有 diff review UI。
- 没有权限审批弹窗。
- 没有 token-by-token 流式显示；它渲染服务端 step 消息。
- 工具调用和工具结果只是纯文本块。
