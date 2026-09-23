"""Regression coverage for turn routing, terminal states, and approval ownership."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from importlib.metadata import version
from typing import Any

import pytest

from codex_app_server_sdk import (
    CodexClient,
    CodexProtocolError,
    CodexTransportError,
    CodexTurnInactiveError,
    __version__,
)
from codex_app_server_sdk.models import ChatContinuation
from codex_app_server_sdk.transport import Transport

WAIT_SECONDS = 1.0
IDLE_SECONDS = 0.01
THREAD_A = "thread-a"
THREAD_B = "thread-b"
FAILURE_MESSAGE = "controlled server failure"
APPROVAL_ID = 42
PACKAGE_NAME = "codex-app-server-sdk"
APPROVAL_PARAMS = {"threadId": THREAD_A, "turnId": THREAD_A, "itemId": "command-a"}


class ControlledTransport(Transport):
    """Answer RPCs while allowing tests to control notification ordering."""

    def __init__(self) -> None:
        """Create queues and configurable interruption behavior."""
        self.incoming: asyncio.Queue[dict[str, Any] | Exception] = asyncio.Queue()
        self.sent: list[dict[str, Any]] = []
        self.started: asyncio.Queue[str] = asyncio.Queue()
        self.interrupt_error = False
        self.complete_interrupt = True
        self.complete_before_response = False

    async def connect(self) -> None:
        """Open the in-memory transport."""

    async def close(self) -> None:
        """Close the in-memory transport."""

    async def recv(self) -> dict[str, Any]:
        """Return the next injected message or raise its transport failure."""
        item = await self.incoming.get()
        if isinstance(item, Exception):
            raise item
        return item

    async def send(self, payload: Mapping[str, Any]) -> None:
        """Record messages and simulate the required server RPC responses.

        :param payload: Client request, notification, or response.
        :return: None.
        """
        message = dict(payload)
        self.sent.append(message)
        method = message.get("method")
        if method is None or "id" not in message:
            return
        result: dict[str, Any] = {}
        if method == "initialize":
            result = {"userAgent": "test-server"}
        elif method == "turn/start":
            thread_id = message["params"]["threadId"]
            result = {"turn": {"id": thread_id, "status": "inProgress"}}
            self.started.put_nowait(thread_id)
            if self.complete_before_response:
                self.finish(thread_id)
        elif method == "turn/interrupt":
            assert message["params"]["threadId"] == message["params"]["turnId"]
            if self.interrupt_error:
                self.incoming.put_nowait(
                    {
                        "id": message["id"],
                        "error": {"code": -32602, "message": FAILURE_MESSAGE},
                    }
                )
                return
            if self.complete_interrupt:
                self.finish(message["params"]["threadId"], status="interrupted")
        self.incoming.put_nowait({"id": message["id"], "result": result})

    def finish(self, thread_id: str, *, status: str = "completed") -> None:
        """Emit a completed assistant item followed by a v2 terminal event.

        :param thread_id: Thread and turn identifier used by the fixture.
        :param status: Terminal turn status.
        :return: None.
        """
        self.incoming.put_nowait(
            {
                "method": "item/completed",
                "params": {
                    "threadId": thread_id,
                    "turnId": thread_id,
                    "item": {
                        "id": thread_id,
                        "type": "agentMessage",
                        "text": thread_id,
                    },
                },
            }
        )
        self.incoming.put_nowait(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": thread_id,
                    "turn": {
                        "id": thread_id,
                        "status": status,
                        "items": [],
                        "error": (
                            {"message": FAILURE_MESSAGE} if status == "failed" else None
                        ),
                    },
                },
            }
        )


async def _started(transport: ControlledTransport) -> str:
    """Wait for one turn-start request without an unbounded test hang.

    :param transport: Fixture transport.
    :return: Started thread id.
    """
    return await asyncio.wait_for(transport.started.get(), WAIT_SECONDS)


async def _continuation(client: CodexClient) -> ChatContinuation:
    """Start a quiet turn and obtain a resumable inactivity token.

    :param client: Connected fixture client.
    :return: Continuation for the quiet turn.
    """
    with pytest.raises(CodexTurnInactiveError) as error:
        await client.chat_once("wait", THREAD_A, inactivity_timeout=IDLE_SECONDS)
    return error.value.continuation


@pytest.mark.parametrize("mode", ["once", "stream"])
@pytest.mark.parametrize("status", ["failed", "interrupted"])
def test_unsuccessful_terminal_status_raises(mode: str, status: str) -> None:
    """Never turn partial assistant output into success after failure/interruption.

    :param mode: Buffered or step-streaming API.
    :param status: Unsuccessful terminal status.
    :return: None.
    """

    async def run() -> None:
        """Drive one controlled terminal event through the public chat API."""
        transport = ControlledTransport()
        async with CodexClient(transport) as client:

            async def consume() -> None:
                """Consume the selected public chat API."""
                if mode == "once":
                    await client.chat_once("test", THREAD_A)
                else:
                    async for _ in client.chat("test", THREAD_A):
                        pass

            task = asyncio.create_task(consume())
            await _started(transport)
            transport.finish(THREAD_A, status=status)
            expected = FAILURE_MESSAGE if status == "failed" else "interrupted"
            with pytest.raises(CodexProtocolError, match=expected):
                await asyncio.wait_for(task, WAIT_SECONDS)

    asyncio.run(run())


def test_concurrent_turns_receive_their_own_events() -> None:
    """Deliver events in reverse consumer order without starving either turn."""

    async def run() -> None:
        """Start two calls together, including their automatic initialization."""
        transport = ControlledTransport()
        async with CodexClient(transport) as client:
            first = asyncio.create_task(client.chat_once("first", THREAD_A))
            second = asyncio.create_task(client.chat_once("second", THREAD_B))
            await _started(transport)
            await _started(transport)
            await client.request("test/barrier")
            await asyncio.sleep(0)
            transport.finish(THREAD_B)
            transport.finish(THREAD_A)
            results = await asyncio.wait_for(
                asyncio.gather(first, second), WAIT_SECONDS
            )
            assert [result.final_text for result in results] == [THREAD_A, THREAD_B]
            assert (
                sum(message.get("method") == "initialize" for message in transport.sent)
                == 1
            )
            methods = [message.get("method") for message in transport.sent]
            assert methods[:2] == ["initialize", "initialized"]
            assert transport.sent[0]["params"]["clientInfo"] == {
                "name": PACKAGE_NAME,
                "version": version(PACKAGE_NAME),
            }
            assert __version__ == version(PACKAGE_NAME)

    asyncio.run(run())


def test_completion_before_start_response_is_preserved() -> None:
    """Retain notifications that arrive before the turn-start RPC result."""

    async def run() -> None:
        """Complete a turn before the client learns its turn id."""
        transport = ControlledTransport()
        transport.complete_before_response = True
        async with CodexClient(transport) as client:
            result = await asyncio.wait_for(
                client.chat_once("test", THREAD_A), WAIT_SECONDS
            )
            assert result.final_text == THREAD_A

    asyncio.run(run())


@pytest.mark.parametrize("disconnect", [False, True])
def test_close_or_disconnect_wakes_all_turns(disconnect: bool) -> None:
    """All indefinite turn waits must exit when their shared transport ends.

    :param disconnect: Simulate receive failure instead of explicit client close.
    :return: None.
    """

    async def run() -> None:
        """End a shared connection while both callers await notifications."""
        transport = ControlledTransport()
        async with CodexClient(transport, inactivity_timeout=None) as client:
            first = asyncio.create_task(client.chat_once("first", THREAD_A))
            second = asyncio.create_task(client.chat_once("second", THREAD_B))
            await _started(transport)
            await _started(transport)
            if disconnect:
                transport.incoming.put_nowait(CodexTransportError("disconnected"))
            else:
                await client.close()
            results = await asyncio.wait_for(
                asyncio.gather(first, second, return_exceptions=True), WAIT_SECONDS
            )
            assert all(isinstance(result, CodexTransportError) for result in results)

    asyncio.run(run())


def test_interrupt_infers_thread_for_tracked_turn() -> None:
    """The existing turn-only call remains usable for SDK-owned turns."""

    async def run() -> None:
        """Infer the owning thread, then observe the interrupted outcome."""
        transport = ControlledTransport()
        async with CodexClient(transport) as client:
            continuation = await _continuation(client)
            with pytest.raises(ValueError, match="does not match"):
                await client.interrupt_turn(THREAD_A, thread_id=THREAD_B)
            await client.interrupt_turn(THREAD_A)
            with pytest.raises(CodexProtocolError, match="interrupted"):
                await client.chat_once(continuation=continuation)

    asyncio.run(run())


def test_interrupt_requires_thread_for_untracked_turn() -> None:
    """Raw callers must supply the thread rather than send an invalid RPC."""

    async def run() -> None:
        """Reject missing identity locally, then send a valid explicit request."""
        transport = ControlledTransport()
        async with CodexClient(transport) as client:
            with pytest.raises(ValueError, match="thread_id is required"):
                await client.interrupt_turn(THREAD_A)
            assert not transport.sent
            await client.interrupt_turn(THREAD_A, thread_id=THREAD_A)

    asyncio.run(run())


def test_cancel_drains_already_completed_turn_without_interrupt() -> None:
    """Buffered completion makes a redundant interrupt request unnecessary."""

    async def run() -> None:
        """Finish after timeout but before the cancellation call."""
        transport = ControlledTransport()
        async with CodexClient(transport) as client:
            continuation = await _continuation(client)
            transport.finish(THREAD_A)
            await client.request("test/barrier")
            result = await client.cancel(continuation)
            assert result.was_completed and not result.was_interrupted
            assert all(
                message.get("method") != "turn/interrupt" for message in transport.sent
            )

    asyncio.run(run())


def test_cancel_sends_both_ids_and_observes_interruption() -> None:
    """Cancellation uses the full wire identity and consumes terminal events."""

    async def run() -> None:
        """Cancel a turn after an inactivity timeout."""
        transport = ControlledTransport()
        async with CodexClient(transport) as client:
            continuation = await _continuation(client)
            result = await client.cancel(continuation)
            assert result.was_interrupted
            assert result.raw_events[-1]["params"]["turn"]["status"] == "interrupted"

    asyncio.run(run())


@pytest.mark.parametrize("reject", [False, True])
def test_unsuccessful_cancel_preserves_continuation(reject: bool) -> None:
    """Keep turn state when interruption is rejected or never confirmed.

    :param reject: Reject the RPC instead of withholding its terminal event.
    :return: None.
    """

    async def run() -> None:
        """Resume the same turn after a failed cancellation attempt."""
        transport = ControlledTransport()
        transport.interrupt_error = reject
        transport.complete_interrupt = False
        async with CodexClient(transport) as client:
            continuation = await _continuation(client)
            error = CodexProtocolError if reject else CodexTurnInactiveError
            with pytest.raises(error):
                await client.cancel(continuation, timeout=IDLE_SECONDS)
            transport.finish(THREAD_A)
            result = await client.chat_once(continuation=continuation)
            assert result.final_text == THREAD_A

    asyncio.run(run())


@pytest.mark.parametrize(
    "method",
    ["item/commandExecution/requestApproval", "item/fileChange/requestApproval"],
)
def test_manual_approval_survives_human_wait(method: str) -> None:
    """Manual requests remain pending across asynchronous human interaction.

    :param method: Approval request type.
    :return: None.
    """

    async def run() -> None:
        """Delay a manual decision, then verify exactly one approval response."""
        transport = ControlledTransport()
        async with CodexClient(transport, approval_mode="manual") as client:
            transport.incoming.put_nowait(
                {"id": APPROVAL_ID, "method": method, "params": APPROVAL_PARAMS}
            )
            request = await asyncio.wait_for(
                anext(client.approval_requests()), WAIT_SECONDS
            )
            await asyncio.sleep(IDLE_SECONDS)
            assert not transport.sent
            await client.approve_approval(request)
            assert transport.sent == [
                {"jsonrpc": "2.0", "id": APPROVAL_ID, "result": {"decision": "accept"}}
            ]
            with pytest.raises(CodexProtocolError, match="no longer pending"):
                await client.approve_approval(request)

    asyncio.run(run())


def test_resolved_manual_approval_cannot_be_answered() -> None:
    """A server-cleared prompt must not accept a stale manual decision."""

    async def run() -> None:
        """Use a subsequent RPC reply as a barrier after server resolution."""
        transport = ControlledTransport()
        async with CodexClient(transport, approval_mode="manual") as client:
            transport.incoming.put_nowait(
                {
                    "id": APPROVAL_ID,
                    "method": "item/fileChange/requestApproval",
                    "params": APPROVAL_PARAMS,
                }
            )
            request = await asyncio.wait_for(
                anext(client.approval_requests()), WAIT_SECONDS
            )
            transport.incoming.put_nowait(
                {
                    "method": "serverRequest/resolved",
                    "params": {"threadId": THREAD_A, "requestId": APPROVAL_ID},
                }
            )
            await client.request("test/barrier")
            with pytest.raises(CodexProtocolError, match="no longer pending"):
                await client.approve_approval(request)

    asyncio.run(run())
