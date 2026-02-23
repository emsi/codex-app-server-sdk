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

## Basic usage (stdio)

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

## Basic usage (websocket)

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

## Notes

- This version does not expose a streaming event API.
- `chat_once` buffers notifications and returns final assistant text on turn completion.
