import pytest

from codex_app_server_client.errors import CodexTransportError
from codex_app_server_client.transport import StdioTransport, WebSocketTransport


def test_stdio_transport_requires_command() -> None:
    with pytest.raises(ValueError):
        StdioTransport([])


def test_websocket_transport_send_requires_connection() -> None:
    async def _run() -> None:
        transport = WebSocketTransport("ws://127.0.0.1:9999")
        with pytest.raises(CodexTransportError):
            await transport.send({"jsonrpc": "2.0", "id": 1, "method": "ping"})

    import asyncio

    asyncio.run(_run())
