# codex-app-server-client

High-level async Python client for `codex app-server`.

It gives you a convenient conversation API over `stdio` or `websocket` without having to manage raw protocol events yourself.

## Highlights

- simple one-shot turns with `chat_once(...)`
- step-streaming turns with `chat(...)` (`thinking`, `exec`, `codex`, etc.), non-delta
- built-in thread/turn lifecycle handling
- inactivity timeout continuation for long-running turns
- turn cancellation with unread-step/event drain via `cancel(...)`
- optional low-level `request(...)` access when needed

## Install

```bash
uv sync
```

## Quick start

### Stdio

```python
import asyncio
from codex_app_server_client import CodexClient


async def main() -> None:
    async with CodexClient.connect_stdio() as client:
        result = await client.chat_once("Hello from Python")
        print(result.final_text)


asyncio.run(main())
```

By default, stdio transport runs:
- command: `codex app-server`

You can override via:
- `connect_stdio(command=[...])`
- environment variable: `CODEX_APP_SERVER_CMD`

### Websocket

```python
import asyncio
from codex_app_server_client import CodexClient


async def main() -> None:
    async with CodexClient.connect_websocket() as client:
        result = await client.chat_once("Hello over websocket")
        print(result.final_text)


asyncio.run(main())
```

Websocket defaults:
- URL: `CODEX_APP_SERVER_WS_URL` or `ws://127.0.0.1:8765`
- Bearer token: `CODEX_APP_SERVER_TOKEN` (optional)

## Continuation on inactivity timeout

Both high-level APIs support resuming the same running turn.

```python
import asyncio
from codex_app_server_client import CodexClient, CodexTurnInactiveError


async def main() -> None:
    async with CodexClient.connect_stdio(inactivity_timeout=120.0) as client:
        continuation = None
        while True:
            try:
                if continuation is None:
                    result = await client.chat_once("Do a longer task")
                else:
                    result = await client.chat_once(continuation=continuation)
                print(result.final_text)
                break
            except CodexTurnInactiveError as exc:
                continuation = exc.continuation


asyncio.run(main())
```

## Example clients

More complete examples are under `examples/`.

### Rich step-stream example (thinking/exec/codex blocks)

Recommended example for step-oriented API and continuation behavior.

Stdio:

```bash
uv run python examples/chat_steps_rich.py
```

Websocket:

```bash
uv run python examples/chat_steps_rich.py --transport websocket --url ws://127.0.0.1:8765
```

With extra payload summaries:

```bash
uv run python examples/chat_steps_rich.py --show-data
```

Cancel timed-out turns instead of auto-resume:

```bash
uv run python examples/chat_steps_rich.py --cancel-on-timeout
```

Common options:
- `--transport {stdio,websocket}`
- `--cmd "codex app-server"` (stdio mode)
- `--url ws://127.0.0.1:8765` (websocket mode)
- `--token "$CODEX_APP_SERVER_TOKEN"` (websocket mode)
- `--prompt "..."`
- `--user "..."`
- `--inactivity-timeout 120`
- `--show-data`
- `--cancel-on-timeout`

### Stdio example (multi-turn, one thread)

```bash
uv run python examples/chat_session_stdio.py
```

Custom command and prompts:

```bash
uv run python examples/chat_session_stdio.py \
  --cmd "codex app-server" \
  --prompt "First prompt" \
  --prompt "Second prompt"
```

### Websocket example (multi-turn, one thread)

```bash
uv run python examples/chat_session_websocket.py
```

With explicit endpoint/token:

```bash
uv run python examples/chat_session_websocket.py \
  --url ws://127.0.0.1:8765 \
  --token "$CODEX_APP_SERVER_TOKEN"
```

Or via environment:

```bash
export CODEX_APP_SERVER_WS_URL=ws://127.0.0.1:8765
export CODEX_APP_SERVER_TOKEN=your-token
uv run python examples/chat_session_websocket.py
```

## API reference (quick)

### `CodexClient` (`src/codex_app_server_client/client.py`)

- `connect_stdio(...)`: create a stdio-configured client (unstarted).
- `connect_websocket(...)`: create a websocket-configured client (unstarted).
- `start()`: connect transport and start receive loop (idempotent).
- `initialize(params=None, timeout=None)`: perform JSON-RPC initialize handshake.
- `request(method, params=None, timeout=None)`: low-level JSON-RPC request helper.
- `chat(text=None, thread_id=None, user=None, metadata=None, inactivity_timeout=None, continuation=None)`: async iterator yielding completed non-delta step blocks.
- `chat_once(text=None, thread_id=None, user=None, metadata=None, inactivity_timeout=None, continuation=None)`: send one user message and wait for completed turn.
- `cancel(continuation, timeout=None)`: interrupt running turn, return unread steps/events, and clean turn state.
- `interrupt_turn(turn_id, timeout=None)`: low-level turn interruption request.
- `close()`: cancel receive loop and close transport.

### `Transport` and implementations (`src/codex_app_server_client/transport.py`)

- `Transport.connect/send/recv/close`: abstract interface.
- `StdioTransport`: line-delimited JSON over subprocess stdin/stdout.
- `WebSocketTransport`: JSON messages over websocket frames.

### Data models (`src/codex_app_server_client/models.py`)

- `InitializeResult`: parsed initialize response (`protocol_version`, `server_info`, `capabilities`, `raw`).
- `ConversationStep`: completed step from `chat(...)` (`step_type`, `item_type`, `text`, `item_id`, `thread_id`, `turn_id`, `data`).
- `ChatResult`: buffered turn output (`thread_id`, `turn_id`, `final_text`, `raw_events`, `assistant_item_id`, `completion_source`).
- `ChatContinuation`: continuation token for timed-out running turns (`thread_id`, `turn_id`, `cursor`, `mode`).
- `CancelResult`: cancellation result with unread `steps`/`raw_events` plus terminal flags.

### Exceptions (`src/codex_app_server_client/errors.py`)

- `CodexError`: base exception.
- `CodexTransportError`: transport/connectivity problems.
- `CodexTimeoutError`: request timeout (and base for timeout-related flow).
- `CodexTurnInactiveError`: per-turn inactivity timeout with resumable `continuation`.
- `CodexProtocolError`: protocol/JSON-RPC error (optional `code` and `data`).

## Behavior notes

- This version does not expose token-delta streaming as a public API.
- `chat(...)` provides async streaming of completed step blocks (non-delta).
- `chat_once(...)` resolves final text from completed `agentMessage` items (`item/completed`), with `thread/read(includeTurns=true)` fallback.
- `turn_timeout` is intentionally removed to avoid conflicting timeout semantics.
- Turn waits are controlled by `inactivity_timeout` (or unbounded when `None`).
- `cancel(...)` interrupts a continuation turn, returns unread buffered data, and cleans internal session state so the same thread can be reused safely.
- preferred lifecycle is `async with CodexClient.connect_*() as client:`; manual `start()/close()` remains available for advanced control.
- The client uses modern thread/turn methods (`thread/start`, `thread/resume`, `turn/start`, `turn/interrupt`).
- `initialize` currently sends `protocolVersion: "1"` as handshake metadata.
- Websocket transport targets `websockets` (`>=16,<17`), uses `additional_headers`, and disables compression by default (`compression=None`) for codex app-server compatibility.
- After dependency changes, run `uv sync` to refresh the virtual environment.
