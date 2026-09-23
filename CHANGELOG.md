# Changelog

Version headings identify prepared releases. Publication dates and artifacts
are recorded in GitHub Releases and PyPI.

## 0.4.0

### Compatibility and required migration

**This release is not a drop-in behavior-compatible upgrade from 0.3.2.**
Read the [complete migration guide](https://emsi.github.io/codex-app-server-sdk/migration-0.4.0/)
before deploying an existing application. Existing public methods and imports
remain available, but callers may need the following changes:

- **Manual approvals:** set `approval_mode="manual"` when responding through
  `approval_requests()` without a callback. Previously, these responses raced
  automatic decline. The default still declines without a handler; a callback
  handles requests in either mode. Consuming the stream does not select manual
  mode. Late, duplicate, server-resolved, or mismatched approval responses now
  fail stricter pending-request checks.
- **Unsuccessful turns:** both conversation APIs raise `CodexProtocolError`
  for failed/interrupted terminal statuses, even after partial output. A stream
  can yield steps before raising; partial assistant text is not proof of success.
- **Cancellation errors and ownership:** `cancel()` propagates interrupt RPC,
  request-timeout, and transport failures. Missing terminal confirmation raises
  `CodexTurnInactiveError` with the original continuation and cursor. It rejects
  tokens for turns no longer retained by the same client, including repeated
  cancellation after cleanup. Retain tokens on errors, but do not assume a
  failed transport remains usable.
- **Cancellation results:** `was_interrupted` requires confirmed interruption;
  `was_completed` means successful completion. Both can be false for a failed
  terminal turn. An interrupt acknowledgement alone is not confirmation.
- **Low-level interruption:** pass `thread_id` to `interrupt_turn()` for turns
  the SDK does not track. Missing or conflicting thread IDs raise `ValueError`.
- **Initialization:** the first successful call is cached. Later `initialize()`
  calls return the same result and ignore new parameters/timeouts. Supply custom
  settings before implicit initialization. Custom servers and tests must accept
  the new `initialized` notification and the installed SDK version in default
  `clientInfo.version`, replacing the hardcoded `0.1.0`.
- **Unanswered user questions:** `item/tool/requestUserInput` now waits up to
  300 seconds without a callback or manual response, instead of immediately
  returning unsupported-method error `-32601`. The default 180-second turn
  inactivity timeout can fire first. Unattended clients can set
  `user_input_response_timeout=0.0` for a prompt error response (`-32000`, not
  the old code); manual UIs may use `None` with an active response loop.
- **Events and transports:** routing uses explicit turn/thread identifiers,
  not arbitrary nested content. Ambiguous terminal events no longer complete
  arbitrary turns. Raw event counts/timing can change; all waiting consumers
  now receive connection failures. Custom transports and fixtures may need
  updated envelopes and handshake expectations.
- **Installation and model shape:** package imports require installed
  distribution metadata; use `uv sync` or an editable/wheel install rather
  than copying source alone. `TurnOverrides` adds a field, affecting fixed
  dataclass snapshots and tuple unpacking. Python and runtime dependency
  requirements are unchanged.

### Added

- Collaboration mode configuration (`default` and `plan`) through turn overrides.
- Human input requests, typed answers, callbacks, and manual response streams.
- Explicit `approval_mode="manual"` for applications that collect approval
  decisions from a person or UI.
- Public `__version__`, read from installed package metadata and used in the
  app-server initialization handshake.
- Release validation shared with PR CI: Python 3.12–3.14 tests, changed-file
  quality checks, package checks, and an isolated wheel import.
- Automated GitHub Releases with changelog notes and the same artifacts as
  PyPI, with version/tag checks and recovery for interrupted uploads.

### Fixed

- Concurrent turns receive their own notifications, including events that
  arrive before the turn-start response. Connection failures wake all waiters.
- Concurrent first calls initialize the connection once and send `initialized`.
- Failed and interrupted turns raise `CodexProtocolError` from both conversation
  APIs, including when partial assistant text has already arrived.
- Interrupt requests include both thread and turn identifiers. Cancellation
  retains continuations on errors or timeouts and cleans up after confirmation.
- Approval responses reject duplicate or already-resolved requests.
- Update release tooling to validate current Hatchling's core metadata 2.5.
- Make basedpyright warnings non-fatal consistently in local and GitHub
  Actions checks; type errors remain a release gate.

### Validation

- Added deterministic regression tests and an opt-in live smoke test using
  only `gpt-6-luna`. Concurrent turns and cancellation were tested with Codex
  CLI 0.156.1; this does not imply complete coverage of its protocol.
