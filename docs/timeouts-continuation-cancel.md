# Timeouts, continuation, cancel

## Related API

- [`CodexClient.chat_once(...)`](api/client.md#codex_app_server_sdk.client.CodexClient.chat_once)
- [`CodexClient.chat(...)`](api/client.md#codex_app_server_sdk.client.CodexClient.chat)
- [`CodexTurnInactiveError`](api/errors.md#codex_app_server_sdk.errors.CodexTurnInactiveError)
- [`ChatContinuation`](api/models.md#codex_app_server_sdk.models.ChatContinuation)
- [`CodexClient.cancel(...)`](api/client.md#codex_app_server_sdk.client.CodexClient.cancel)

## Request timeout vs inactivity timeout

- request timeout: JSON-RPC request/response envelope timeout
- inactivity timeout: per-turn "no new matching events" timeout

`turn_timeout` is intentionally not used.

## Continuation flow

When a turn goes inactive, APIs raise [`CodexTurnInactiveError`](api/errors.md#codex_app_server_sdk.errors.CodexTurnInactiveError) with a
[`continuation`](api/models.md#codex_app_server_sdk.models.ChatContinuation) token.

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
- `thread_id`
- `user`
- `metadata`
- `thread_config`
- `turn_overrides`

## Cancel semantics

Use `cancel(continuation)` to interrupt a running turn and drain unread data.

```python
cancelled = await client.cancel(exc.continuation)
print(cancelled.was_interrupted, len(cancelled.steps), len(cancelled.raw_events))
```

[`cancel(...)`](api/client.md#codex_app_server_sdk.client.CodexClient.cancel)
cleans internal turn state only after observing a terminal event. An RPC error
propagates and leaves the continuation usable. If the interrupt is acknowledged
but completion does not arrive within `timeout`, `CodexTurnInactiveError`
contains the original continuation, including its unread cursor. Resume that
continuation or retry cancellation; do not assume the server stopped working.

`was_interrupted` means the server confirmed interruption. `was_completed`
means successful completion was observed instead. These flags describe observed
outcomes, rather than merely recording that an interrupt request was sent.

For low-level turns, call `interrupt_turn(turn_id, thread_id=thread_id)`.
The SDK infers `thread_id` when it still owns the turn session.
