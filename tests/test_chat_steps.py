from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from codex_app_server_client.client import CodexClient
from codex_app_server_client.transport import Transport


class StepTransport(Transport):
    def __init__(self) -> None:
        self._incoming: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def connect(self) -> None:
        return None

    async def send(self, payload: Mapping[str, Any]) -> None:
        message = dict(payload)
        method = message.get("method")
        request_id = message.get("id")

        if method == "initialize":
            await self._incoming.put(
                {"jsonrpc": "2.0", "id": request_id, "result": {"protocolVersion": "1"}}
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
                {"jsonrpc": "2.0", "id": request_id, "result": {"turnId": "turn-1"}}
            )
            await self._incoming.put(
                {
                    "jsonrpc": "2.0",
                    "method": "item/completed",
                    "params": {
                        "threadId": "thread-1",
                        "turnId": "turn-1",
                        "item": {
                            "id": "reason-1",
                            "type": "reasoning",
                            "summary": ["Inspecting repository changes"],
                            "content": ["Drafting a clear commit message"],
                        },
                    },
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
                            "id": "cmd-1",
                            "type": "commandExecution",
                            "command": "git diff --cached",
                            "status": "completed",
                            "cwd": "/tmp/x",
                            "commandActions": [],
                        },
                    },
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
                            "text": "Commit message: feat: improve transport behavior",
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

        await self._incoming.put({"jsonrpc": "2.0", "id": request_id, "result": {}})

    async def recv(self) -> dict[str, Any]:
        return await self._incoming.get()

    async def close(self) -> None:
        return None


def test_chat_yields_completed_steps_non_delta() -> None:
    async def _run() -> None:
        client = await CodexClient(
            StepTransport(),
            request_timeout=1.0,
            inactivity_timeout=1.0,
        ).start()
        try:
            seen = []
            async for step in client.chat("hi"):
                seen.append(step)
        finally:
            await client.close()

        assert [s.step_type for s in seen] == ["thinking", "exec", "codex"]
        assert seen[0].text == "Inspecting repository changes\nDrafting a clear commit message"
        assert seen[1].text == "git diff --cached"
        assert seen[2].text == "Commit message: feat: improve transport behavior"

    asyncio.run(_run())
