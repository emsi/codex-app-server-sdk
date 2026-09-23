# codex-app-server-sdk

Async Python client library for `codex app-server` over `stdio` and `websocket`.

## Why use this client?

Use Codex directly from your application's `asyncio` event loop, choose or
replace the transport, and control the Codex runtime independently. The
conversation API adds completed-step streaming, resumable inactivity timeouts,
and cancellation with unread-event recovery.

Both this library and the official `openai-codex` Python SDK use the Codex
app-server protocol, with different integration choices:

| Area | `codex-app-server-sdk` | `openai-codex` |
| --- | --- | --- |
| Execution | Native `asyncio` client; asynchronous protocol I/O on your event loop | Synchronous client with an async wrapper that offloads blocking operations to background threads |
| Transport | Public, replaceable `Transport`; built-in stdio and WebSocket support | SDK-managed subprocess communicating through line-delimited JSON over stdio |
| Protocol access | Flexible dictionary-based RPC via `request(...)`, plus high-level conversation models | Extensive generated types, typed responses, and typed notifications |
| Runtime management | You supply and manage Codex; choose an executable or connect to an existing WebSocket server | Installs an exactly pinned Codex runtime dependency by default |

This client's native async I/O and transport flexibility suit applications with
their own runtime or connection management. The official SDK offers broader
generated type coverage and a reproducible runtime default. You are responsible
for installing and updating Codex when using this client.

Comparison verified against the published
[`openai-codex` 0.156.1](https://pypi.org/project/openai-codex/0.156.1/)
source and package metadata on 2026-09-23. The official SDK also supports a
[`CodexConfig(codex_bin=...)` override](https://learn.chatgpt.com/docs/codex-sdk)
for selecting a different local executable.

## Documentation

This documentation is organized around:

- task-oriented guides (`getting started`, `conversation`, `threads/config`)
- operational behavior (`timeouts`, `continuation`, `cancel`, guarantees)
- complete API reference generated from source docstrings

## Quick links

- Start here: [Getting started](getting-started.md)
- Streaming semantics: [Conversation APIs](conversation.md)
- Plan mode and user confirmation: [Human-in-the-loop](human-in-the-loop.md)
- Long-running turn control: [Timeouts, continuation, cancel](timeouts-continuation-cancel.md)
- Thread/model/config scope: [Threads and configuration](threads-and-config.md)
- Ready-to-run scripts: [Examples](examples.md)
- Method-level mapping: [Protocol mapping](protocol-mapping.md)
- Generated reference: [API reference](api/index.md)

## Install

Install `uv` (if needed):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Install the package from PyPI:

```bash
uv add codex-app-server-sdk
```

Or pip-compatible install in the active environment:

```bash
uv pip install codex-app-server-sdk
```

## Contributor docs workflow

For versioning, package validation, and publishing, see the
[release procedure](releasing.md).

Install development dependencies:

```bash
uv sync --group dev
```

Serve docs locally:

```bash
uv run zensical serve
```

Build docs:

```bash
uv run zensical build
```
