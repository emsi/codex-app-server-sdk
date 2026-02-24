# Conversation APIs

Two high-level conversation entrypoints are provided:

- `chat_once(...)`: one message in, final assistant text out
- `chat(...)`: async iterator of completed, non-delta step blocks

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

`chat(...)` emits steps from live `item/completed` notifications for the active
turn. It does not merge `thread/read` snapshot items into the same stream.

## Thread binding

You can call APIs directly with `thread_id=...`, or use `ThreadHandle`:

```python
thread = await client.start_thread()
result = await thread.chat_once("Hello")
```

## Per-turn metadata and overrides

- `metadata` is applied on `turn/start`
- `TurnOverrides` controls per-turn execution options (`cwd`, `model`, `effort`, etc.)
