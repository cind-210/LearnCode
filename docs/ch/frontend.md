# 前端页面流程

前端只有 `static/index.html` 一个文件，里面同时写 HTML、CSS 和 JavaScript。它不直接请求模型，只和后端 WebSocket 通信。完整 WebSocket action 和 event 结构看 [api.md](api.md)。

## 页面打开

页面加载后会连接 `/ws`。连接成功后，前端会：

1. 把底部状态改成 Connected。
2. 启用 Send 按钮。
3. 发送 `list_sessions`，要求后端刷新左侧 session 列表。

后端连接建立时也会主动发 workspace 和 session 列表，所以正常情况下不需要等用户先发消息才显示 session。

## 用户发送消息

用户点 Send 后，前端先更新页面，再把 `chat` 发给后端。

发送前会：

1. 读取输入框。
2. 把用户消息显示到聊天区。
3. 清空输入框。
4. 把 `processing` 设为 `true`。
5. 禁用 Send 按钮。

如果当前没有 session，后端会创建新 session，再返回给前端。

前端这里先显示用户消息，只改变浏览器页面；真正写入 session 文件发生在后端发送 `done` 前。

## WebSocket 事件更新页面

所有 WebSocket 消息都会进入 `handleEvent(type, data)`。前端主要处理这些事件：

- `workspace`：更新顶部 workspace 文本。
- `sessions`：重新渲染左侧列表。
- `session_created`：记录当前 session id；如果正在自动命名，标题旁边显示转圈。
- `session_loaded`：清空聊天区，把历史消息重新画出来。
- `session_renamed`：更新左侧标题，取消命名转圈。
- `session_deleted`：删除左侧对应项。
- `session_forked`：切到复制出来的新 session。
- `step`：显示模型中间输出、工具调用或最终回答。
- `done`：解除发送按钮禁用，必要时触发自动命名。
- `error`：显示错误，并解除发送按钮禁用。
- `compact`：显示手动压缩结果。

这些事件只改变浏览器页面状态。session 文件怎么写由后端决定，看 [sessions.md](sessions.md)。

## 显示普通回答

后端发来普通 assistant step 时，前端把它显示成一条 assistant 消息。

如果聊天区里已经有本次用户输入对应的 agent loop 容器，最终回答会作为最后结果显示，中间过程折叠起来，用户可以再展开。

## 显示工具调用

后端发来工具调用 step 时，前端会创建或更新一个 agent loop 容器。

这个容器里按顺序显示：

1. 模型在工具调用前写出的文本。
2. 每个工具调用块。

每个工具调用单独显示成一块，显示工具名和输入 JSON。工具返回内容不会作为单独块完整显示；它会进入后端消息列表，再被模型用于下一次回答。

如果一次用户输入里有多轮工具调用，本次用户输入产生的中间文本和工具块会继续追加到同一个容器。内容增长时会自动滚动到底部。

## Session 列表

左侧 session 列表由后端发来的 `sessions` 事件渲染。

每个 session 显示：

- 标题。
- 手动重命名按钮。
- AI 自动命名按钮。
- Fork 按钮。
- Delete 按钮。

如果某个 session 正在自动命名，标题旁边显示转圈。

## 自动命名

新 session 第一次 `done` 后，如果后端要求自动命名，前端会发送 `auto_name_session`。

命名前，左侧标题仍显示 `New Session`，旁边显示转圈。后端返回改名事件后，前端替换成新标题。

## 当前边界

- 没有 Markdown 渲染。
- 没有代码 diff 专用视图。
- 没有工具权限审批弹窗。
- 不是 token-by-token 流式输出，而是按后端 `step` 事件更新。
