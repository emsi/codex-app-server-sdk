# codex-app-server-sdk

Async Python client library for `codex app-server` over `stdio` and `websocket`.

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
