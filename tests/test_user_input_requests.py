from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from codex_app_server_sdk import (
    CodexClient,
    UserInputAnswer,
    UserInputRequest,
    UserInputResponse,
)
from codex_app_server_sdk.transport import Transport


class UserInputTransport(Transport):
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


async def _wait_for_response(
    transport: UserInputTransport,
    *,
    timeout: float = 1.0,
) -> dict[str, Any]:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if transport.sent:
            return transport.sent[-1]
        await asyncio.sleep(0.01)
    raise AssertionError("timed out waiting for JSON-RPC response")


def test_user_input_request_auto_errors_without_handler() -> None:
    async def _run() -> None:
        transport = UserInputTransport()
        client = await CodexClient(
            transport,
            request_timeout=1.0,
            user_input_response_timeout=0.01,
        ).start()
        try:
            await transport._incoming.put(
                {
                    "jsonrpc": "2.0",
                    "id": 501,
                    "method": "item/tool/requestUserInput",
                    "params": {
                        "threadId": "thread-1",
                        "turnId": "turn-1",
                        "itemId": "item-1",
                        "questions": [
                            {
                                "id": "q1",
                                "header": "Plan",
                                "question": "Proceed?",
                                "isOther": True,
                                "isSecret": False,
                                "options": [
                                    {"label": "yes", "description": "Proceed"},
                                    {"label": "no", "description": "Stop"},
                                ],
                            }
                        ],
                    },
                }
            )

            response = await _wait_for_response(transport)
            assert response["id"] == 501
            error = response.get("error")
            assert isinstance(error, dict)
            assert error["code"] == -32000
        finally:
            await client.close()

    asyncio.run(_run())


def test_user_input_request_uses_callback_handler() -> None:
    async def _run() -> None:
        transport = UserInputTransport()
        client = await CodexClient(transport, request_timeout=1.0).start()
        try:

            async def _handler(req: UserInputRequest) -> UserInputResponse:
                assert req.thread_id == "thread-2"
                return UserInputResponse(
                    answers={
                        "q1": UserInputAnswer(answers=["yes"]),
                    }
                )

            client.set_user_input_handler(_handler)

            await transport._incoming.put(
                {
                    "jsonrpc": "2.0",
                    "id": "uid-502",
                    "method": "item/tool/requestUserInput",
                    "params": {
                        "threadId": "thread-2",
                        "turnId": "turn-2",
                        "itemId": "item-2",
                        "questions": [
                            {
                                "id": "q1",
                                "header": "Plan",
                                "question": "Proceed?",
                                "isOther": True,
                                "isSecret": False,
                                "options": None,
                            }
                        ],
                    },
                }
            )

            response = await _wait_for_response(transport)
            assert response["id"] == "uid-502"
            result = response.get("result")
            assert isinstance(result, dict)
            answers = result.get("answers")
            assert isinstance(answers, dict)
            q1 = answers.get("q1")
            assert isinstance(q1, dict)
            assert q1.get("answers") == ["yes"]
        finally:
            await client.close()

    asyncio.run(_run())


def test_user_input_manual_stream_response() -> None:
    async def _run() -> None:
        transport = UserInputTransport()
        client = await CodexClient(
            transport,
            request_timeout=1.0,
            user_input_response_timeout=None,
        ).start()
        try:
            await transport._incoming.put(
                {
                    "jsonrpc": "2.0",
                    "id": 503,
                    "method": "item/tool/requestUserInput",
                    "params": {
                        "threadId": "thread-3",
                        "turnId": "turn-3",
                        "itemId": "item-3",
                        "questions": [
                            {
                                "id": "q1",
                                "header": "Plan",
                                "question": "Proceed?",
                                "isOther": True,
                                "isSecret": False,
                                "options": [
                                    {"label": "yes", "description": "Proceed"},
                                ],
                            }
                        ],
                    },
                }
            )

            req = await asyncio.wait_for(
                anext(client.user_input_requests()), timeout=1.0
            )
            assert isinstance(req, UserInputRequest)
            await client.respond_user_input_choice(
                req,
                question_id="q1",
                selections=["yes"],
            )

            response = await _wait_for_response(transport)
            assert response["id"] == 503
            result = response.get("result")
            assert isinstance(result, dict)
            answers = result.get("answers")
            assert isinstance(answers, dict)
            assert answers["q1"]["answers"] == ["yes"]
        finally:
            await client.close()

    asyncio.run(_run())
