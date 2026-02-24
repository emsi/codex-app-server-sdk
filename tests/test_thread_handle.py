from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from codex_app_server_client.client import CodexClient
from codex_app_server_client.models import ThreadConfig, TurnOverrides
from codex_app_server_client.transport import Transport


class ThreadApiTransport(Transport):
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

        if method == "thread/resume":
            await self._incoming.put(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"threadId": "thread-1"},
                }
            )
            return

        if method == "thread/fork":
            await self._incoming.put(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"threadId": "thread-2"},
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
                            "text": "hello",
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
        return None


def _request_for_method(
    sent: list[dict[str, Any]],
    method_name: str,
) -> dict[str, Any]:
    for message in sent:
        if message.get("method") == method_name:
            return message
    raise AssertionError(f"method not found in sent requests: {method_name!r}")


def test_start_thread_and_handle_chat_once_support_config_and_turn_overrides() -> None:
    async def _run() -> None:
        client = await CodexClient(
            ThreadApiTransport(),
            request_timeout=1.0,
            inactivity_timeout=1.0,
        ).start()

        try:
            handle = await client.start_thread(
                ThreadConfig(
                    cwd="/tmp/project",
                    base_instructions="base",
                    developer_instructions="dev",
                    model="gpt-5",
                )
            )
            result = await handle.chat_once(
                "hello",
                turn_overrides=TurnOverrides(
                    effort="low",
                    model="gpt-5-mini",
                    cwd="/tmp/project/subdir",
                ),
            )
        finally:
            transport = client._transport
            await client.close()

        assert result.thread_id == "thread-1"
        assert result.final_text == "hello"

        assert isinstance(transport, ThreadApiTransport)
        start_request = _request_for_method(transport.sent, "thread/start")
        start_params = start_request.get("params")
        assert isinstance(start_params, dict)
        assert start_params.get("cwd") == "/tmp/project"
        assert start_params.get("baseInstructions") == "base"
        assert start_params.get("developerInstructions") == "dev"
        assert start_params.get("model") == "gpt-5"

        turn_request = _request_for_method(transport.sent, "turn/start")
        turn_params = turn_request.get("params")
        assert isinstance(turn_params, dict)
        assert turn_params.get("cwd") == "/tmp/project/subdir"
        assert turn_params.get("model") == "gpt-5-mini"
        assert turn_params.get("effort") == "low"

    asyncio.run(_run())


def test_fork_thread_and_update_defaults_use_thread_methods() -> None:
    async def _run() -> None:
        client = await CodexClient(
            ThreadApiTransport(),
            request_timeout=1.0,
            inactivity_timeout=1.0,
        ).start()

        try:
            forked = await client.fork_thread(
                "thread-1",
                overrides=ThreadConfig(
                    cwd="/tmp/fork",
                    model="gpt-5-mini",
                ),
            )
            assert forked.thread_id == "thread-2"

            handle = await client.resume_thread("thread-1")
            await handle.update_defaults(
                ThreadConfig(
                    developer_instructions="updated",
                    model="gpt-5",
                )
            )
        finally:
            transport = client._transport
            await client.close()

        assert isinstance(transport, ThreadApiTransport)

        fork_request = _request_for_method(transport.sent, "thread/fork")
        fork_params = fork_request.get("params")
        assert isinstance(fork_params, dict)
        assert fork_params.get("threadId") == "thread-1"
        assert fork_params.get("cwd") == "/tmp/fork"
        assert fork_params.get("model") == "gpt-5-mini"

        resume_requests = [m for m in transport.sent if m.get("method") == "thread/resume"]
        assert len(resume_requests) >= 2
        update_params = resume_requests[-1].get("params")
        assert isinstance(update_params, dict)
        assert update_params.get("threadId") == "thread-1"
        assert update_params.get("developerInstructions") == "updated"
        assert update_params.get("model") == "gpt-5"

    asyncio.run(_run())
