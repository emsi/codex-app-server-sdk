from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from codex_app_server_sdk import (
    CodexClient,
    CommandApprovalDecision,
    CommandApprovalRequest,
    CommandApprovalWithExecpolicyAmendment,
    FileChangeApprovalDecision,
    FileChangeApprovalRequest,
)
from codex_app_server_sdk.transport import Transport


class ApprovalTransport(Transport):
    def __init__(self) -> None:
        self._incoming: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.sent: list[dict[str, Any]] = []

    async def connect(self) -> None:
        return None

    async def send(self, payload: Mapping[str, Any]) -> None:
        self.sent.append(dict(payload))

    async def recv(self) -> dict[str, Any]:
        return await self._incoming.get()

    async def close(self) -> None:
        return None


async def _wait_for_sent_message(
    transport: ApprovalTransport,
    *,
    timeout: float = 1.0,
) -> dict[str, Any]:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if transport.sent:
            return transport.sent[-1]
        await asyncio.sleep(0.01)
    raise AssertionError("timed out waiting for transport.send()")


def test_approval_request_auto_declines_when_no_handler() -> None:
    async def _run() -> None:
        transport = ApprovalTransport()
        client = await CodexClient(transport, request_timeout=1.0).start()
        try:
            await transport._incoming.put(
                {
                    "jsonrpc": "2.0",
                    "id": 101,
                    "method": "item/commandExecution/requestApproval",
                    "params": {
                        "threadId": "thread-1",
                        "turnId": "turn-1",
                        "itemId": "item-1",
                        "command": "rg TODO",
                        "cwd": "/tmp/project",
                    },
                }
            )

            req = await asyncio.wait_for(anext(client.approval_requests()), timeout=1.0)
            assert isinstance(req, CommandApprovalRequest)
            assert req.thread_id == "thread-1"
            assert req.turn_id == "turn-1"
            assert req.item_id == "item-1"

            response = await _wait_for_sent_message(transport)
            assert response["id"] == 101
            result = response.get("result")
            assert isinstance(result, dict)
            assert result["decision"] == "decline"
        finally:
            await client.close()

    asyncio.run(_run())


def test_approval_request_uses_async_handler_decision() -> None:
    async def _run() -> None:
        transport = ApprovalTransport()
        client = await CodexClient(transport, request_timeout=1.0).start()
        try:
            async def _handler(request: Any) -> FileChangeApprovalDecision:
                assert isinstance(request, FileChangeApprovalRequest)
                return "accept_for_session"

            client.set_approval_handler(_handler)

            await transport._incoming.put(
                {
                    "jsonrpc": "2.0",
                    "id": 202,
                    "method": "item/fileChange/requestApproval",
                    "params": {
                        "threadId": "thread-2",
                        "turnId": "turn-2",
                        "itemId": "item-2",
                        "reason": "needs write access",
                    },
                }
            )

            response = await _wait_for_sent_message(transport)
            assert response["id"] == 202
            result = response.get("result")
            assert isinstance(result, dict)
            assert result["decision"] == "acceptForSession"
        finally:
            await client.close()

    asyncio.run(_run())


def test_approval_request_encodes_execpolicy_amendment_decision() -> None:
    async def _run() -> None:
        transport = ApprovalTransport()
        client = await CodexClient(transport, request_timeout=1.0).start()
        try:
            async def _handler(request: Any) -> CommandApprovalDecision:
                assert isinstance(request, CommandApprovalRequest)
                return CommandApprovalWithExecpolicyAmendment(["uv", "run"])

            client.set_approval_handler(_handler)

            await transport._incoming.put(
                {
                    "jsonrpc": "2.0",
                    "id": "approval-303",
                    "method": "item/commandExecution/requestApproval",
                    "params": {
                        "threadId": "thread-3",
                        "turnId": "turn-3",
                        "itemId": "item-3",
                    },
                }
            )

            response = await _wait_for_sent_message(transport)
            assert response["id"] == "approval-303"
            result = response.get("result")
            assert isinstance(result, dict)
            decision = result.get("decision")
            assert isinstance(decision, dict)
            amendment = decision.get("acceptWithExecpolicyAmendment")
            assert isinstance(amendment, dict)
            assert amendment["execpolicy_amendment"] == ["uv", "run"]
        finally:
            await client.close()

    asyncio.run(_run())


def test_unknown_server_request_still_returns_method_not_found_error() -> None:
    async def _run() -> None:
        transport = ApprovalTransport()
        client = await CodexClient(transport, request_timeout=1.0).start()
        try:
            await transport._incoming.put(
                {
                    "jsonrpc": "2.0",
                    "id": 404,
                    "method": "unknown/request",
                    "params": {},
                }
            )
            response = await _wait_for_sent_message(transport)
            assert response["id"] == 404
            error = response.get("error")
            assert isinstance(error, dict)
            assert error["code"] == -32601
        finally:
            await client.close()

    asyncio.run(_run())
