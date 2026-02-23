import asyncio
from types import SimpleNamespace

import pytest

from codex_app_server_client.errors import CodexTransportError
from codex_app_server_client.transport import StdioTransport, WebSocketTransport
import codex_app_server_client.transport as transport_module


def test_stdio_transport_requires_command() -> None:
    with pytest.raises(ValueError):
        StdioTransport([])


def test_websocket_transport_send_requires_connection() -> None:
    async def _run() -> None:
        transport = WebSocketTransport("ws://127.0.0.1:9999")
        with pytest.raises(CodexTransportError):
            await transport.send({"jsonrpc": "2.0", "id": 1, "method": "ping"})

    asyncio.run(_run())


def test_websocket_transport_connect_uses_additional_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_connect(uri: str, **kwargs: object) -> object:
        captured["uri"] = uri
        captured["kwargs"] = kwargs

        class DummySocket:
            async def close(self) -> None:
                return None

        return DummySocket()

    monkeypatch.setattr(
        transport_module,
        "websockets",
        SimpleNamespace(connect=fake_connect),
    )

    async def _run() -> None:
        transport = WebSocketTransport(
            "ws://127.0.0.1:8765",
            headers={"Authorization": "Bearer token"},
        )
        await transport.connect()
        await transport.close()

    asyncio.run(_run())
    assert captured["uri"] == "ws://127.0.0.1:8765"
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["additional_headers"] == {"Authorization": "Bearer token"}
    assert kwargs["compression"] is None


def test_websocket_transport_connect_wraps_original_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_connect(uri: str, **kwargs: object) -> object:
        raise RuntimeError("boom")

    monkeypatch.setattr(
        transport_module,
        "websockets",
        SimpleNamespace(connect=fake_connect),
    )

    async def _run() -> None:
        transport = WebSocketTransport("ws://127.0.0.1:8765")
        with pytest.raises(CodexTransportError) as exc_info:
            await transport.connect()
        message = str(exc_info.value)
        assert "RuntimeError" in message
        assert "boom" in message

    asyncio.run(_run())
