# codex-app-server-sdk

Async Python client library for `codex app-server` over `stdio` and `websocket`.

This documentation is organized around:

- task-oriented guides (`getting started`, `conversation`, `threads/config`)
- operational behavior (`timeouts`, `continuation`, `cancel`, guarantees)
- complete API reference generated from source docstrings

## Quick links

- Start here: [Getting started](getting-started.md)
- Streaming semantics: [Conversation APIs](conversation.md)
- Long-running turn control: [Timeouts, continuation, cancel](timeouts-continuation-cancel.md)
- Thread/model/config scope: [Threads and configuration](threads-and-config.md)
- Ready-to-run scripts: [Examples](examples.md)
- Method-level mapping: [Protocol mapping](protocol-mapping.md)
- Generated reference: [API reference](api/index.md)

## Install

```bash
uv sync
```

## Local docs workflow

```bash
uv run mkdocs serve
```

```bash
uv run mkdocs build --strict
```
