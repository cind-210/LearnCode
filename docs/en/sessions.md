# Sessions

Sessions are implemented in `src/session.py` and stored in `LearnCode/.sessions`.

## Storage Format

Each session is a JSONL file:

```text
.sessions/<session_id>.jsonl
```

Each line is an event object. Message events contain:

- `type`
- `message`
- `uuid`
- `timestamp`
- `session_id`
- `cwd`
- `parent_uuid`

Message roles are mapped to event types:

- `user` -> `user`
- `assistant` -> `assistant`
- `assistant_progress` -> `progress`
- `assistant_thinking` -> `thinking`
- `assistant_tool_call` -> `tool_call`
- `tool_result` -> `tool_result`
- `context_summary` -> `summary`
- `snip_boundary` -> `snip_boundary`

## Append-Only Save

`save_session()` reads existing event UUIDs and appends only new messages. It skips the leading `system` message when saving normal chat context.

## Loading Active Context

`load_session()` reads all events, finds the last `compact_boundary`, and only restores events after that boundary.

This mirrors MiniCode's resume behavior: old pre-compact history remains in the event log, but the active model context resumes from the latest compacted state.

## Compact Boundary

`append_compact_boundary()` appends:

1. a `compact_boundary` event
2. a summary user-message event
3. retained messages

Manual compaction in `main.py` calls this function after `run_manual_compact()`.

## Rename

`rename_session()` appends a `rename` event. Listing sessions uses the latest rename event as the title.

## Fork

`fork_session()`:

1. Loads the active context from the source session.
2. Creates a new session.
3. Saves the source active messages into the new session.
4. Adds a title like `<source_title>_fork1`.

## Cleanup

`cleanup_expired_sessions()` deletes sessions older than 30 days by default.

## Legacy Compatibility

`_read_events()` can read older JSONL files that contain raw `ChatMessage` objects with a `role` field but no event wrapper. New saves use the event format.

## Current Limitations

- Sessions are scoped only by the local `.sessions` directory, not by a global per-project directory like MiniCode's `~/.mini-code/projects`.
- There is no separate transcript renderer module.
- Auto compact does not yet append a compact boundary event; manual compact does.
