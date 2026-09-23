from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from codex_app_server_sdk import (
    CodexClient,
    CollaborationMode,
    CollaborationSettings,
    TurnOverrides,
)
from codex_app_server_sdk.transport import Transport


class CollaborationTransport(Transport):
    def __init__(self) -> None:
        self._incoming: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.sent: list[dict[str, Any]] = []

    async def connect(self) -> None:
        return None

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
                    "result": {"protocolVersion": "2", "serverInfo": {"name": "fake"}},
                }
            )
            return

        if method == "thread/start":
            await self._incoming.put(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"threadId": "thread-collab"},
                }
            )
            return

        if method == "turn/start":
            await self._incoming.put(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"turnId": "turn-collab"},
                }
            )
            await self._incoming.put(
                {
                    "jsonrpc": "2.0",
                    "method": "item/completed",
                    "params": {
                        "threadId": "thread-collab",
                        "turnId": "turn-collab",
                        "item": {
                            "id": "msg-1",
                            "type": "agentMessage",
                            "text": "ok",
                        },
                    },
                }
            )
            await self._incoming.put(
                {
                    "jsonrpc": "2.0",
                    "method": "turn/completed",
                    "params": {"turnId": "turn-collab"},
                }
            )
            return

        await self._incoming.put({"jsonrpc": "2.0", "id": request_id, "result": {}})

    async def recv(self) -> dict[str, Any]:
        return await self._incoming.get()

    async def close(self) -> None:
        return None


def test_chat_once_forwards_collaboration_mode_from_turn_overrides() -> None:
    async def _run() -> None:
        transport = CollaborationTransport()
        client = await CodexClient(transport, request_timeout=1.0).start()
        try:
            result = await client.chat_once(
                "Plan this task.",
                turn_overrides=TurnOverrides(
                    collaboration_mode=CollaborationMode(
                        mode="plan",
                        settings=CollaborationSettings(
                            model="gpt-5.3-codex",
                            reasoning_effort="high",
                            developer_instructions=None,
                        ),
                    )
                ),
            )
            assert result.final_text == "ok"
        finally:
            await client.close()

        turn_start_messages = [
            message
            for message in transport.sent
            if message.get("method") == "turn/start"
        ]
        assert turn_start_messages, "turn/start was not sent"
        params = turn_start_messages[-1].get("params")
        assert isinstance(params, dict)
        collab = params.get("collaborationMode")
        assert isinstance(collab, dict)
        assert collab["mode"] == "plan"
        settings = collab.get("settings")
        assert isinstance(settings, dict)
        assert settings["model"] == "gpt-5.3-codex"
        assert settings["reasoning_effort"] == "high"

    asyncio.run(_run())
