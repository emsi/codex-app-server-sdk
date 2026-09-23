# Conversation APIs

## Related API

- [`CodexClient.chat_once(...)`](api/client.md#codex_app_server_sdk.client.CodexClient.chat_once)
- [`CodexClient.chat(...)`](api/client.md#codex_app_server_sdk.client.CodexClient.chat)
- [`CodexClient.start_thread(...)`](api/client.md#codex_app_server_sdk.client.CodexClient.start_thread)
- [`ThreadHandle`](api/client.md#codex_app_server_sdk.client.ThreadHandle)
- [`TurnOverrides`](api/models.md#codex_app_server_sdk.models.TurnOverrides)

Two high-level conversation entrypoints are provided:

- [`chat_once(...)`](api/client.md#codex_app_server_sdk.client.CodexClient.chat_once): one message in, final assistant text out
- [`chat(...)`](api/client.md#codex_app_server_sdk.client.CodexClient.chat): async iterator of completed, non-delta step blocks

## `chat_once(...)`

Use when you only need final text.

```python
result = await client.chat_once("Summarize this repository")
print(result.final_text)
print(result.thread_id, result.turn_id)
```

## `chat(...)`

Use when you need structured step updates (`thinking`, `exec`, `codex`, etc.).

```python
async for step in client.chat("Diagnose this failure"):
    print(step.step_type, step.item_type, step.text)
```

### Step source semantics

[`chat(...)`](api/client.md#codex_app_server_sdk.client.CodexClient.chat) emits steps from live `item/completed` notifications for the active
turn. It does not merge `thread/read` snapshot items into the same stream.

## Thread binding

You can call APIs directly with `thread_id=...`, or use [`ThreadHandle`](api/client.md#codex_app_server_sdk.client.ThreadHandle):

```python
thread = await client.start_thread()
result = await thread.chat_once("Hello")
```

## Per-turn metadata and overrides

- `metadata` is applied on `turn/start`
- [`TurnOverrides`](api/models.md#codex_app_server_sdk.models.TurnOverrides) controls per-turn execution options (`cwd`, `model`, `effort`, etc.)
- when resuming with `continuation=...`, do not pass `text`, `thread_id`,
  `user`, `metadata`, `thread_config`, or `turn_overrides`

For collaboration modes (`default`/`plan`) and server-driven user questions,
see [Human-in-the-loop](human-in-the-loop.md).
