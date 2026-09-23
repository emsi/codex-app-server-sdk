# Behavior guarantees

This page records intended high-level behavior for consumers.

## Related API

- [`CodexClient.chat(...)`](api/client.md#codex_app_server_sdk.client.CodexClient.chat)
- [`CodexClient.chat_once(...)`](api/client.md#codex_app_server_sdk.client.CodexClient.chat_once)
- [`CodexTurnInactiveError`](api/errors.md#codex_app_server_sdk.errors.CodexTurnInactiveError)
- [`CodexClient.cancel(...)`](api/client.md#codex_app_server_sdk.client.CodexClient.cancel)
- [`CodexClient`](api/client.md#codex_app_server_sdk.client.CodexClient)

## Streaming model

- [`chat(...)`](api/client.md#codex_app_server_sdk.client.CodexClient.chat) yields completed, non-delta step blocks.
- step stream is based on live turn notifications (`item/completed`).
- snapshot backfill from `thread/read` is not merged into [`chat(...)`](api/client.md#codex_app_server_sdk.client.CodexClient.chat).

## Final text resolution

- [`chat_once(...)`](api/client.md#codex_app_server_sdk.client.CodexClient.chat_once) prefers completed assistant messages from live events.
- fallback to `thread/read(includeTurns=true)` is used when needed to recover final text.

## Timeout model

- request timeout controls request/response calls.
- inactivity timeout controls per-turn waiting for new events.
- no separate turn timeout abstraction.

## Continuation model

- inactivity timeout raises [`CodexTurnInactiveError`](api/errors.md#codex_app_server_sdk.errors.CodexTurnInactiveError) with continuation token.
- continuation can resume the same running turn in the same client instance.
- continuation cannot be combined with fresh turn input/options.

## Cancel model

- [`cancel(...)`](api/client.md#codex_app_server_sdk.client.CodexClient.cancel) sends both thread and turn identifiers and waits for terminal confirmation.
- unread steps/events since continuation cursor are returned.
- internal state is cleaned only after terminal confirmation; interruption errors
  propagate and confirmation timeouts retain the continuation and unread cursor.

## Concurrent and unsuccessful turns

- Turn consumers wake independently and consume only events for their own turn.
- Events received before the turn-start response are retained.
- Failed and interrupted terminal statuses raise `CodexProtocolError` from both
  chat APIs; partial assistant messages do not turn a failed turn into success.
- Initialization runs once per connection, including concurrent first calls.

## Transport behavior

- context-manager lifecycle (`async with`) is preferred.
- pending requests fail with transport error when client closes.
- All waiting turn consumers wake on connection failure or client close.
