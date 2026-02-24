# Timeouts, continuation, cancel

## Request timeout vs inactivity timeout

- request timeout: JSON-RPC request/response envelope timeout
- inactivity timeout: per-turn "no new matching events" timeout

`turn_timeout` is intentionally not used.

## Continuation flow

When a turn goes inactive, APIs raise `CodexTurnInactiveError` with a
`continuation` token.

```python
continuation = None
while True:
    try:
        if continuation is None:
            result = await client.chat_once("Do a long task")
        else:
            result = await client.chat_once(continuation=continuation)
        break
    except CodexTurnInactiveError as exc:
        continuation = exc.continuation
```

Continuation is tied to in-memory session state in the same client instance.

## Continuation constraints

When `continuation=...` is used, do not pass:

- `text`
- `thread_config`
- `turn_overrides`

## Cancel semantics

Use `cancel(continuation)` to interrupt a running turn and drain unread data.

```python
cancelled = await client.cancel(exc.continuation)
print(cancelled.was_interrupted, len(cancelled.steps), len(cancelled.raw_events))
```

`cancel(...)` cleans internal turn state so the thread can be reused safely.
