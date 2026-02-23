from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from codex_app_server_client.client import CodexClient
from codex_app_server_client.errors import CodexTurnInactiveError
from codex_app_server_client.models import ChatContinuation
from codex_app_server_client.transport import Transport


class DelayedCompletionTransport(Transport):
    def __init__(self, *, delay: float = 0.05) -> None:
        self._incoming: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._delay = delay
        self._tasks: set[asyncio.Task[None]] = set()

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

        if method == "thread/resume":
            await self._incoming.put({"jsonrpc": "2.0", "id": request_id, "result": {}})
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
                            "summary": ["Preparing response"],
                        },
                    },
                }
            )

            async def _finish() -> None:
                await asyncio.sleep(self._delay)
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
                                "command": "git status --short",
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
                                "text": "Final answer",
                            },
                        },
                    }
                )
                await self._incoming.put(
                    {
                        "jsonrpc": "2.0",
                        "method": "turn/completed",
                        "params": {"threadId": "thread-1", "turnId": "turn-1"},
                    }
                )

            task = asyncio.create_task(_finish())
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            return

        if method == "thread/read":
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
                                    "items": [
                                        {
                                            "id": "msg-1",
                                            "type": "agentMessage",
                                            "text": "Final answer",
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
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)


class CancelTransport(Transport):
    def __init__(self) -> None:
        self._incoming: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._turn_count = 0

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

        if method == "thread/resume":
            await self._incoming.put({"jsonrpc": "2.0", "id": request_id, "result": {}})
            return

        if method == "turn/start":
            self._turn_count += 1
            turn_id = f"turn-{self._turn_count}"
            await self._incoming.put(
                {"jsonrpc": "2.0", "id": request_id, "result": {"turnId": turn_id}}
            )

            if turn_id == "turn-1":
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
                                "summary": ["Long-running step"],
                            },
                        },
                    }
                )
                return

            await self._incoming.put(
                {
                    "jsonrpc": "2.0",
                    "method": "item/completed",
                    "params": {
                        "threadId": "thread-1",
                        "turnId": turn_id,
                        "item": {
                            "id": f"msg-{turn_id}",
                            "type": "agentMessage",
                            "text": "second turn ok",
                        },
                    },
                }
            )
            await self._incoming.put(
                {
                    "jsonrpc": "2.0",
                    "method": "turn/completed",
                    "params": {"threadId": "thread-1", "turnId": turn_id},
                }
            )
            return

        if method == "turn/interrupt":
            await self._incoming.put({"jsonrpc": "2.0", "id": request_id, "result": {}})
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
                            "command": "echo interrupted",
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
                            "text": "turn interrupted",
                        },
                    },
                }
            )
            await self._incoming.put(
                {
                    "jsonrpc": "2.0",
                    "method": "turn/completed",
                    "params": {"threadId": "thread-1", "turnId": "turn-1"},
                }
            )
            return

        if method == "thread/read":
            await self._incoming.put(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "thread": {
                            "id": "thread-1",
                            "turns": [],
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


def test_chat_once_timeout_can_resume_with_continuation() -> None:
    async def _run() -> None:
        client = await CodexClient(
            DelayedCompletionTransport(delay=0.05),
            request_timeout=1.0,
            inactivity_timeout=0.01,
        ).start()

        try:
            continuation: ChatContinuation | None = None
            try:
                await client.chat_once("Hello")
            except CodexTurnInactiveError as exc:
                continuation = exc.continuation

            assert continuation is not None
            assert continuation.mode == "once"

            result = await client.chat_once(continuation=continuation, inactivity_timeout=1.0)
            assert result.final_text == "Final answer"
            assert result.turn_id == "turn-1"
        finally:
            await client.close()

    asyncio.run(_run())


def test_chat_stream_timeout_can_resume_without_duplicates() -> None:
    async def _run() -> None:
        client = await CodexClient(
            DelayedCompletionTransport(delay=0.05),
            request_timeout=1.0,
            inactivity_timeout=0.01,
        ).start()

        try:
            continuation: ChatContinuation | None = None
            seen_step_types: list[str] = []

            try:
                async for step in client.chat("Hello"):
                    seen_step_types.append(step.step_type)
            except CodexTurnInactiveError as exc:
                continuation = exc.continuation

            assert continuation is not None
            assert continuation.mode == "stream"

            async for step in client.chat(continuation=continuation, inactivity_timeout=1.0):
                seen_step_types.append(step.step_type)

            assert seen_step_types == ["thinking", "exec", "codex"]
        finally:
            await client.close()

    asyncio.run(_run())


def test_cancel_returns_unread_and_allows_same_thread_reuse() -> None:
    async def _run() -> None:
        client = await CodexClient(
            CancelTransport(),
            request_timeout=1.0,
            inactivity_timeout=0.01,
        ).start()

        try:
            continuation: ChatContinuation | None = None
            thread_id: str | None = None

            try:
                async for step in client.chat("first"):
                    thread_id = step.thread_id
            except CodexTurnInactiveError as exc:
                continuation = exc.continuation
                thread_id = exc.continuation.thread_id

            assert continuation is not None
            assert thread_id == "thread-1"

            cancel_result = await client.cancel(continuation)
            assert cancel_result.was_interrupted is True
            assert [step.step_type for step in cancel_result.steps] == ["exec", "codex"]
            assert cancel_result.turn_id == "turn-1"

            result = await client.chat_once("second", thread_id=thread_id)
            assert result.final_text == "second turn ok"
            assert result.thread_id == "thread-1"
        finally:
            await client.close()

    asyncio.run(_run())
