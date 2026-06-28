# API and WebSocket

`src/main.py` exposes a small HTTP API plus the main WebSocket runtime.

## HTTP Endpoints

### `GET /api/health`

Returns:

```json
{"status":"ok"}
```

### `GET /api/workspace`

Returns the workspace bound to the current server process. LearnCode's workspace is the server startup directory, or the `WORKSPACE` environment variable when set.

```json
{"workspace":"C:/path/to/project"}
```

### `GET /api/sessions`

Lists sessions from `.sessions`. It calls `cleanup_expired_sessions()` first.

Returned item fields:

- `id`
- `title`
- `created_at`
- `updated_at`
- `message_count`
- `workspace`

### `POST /api/sessions`

Creates a session.

Input:

```json
{"title":"New Session"}
```

Returns:

```json
{"id":"...","title":"..."}
```

### `GET /api/sessions/{session_id}`

Loads one session and returns visible active messages.

### `DELETE /api/sessions/{session_id}`

Deletes the session JSONL file and old `.meta.json` file if present.

## WebSocket: `/ws`

All browser workflow uses JSON messages:

```json
{"action":"chat","message":"hello","session_id":"optional"}
```

### Client Actions

#### `chat`

Runs one agent turn.

Fields:

- `message`: user input
- `session_id`: optional existing session

Server events:

- `step`: model content or tool calls for the current turn
- `done`: final turn status and session id
- `error`: runtime error text

#### `compact`

Runs manual compaction on the active session. If compaction succeeds, `append_compact_boundary()` records the boundary and retained messages.

#### `rename_session`

Appends a rename event.

Fields:

- `session_id`
- `title`

#### `delete_session`

Deletes one session and refreshes the session list.

#### `auto_name_session`

Asks the configured model to generate a short title, then appends a rename event.

#### `load_session`

Loads active messages from a session and sends `session_loaded`.

#### `new_session`

Creates a persistent empty session with title `New Session`.

#### `fork_session`

Creates a new session from the active context of an existing session.

#### `list_sessions`

Sends the session list.

## Server Events

- `step`: `{type, content, kind, calls, usage}`
- `done`: `{stop_reason, turn, session_id}`
- `error`: string
- `sessions`: list of `{id,title,updated_at}`
- `session_loaded`
- `session_created`
- `session_renamed`
- `session_deleted`
- `session_forked`
- `compact`

## Current Limitations

- HTTP endpoints return error objects rather than raising HTTP status codes for some not-found cases.
- WebSocket error handling catches broad exceptions and returns text to the UI.
- There is no authentication layer.
