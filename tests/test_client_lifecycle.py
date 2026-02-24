from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import codex_app_server_client.client as client_module
from codex_app_server_client.client import CodexClient
from codex_app_server_client.transport import Transport


class CountingTransport(Transport):
    def __init__(self) -> None:
        self.connect_calls = 0
        self.close_calls = 0
        self._incoming: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def connect(self) -> None:
        self.connect_calls += 1

    async def send(self, payload: Mapping[str, Any]) -> None:
        return None

    async def recv(self) -> dict[str, Any]:
        return await self._incoming.get()

    async def close(self) -> None:
        self.close_calls += 1


class FakeStdioTransport(Transport):
    last_instance: FakeStdioTransport | None = None

    def __init__(self, *args: object, **kwargs: object) -> None:
        self.connect_calls = 0
        self.close_calls = 0
        self._incoming: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        FakeStdioTransport.last_instance = self

    async def connect(self) -> None:
        self.connect_calls += 1

    async def send(self, payload: Mapping[str, Any]) -> None:
        return None

    async def recv(self) -> dict[str, Any]:
        return await self._incoming.get()

    async def close(self) -> None:
        self.close_calls += 1


def test_start_is_idempotent() -> None:
    async def _run() -> None:
        transport = CountingTransport()
        client = CodexClient(transport)
        await client.start()
        await client.start()
        assert transport.connect_calls == 1
        await client.close()
        await client.close()
        assert transport.close_calls == 1

    asyncio.run(_run())


def test_async_with_started_client_does_not_reconnect() -> None:
    async def _run() -> None:
        transport = CountingTransport()
        client = CodexClient(transport)
        await client.start()
        assert transport.connect_calls == 1

        async with client as managed:
            assert managed is client

        assert transport.connect_calls == 1
        assert transport.close_calls == 1

    asyncio.run(_run())


def test_connect_stdio_returns_unstarted_client_for_async_with(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(client_module, "StdioTransport", FakeStdioTransport)

    async def _run() -> None:
        client = CodexClient.connect_stdio(command=["codex", "app-server"])
        assert isinstance(client, CodexClient)

        transport = FakeStdioTransport.last_instance
        assert transport is not None
        assert transport.connect_calls == 0

        async with client:
            pass

        assert transport.connect_calls == 1
        assert transport.close_calls == 1

    asyncio.run(_run())
