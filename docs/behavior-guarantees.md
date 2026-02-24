# Behavior guarantees

This page records intended high-level behavior for consumers.

## Streaming model

- `chat(...)` yields completed, non-delta step blocks.
- step stream is based on live turn notifications (`item/completed`).
- snapshot backfill from `thread/read` is not merged into `chat(...)`.

## Final text resolution

- `chat_once(...)` prefers completed assistant messages from live events.
- fallback to `thread/read(includeTurns=true)` is used when needed to recover final text.

## Timeout model

- request timeout controls request/response calls.
- inactivity timeout controls per-turn waiting for new events.
- no separate turn timeout abstraction.

## Continuation model

- inactivity timeout raises `CodexTurnInactiveError` with continuation token.
- continuation can resume the same running turn in the same client instance.
- continuation cannot be combined with fresh turn input/options.

## Cancel model

- `cancel(...)` sends best-effort interrupt.
- unread steps/events since continuation cursor are returned.
- internal state is cleaned so thread reuse is safe.

## Transport behavior

- context-manager lifecycle (`async with`) is preferred.
- pending requests fail with transport error when client closes.
