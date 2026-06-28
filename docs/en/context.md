# Context Management

Context management lives under `src/context`.

## Token Accounting

`src/context/token_estimator.py` estimates tokens and computes context stats.

Important functions:

- `estimate_message_tokens`
- `estimate_messages_tokens`
- `token_count_with_estimation`
- `mark_provider_usage_stale`
- `compute_context_stats`

Provider usage is stored on assistant/progress/tool-call messages when the model adapter returns usage.

## Model Context Windows

`src/context/model_context.py` maps model names to context limits.

`get_model_context_window(model)` returns the configured context window used by compaction logic.

## Large Tool Results

`src/context/tool_result_storage.py` persists large tool outputs under:

```text
~/.learncode/tool-results
```

Core behavior:

- outputs over `50_000` chars are persisted
- visible context gets a short preview plus file path
- batches are reduced toward a visible budget

## Microcompact

`src/context/compact/microcompact.py` clears older compactable tool results while retaining recent ones.

Compactable tools are defined in `src/context/tool_limits.py`.

## Manual Compact

`src/context/compact/manual_compact.py` calls `compact_conversation()`.

In the web runtime, successful manual compact is persisted with `append_compact_boundary()`.

## LLM Compact

`src/context/compact/compact.py`:

1. Chooses a retention boundary.
2. Converts older messages to text.
3. Sends a summarization prompt to the model.
4. Builds a `context_summary` message.
5. Keeps system messages, summary, and recent messages.

## Auto Compact

`src/context/compact/auto_compact.py` decides when to compact based on context pressure.

Current state is process-local.

## Snip Compact

`src/context/compact/snip_compact.py` removes safe middle-history ranges while protecting:

- file modification tools
- important errors
- unclosed tool-call groups
- recent context

It inserts a `snip_boundary` message.

## Context Collapse

`src/context/compact/context_collapse.py` creates a projection-layer collapsed view for long conversations. The agent loop calls `project_collapsed_view()` before model requests.

## Current Limitations

- Manual compact is the only compaction path persisted with explicit session compact boundaries.
- Large tool result storage uses `.learncode` naming.
- Context collapse state is in-memory for the active loop.
