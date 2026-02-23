from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from codex_app_server_client.client import CodexClient
from codex_app_server_client.transport import Transport


class CompletedItemTransport(Transport):
    def __init__(
        self,
        *,
        emit_item_completed: bool,
        completed_text: str,
        fallback_text: str | None = None,
    ) -> None:
        self._incoming: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._emit_item_completed = emit_item_completed
        self._completed_text = completed_text
        self._fallback_text = fallback_text

    async def connect(self) -> None:
        return None

    async def send(self, payload: Mapping[str, Any]) -> None:
        message = dict(payload)
        method = message.get("method")
        request_id = message.get("id")

        if method == "initialize":
            await self._incoming.put(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"protocolVersion": "1"},
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
                    "method": "item/agentMessage/delta",
                    "params": {"threadId": "thread-1", "turnId": "turn-1", "delta": "a"},
                }
            )
            await self._incoming.put(
                {
                    "jsonrpc": "2.0",
                    "method": "item/agentMessage/delta",
                    "params": {"threadId": "thread-1", "turnId": "turn-1", "delta": " a"},
                }
            )
            await self._incoming.put(
                {
                    "jsonrpc": "2.0",
                    "method": "item/agentMessage/delta",
                    "params": {"threadId": "thread-1", "turnId": "turn-1", "delta": " a"},
                }
            )
            if self._emit_item_completed:
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
                                "text": self._completed_text,
                            },
                        },
                    }
                )
            await self._incoming.put(
                {
                    "jsonrpc": "2.0",
                    "method": "turn/completed",
                    "params": {
                        "threadId": "thread-1",
                        "turn": {"id": "turn-1", "status": "completed", "items": []},
                    },
                }
            )
            return

        if method == "thread/read":
            text = self._fallback_text or ""
            await self._incoming.put(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "thread": {
                            "id": "thread-1",
                            "turns": [
                                {
                                    "id": "turn-1",
                                    "status": "completed",
                                    "items": [
                                        {
                                            "id": "msg-fallback",
                                            "type": "agentMessage",
                                            "text": text,
                                        }
                                    ],
                                }
                            ],
                        }
                    },
                }
            )
            return

        await self._incoming.put({"jsonrpc": "2.0", "id": request_id, "result": {}})

    async def recv(self) -> dict[str, Any]:
        return await self._incoming.get()

    async def close(self) -> None:
        return None


def test_chat_once_uses_completed_item_text_exactly() -> None:
    async def _run() -> None:
        client = await CodexClient(
            CompletedItemTransport(
                emit_item_completed=True,
                completed_text="a a a",
            )
        ).start()
        try:
            result = await client.chat_once("repeat")
        finally:
            await client.close()

        assert result.final_text == "a a a"
        assert result.completion_source == "item_completed"
        assert result.assistant_item_id == "msg-1"

    asyncio.run(_run())


def test_chat_once_falls_back_to_thread_read_when_item_completed_missing() -> None:
    async def _run() -> None:
        client = await CodexClient(
            CompletedItemTransport(
                emit_item_completed=False,
                completed_text="ignored",
                fallback_text="1. alpha\n\n2. beta",
            )
        ).start()
        try:
            result = await client.chat_once("fallback")
        finally:
            await client.close()

        assert result.final_text == "1. alpha\n\n2. beta"
        assert result.completion_source == "thread_read_fallback"
        assert result.assistant_item_id == "msg-fallback"

    asyncio.run(_run())
