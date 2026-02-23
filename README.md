# codex-app-server-client

Async Python client library for Codex app-server using either `stdio` or `websocket` transport.

Current scope is intentionally small:
- JSON-RPC request/response handling
- `initialize`
- basic thread + turn chat flow
- high-level non-streaming `chat_once(...)`

## Install

```bash
uv sync
```

## Basic usage

### Stdio

```python
import asyncio
from codex_app_server_client import CodexClient


async def main() -> None:
    client = await CodexClient.connect_stdio()
    try:
        result = await client.chat_once("Hello from Python")
        print(result.final_text)
    finally:
        await client.close()


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
    client = await CodexClient.connect_websocket()
    try:
        result = await client.chat_once("Hello over websocket")
        print(result.final_text)
    finally:
        await client.close()


asyncio.run(main())
```

Websocket defaults:
- URL: `CODEX_APP_SERVER_WS_URL` or `ws://127.0.0.1:8765`
- Bearer token: `CODEX_APP_SERVER_TOKEN` (optional)

## Example clients

More complete examples are included under `examples/`.

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

- `connect_stdio(...)`: create + connect client over subprocess stdio.
- `connect_websocket(...)`: create + connect client over websocket.
- `start()`: connect transport and start receive loop.
- `initialize(params=None, timeout=None)`: perform JSON-RPC initialize handshake.
- `request(method, params=None, timeout=None)`: low-level JSON-RPC request helper.
- `chat_once(text, thread_id=None, user=None, metadata=None, timeout=None)`: send one user message and wait for completed turn.
- `interrupt_turn(turn_id, timeout=None)`: send turn interruption request.
- `close()`: cancel receive loop and close transport.

### `Transport` and implementations (`src/codex_app_server_client/transport.py`)

- `Transport.connect/send/recv/close`: abstract interface.
- `StdioTransport`: line-delimited JSON over subprocess stdin/stdout.
- `WebSocketTransport`: JSON messages over websocket frames.

### Data models (`src/codex_app_server_client/models.py`)

- `InitializeResult`: parsed initialize response (`protocol_version`, `server_info`, `capabilities`, `raw`).
- `ChatResult`: buffered turn output (`thread_id`, `turn_id`, `final_text`, `raw_events`, `assistant_item_id`, `completion_source`).

### Exceptions (`src/codex_app_server_client/errors.py`)

- `CodexError`: base exception.
- `CodexTransportError`: transport/connectivity problems.
- `CodexTimeoutError`: request or turn timeout.
- `CodexProtocolError`: protocol/JSON-RPC error (includes optional `code` and `data`).

## Behavior notes

- This version does not expose a streaming event API.
- `chat_once` buffers notifications and resolves final assistant text from completed `agentMessage` items (`item/completed`), with a `thread/read(includeTurns=true)` fallback.
- `chat_once` does not rely on delta-string concatenation heuristics for final output.
- The client uses modern thread/turn methods (`thread/start`, `thread/resume`, `turn/start`, `turn/interrupt`).
- `initialize` currently sends `protocolVersion: "1"` as handshake metadata.
- Websocket transport targets modern `websockets` (`>=16,<17`), uses the `additional_headers` API, and disables compression by default (`compression=None`) for codex app-server compatibility.
- After dependency changes, run `uv sync` to refresh the virtual environment.
