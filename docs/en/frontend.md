# Frontend

The frontend is `static/index.html`.

It is a single-file HTML/CSS/JavaScript app served by FastAPI static files.

## Layout

- Header with app title and Compact button.
- Sidebar with New Session and session list.
- Chat area with messages.
- Status bar.
- Textarea input and Send button.

## WebSocket

The browser connects to:

```javascript
new WebSocket(`${protocol}//${location.host}/ws`)
```

On open:

- status becomes connected
- sessions are requested
- send button state updates

On close:

- status becomes disconnected
- reconnects after 2 seconds

## Sending Chat

`sendMessage()`:

1. Reads textarea.
2. Adds a local user message bubble.
3. Sets processing state.
4. Sends:

```json
{"action":"chat","message":"...","session_id":"..."}
```

## Session UI Actions

- `newSession()` sends `new_session`.
- `selectSession(id)` sends `load_session`.
- `startRename()` edits the title inline.
- `finishRename()` sends `rename_session`.
- `autoNameSession()` sends `auto_name_session`.
- `forkSession()` sends `fork_session`.
- `deleteSession()` sends `delete_session`.
- `compactSession()` sends `compact`.

## Rendering Messages

Messages are rendered by `addMessage(role, content)` with roles:

- `user`
- `assistant`
- `tool`
- `error`
- `compact`

Loaded session messages are converted from backend roles:

- `user` -> user bubble
- `assistant` / `assistant_progress` -> assistant bubble
- `assistant_tool_call` -> tool bubble
- `tool_result` -> tool bubble
- `system` -> skipped

## Current Limitations

- No markdown rendering.
- No diff review UI.
- No permission approval modal.
- No streaming token-by-token display; it renders server step messages.
- Tool calls and tool results are plain text blocks.
