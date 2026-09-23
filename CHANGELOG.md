# Changelog

Version headings identify prepared releases. Publication dates and artifacts
are recorded in GitHub Releases and PyPI.

## 0.4.0

### Added

- Collaboration mode configuration (`default` and `plan`) through turn overrides.
- Human input requests, typed answers, callbacks, and manual response streams.
- Explicit `approval_mode="manual"` for applications that collect approval
  decisions from a person or UI.
- Public `__version__`, read from installed package metadata and used in the
  app-server initialization handshake.

### Fixed

- Concurrent turns receive their own notifications, including events that
  arrive before the turn-start response. Connection failures wake all waiters.
- Concurrent first calls initialize the connection once and send `initialized`.
- Failed and interrupted turns raise `CodexProtocolError` from both conversation
  APIs, including when partial assistant text has already arrived.
- Interrupt requests include both thread and turn identifiers. Cancellation
  retains continuations on errors or timeouts and cleans up after confirmation.
- Approval responses reject duplicate or already-resolved requests.

### Migration notes

- Set `approval_mode="manual"` when responding through `approval_requests()`
  without a callback. The default still declines requests without a handler.
- Catch `CodexProtocolError` for failed or interrupted turns; they no longer
  appear to succeed with partial output.
- `cancel()` can now raise an RPC error or `CodexTurnInactiveError`. Retain the
  continuation for another wait or cancellation attempt.
- `CancelResult.was_interrupted` means confirmed interruption, and
  `was_completed` means observed successful completion.
- Pass `thread_id` to `interrupt_turn()` for turns the SDK is not tracking.

### Validation

- Added deterministic regression tests and an opt-in live smoke test using
  only `gpt-6-luna`. Concurrent turns and cancellation were tested with Codex
  CLI 0.156.1; this does not imply complete coverage of its protocol.
