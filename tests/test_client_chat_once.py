from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from codex_app_server_client.client import CodexClient
from codex_app_server_client.transport import Transport


class FakeTransport(Transport):
    def __init__(self) -> None:
        self._incoming: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.sent: list[dict[str, Any]] = []
        self.connected = False

    async def connect(self) -> None:
        self.connected = True

    async def send(self, payload: Mapping[str, Any]) -> None:
        message = dict(payload)
        self.sent.append(message)
        method = message.get("method")
        request_id = message.get("id")

        if method == "initialize":
            await self._incoming.put(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "protocolVersion": "1",
                        "serverInfo": {"name": "fake-server"},
                        "capabilities": {},
                    },
                }
            )
            return

        if method == "thread/start":
            await self._incoming.put(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"threadId": "thread-1"},
                }
            )
            return

        if method == "turn/start":
            await self._incoming.put(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"turnId": "turn-1"},
                }
            )
            await self._incoming.put(
                {
                    "jsonrpc": "2.0",
                    "method": "item/completed",
                    "params": {
                        "threadId": "thread-1",
                        "turnId": "turn-1",
                        "item": {
                            "id": "msg-1",
                            "type": "agentMessage",
                            "text": "Hello world",
                        },
                    },
                }
            )
            await self._incoming.put(
                {
                    "jsonrpc": "2.0",
                    "method": "turn/completed",
                    "params": {"turnId": "turn-1"},
                }
            )
            return

        await self._incoming.put(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {},
            }
        )

    async def recv(self) -> dict[str, Any]:
        return await self._incoming.get()

    async def close(self) -> None:
        self.connected = False


def test_chat_once_collects_full_text() -> None:
    async def _run() -> None:
        transport = FakeTransport()
        client = await CodexClient(
            transport,
            request_timeout=1.0,
            inactivity_timeout=1.0,
        ).start()

        try:
            result = await client.chat_once("Hi")
        finally:
            await client.close()

        assert transport.sent[0]["method"] == "initialize"
        init_params = transport.sent[0].get("params")
        assert isinstance(init_params, dict)
        capabilities = init_params.get("capabilities")
        assert isinstance(capabilities, dict)
        opt_out = capabilities.get("optOutNotificationMethods")
        assert isinstance(opt_out, list)
        assert "codex/event/agent_message_content_delta" in opt_out

        assert result.thread_id == "thread-1"
        assert result.turn_id == "turn-1"
        assert result.final_text == "Hello world"

    asyncio.run(_run())
