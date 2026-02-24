# Getting started

## Requirements

- Python `>=3.12`
- `uv`
- `codex app-server` available in `PATH` (for stdio mode)

## Install

```bash
uv sync
```

## Minimal stdio example

```python
import asyncio
from codex_app_server_client import CodexClient


async def main() -> None:
    async with CodexClient.connect_stdio() as client:
        result = await client.chat_once("Hello from Python")
        print(result.final_text)


asyncio.run(main())
```

## Minimal websocket example

```python
import asyncio
from codex_app_server_client import CodexClient


async def main() -> None:
    async with CodexClient.connect_websocket(
        url="ws://127.0.0.1:8765",
    ) as client:
        result = await client.chat_once("Hello over websocket")
        print(result.final_text)


asyncio.run(main())
```

## Explicit initialize handshake

`chat_once(...)` and `chat(...)` initialize automatically, but you can initialize
early to fail fast or inspect server metadata.

```python
init = await client.initialize()
print(init.protocol_version, init.server_info)
```
