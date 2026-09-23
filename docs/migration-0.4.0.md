# Upgrading from 0.3.2 to 0.4.0

**0.4.0 is not a drop-in behavior-compatible upgrade.** Existing public methods
and imports remain available, but error handling, cancellation, initialization,
and unanswered user questions behave differently. Applications that respond to
the approval stream must explicitly select `approval_mode="manual"`.

This guide compares the published `v0.3.2` source with 0.4.0. Review the changes
below before upgrading an existing application, including custom transports,
test doubles, and code that inspects raw events or model fields.

## Manual approvals: explicitly select the response mode

If your application reads `approval_requests()` and responds through
`respond_approval()`, `approve_approval()`, `decline_approval()`, or
`cancel_approval()` **without an approval callback**, change client creation:

```python
from codex_app_server_sdk import CodexClient

# Before: manual responses raced the automatic decline in 0.3.2.
client = CodexClient.connect_stdio()

# After: the application owns responses to pending approvals.
client = CodexClient.connect_stdio(approval_mode="manual")
```

The same keyword is available on `connect_websocket()` and the `CodexClient`
constructor. It must be chosen before requests arrive; merely consuming
`approval_requests()` does not switch modes.

| Configuration | 0.4.0 behavior |
| --- | --- |
| Default `approval_mode="auto"`, no callback | Automatically declines approvals, as 0.3.2 did. The stream can observe them but is not a reliable manual response mechanism. |
| `approval_mode="manual"`, no callback | Leaves approvals pending until the application responds or the server resolves them. There is no automatic decline timer. |
| `set_approval_handler(...)` registered, either mode | Uses the callback to decide; the stream is observational. Choose one response mechanism per request. |

The new option fixes the old manual-response race; it does not change the
default to accept approvals. It controls client-side response handling, not
the server's `approval_policy` or sandbox settings. It also does not control
user-input questions (see below).

With manual responses, keep the approval loop running concurrently with the
conversation. A long human wait can still raise `CodexTurnInactiveError`;
retain its continuation or configure the client's inactivity timeout for your
UI. See [the complete approval example](approvals-and-sandbox.md#stream-mode-manual-response).

Approval responses are also checked more strictly:

- Responses to requests already handled, cleaned up with a turn, or resolved by
  `serverRequest/resolved` raise `CodexProtocolError`. Do not retry a resolved
  prompt as if it were still awaiting a decision.
- A response must match the complete pending request, not just its ID and
  request class. Use the received request rather than reconstructing it with
  different fields.
- Concurrent responders can no longer both send a decision. Invalid decisions
  and failed sends no longer remove the pending request before a successful
  send. Retaining a request after a transport failure does not guarantee that
  retrying on that connection will work or that the server received nothing.

## Failed and interrupted conversations now raise

In 0.3.2, a `turn/completed` notification containing `turn.status="failed"` or
`"interrupted"` could appear successful, including returning partial assistant
text. In 0.4.0, both `chat_once()` and `chat()` raise `CodexProtocolError` for
these terminal states. The same applies through `ThreadHandle`.

Update error handling around the whole awaited call or stream iteration:

```python
from codex_app_server_sdk import CodexProtocolError

try:
    result = await client.chat_once("Run the task")
except CodexProtocolError as exc:
    print(f"Turn did not succeed: {exc}")
else:
    print(result.final_text)
```

A stream may yield completed steps before raising. Receiving an assistant step
does not establish successful turn completion; wait for normal iterator
exhaustion. For these terminal-status errors, `exc.data` contains the terminal
turn payload. Other protocol errors need not have that payload.

`chat_once()` does not return a partial `ChatResult` on failure. If your UI needs
partial output, retain streamed steps or explicitly read the server's thread
history. Do not blindly retry a failed turn: it may already have performed work.

## Cancellation requires ownership and terminal confirmation

| Area | 0.3.2 behavior | 0.4.0 behavior and migration |
| --- | --- | --- |
| Interrupt failure | `cancel()` suppressed interrupt errors and could return a result. | RPC, request-timeout, and transport failures propagate. Handle `CodexProtocolError`, `CodexTimeoutError`, and `CodexTransportError` as applicable. |
| Confirmation timeout | Could return without observing a terminal event and discard local state. | Raises `CodexTurnInactiveError` and preserves the original continuation, including the unread cursor. Keep it to continue waiting or retry cancellation on a working connection. |
| Missing local turn | Attempted best-effort interruption even without the retained turn session. | Raises `CodexProtocolError` for an unknown or no-longer-retained turn. Use the continuation with the same client while it still owns that turn. |
| Already terminal turn | Could send another interrupt unnecessarily. | Consumes a buffered terminal event first, returns its outcome, and cleans local state. A second cancellation with that token then raises. |
| `CancelResult.was_interrupted` | Indicated that the interrupt RPC returned successfully. | Means the server confirmed an interrupted terminal state. An RPC acknowledgement alone is insufficient. |
| `CancelResult.was_completed` | Reflected a generic completion event, including unsuccessful terminal statuses. | Means observed successful completion, excluding failure and interruption. |
| Low-level `interrupt_turn()` | Sent only the turn ID. | Sends both thread and turn IDs. Pass `thread_id` for turns the client is not tracking; missing or conflicting thread IDs raise `ValueError`. |

Continuations are not durable server-side resume tokens. The ownership check
now also applies to cancellation: tokens from a different/recreated client or
from a session already cleaned up are unusable. Retained state after an error
does not make a broken transport reusable. To recover through a new connection,
use server thread/turn identifiers and the low-level APIs as appropriate.

`cancel()` can return a terminal failure with both result flags false; inspect
the terminal event in `raw_events` instead of assuming every returned result
means interruption or success. This differs from the conversation APIs, which
raise for failed or interrupted terminal states.

For a low-level turn, supply its thread explicitly:

```python
await client.interrupt_turn(turn_id, thread_id=thread_id)
```

This low-level method waits for the RPC response, not terminal confirmation.
See [timeouts and cancellation](timeouts-continuation-cancel.md).

## Initialization runs once per client connection

In 0.3.2, repeated explicit `initialize()` calls sent additional requests. In
0.4.0, the first successful initialization is cached, including concurrent
callers. Later calls return the same cached `InitializeResult`; their parameters
and timeout do not reconfigure the connection or perform a round trip.

If you need custom initialization settings, call `initialize(params=...)`
before a high-level method initializes implicitly. Reinitialization is not a
configuration or health-check mechanism; create a new connection when different
initialization settings are needed. Avoid mutating the shared cached result.

Custom transports, proxies, and scripted test servers must account for:

- A new `{"method": "initialized"}` notification sent after the successful
  initialize response. It has no request ID and expects no response.
- Default `clientInfo.version` now uses installed package metadata (`0.4.0`
  for this release), replacing the hardcoded `0.1.0`. Update exact-payload
  assertions. The default `protocolVersion="1"` is unchanged and is separate
  from the SDK version.

## Unanswered user questions now wait by default

Previously, `item/tool/requestUserInput` was unsupported and immediately
received JSON-RPC error `-32601`. It is now supported and delivered to the
callback or `user_input_requests()` stream. Without a callback or manual answer,
the SDK waits **300 seconds** by default, then replies with error `-32000`.

An existing unattended application can therefore appear to stall when a
server asks a question. The default conversation inactivity timeout is only
180 seconds, so `CodexTurnInactiveError` can arrive before the input timeout.

Choose an explicit input policy:

- Interactive apps: register `set_user_input_handler(...)` or run a concurrent
  `user_input_requests()` response loop.
- Unattended apps: use `user_input_response_timeout=0.0` to send an automatic
  error promptly when no callback is registered. This does not restore the
  old `-32601` error code, disable question support, or itself cancel the turn.
- Manual UIs that intentionally wait indefinitely: use
  `user_input_response_timeout=None` and ensure a response loop is running.
  Configure or handle the separate conversation inactivity timeout as well.

The input timeout applies when no callback is registered; it does not limit
callback execution time. `approval_mode="manual"` does not configure this
timeout. See [human-in-the-loop handling](human-in-the-loop.md).

## Event routing and connection failures

Concurrent turns now receive only matching notifications, including events
that arrive before the turn-start response. The old shared queue could lose or
misassign these events. Applications inspecting `raw_events` must not depend
on the old event counts, accidental cross-turn events, or delivery timing.

If you implement a proxy or fake transport, provide standard event identifiers:

- The turn ID comes from `params.turnId`, `params.turn_id`, or
  `params.turn.id`; arbitrary nested item content is no longer searched for it.
- A provided `params.threadId` or `params.thread_id` must match the turn's
  thread.
- A terminal event without a turn ID is accepted only when one tracked turn
  matches its thread, or one tracked turn exists overall if no thread is given.
  Ambiguous terminal events do not complete arbitrary turns; waits can time out.

On connection failure or client close, all waiting conversation consumers now
wake with `CodexTransportError`. Code must handle the error for every concurrent
task. The old internal synthetic `__transport_error__` notification is no
longer queued; integrations must not depend on that private queue mechanism.

## Installation and model shape

- Importing the SDK now reads its installed distribution metadata to expose
  `__version__`. Copying only `src/codex_app_server_sdk` or adding it to
  `PYTHONPATH` without installing the distribution can raise
  `importlib.metadata.PackageNotFoundError`. Use `uv sync` in a checkout or
  `uv pip install -e .` for an editable install; normal wheel installs include
  the required metadata. Bundlers must preserve that metadata too.
- `TurnOverrides` has a new optional `collaboration_mode` field at the end,
  defaulting to `UNSET`. Existing constructor arguments still work, but
  `dataclasses.fields()`, `asdict()`, and `astuple()` expose the extra field.
  Update fixed-length unpacking and exact-schema snapshots if you use them.

Python still requires 3.12 or newer. Runtime dependency constraints, existing
public imports, and transport selection defaults are unchanged. New
collaboration and user-input types are additive; their server-side capabilities
depend on the Codex runtime you supply.

The new `approval_mode` and `user_input_response_timeout` constructor/factory
keywords require SDK 0.4.0 or newer. If you adopt them, raise your application's
minimum SDK dependency accordingly; 0.3.2 rejects these keywords.

## Upgrade validation

Before deploying, exercise your approval UI, failed/interrupted turn handling,
cancellation timeout recovery, and any unattended plan-mode flow. Update custom
transport fixtures for initialization and event identifiers.

The release has deterministic regression coverage and a live concurrency and
cancellation smoke test against Codex CLI 0.156.1 using `gpt-6-luna`. This is
not a compatibility matrix for every older Codex runtime or protocol feature;
test the runtime your application deploys.
