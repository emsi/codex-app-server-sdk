from __future__ import annotations

import asyncio
import contextlib
import os
import shlex
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from .errors import (
    CodexProtocolError,
    CodexTimeoutError,
    CodexTransportError,
    CodexTurnInactiveError,
)
from .models import (
    CancelResult,
    ChatContinuation,
    ChatResult,
    ConversationStep,
    InitializeResult,
    ThreadConfig,
    TurnOverrides,
    UnsetType,
)
from .protocol import (
    DEFAULT_OPT_OUT_NOTIFICATION_METHODS,
    INITIALIZE_METHOD,
    ITEM_COMPLETED_METHOD,
    THREAD_FORK_METHOD,
    THREAD_READ_METHOD,
    THREAD_RESUME_METHOD,
    THREAD_START_METHOD,
    TURN_INTERRUPT_METHOD,
    TURN_START_METHOD,
    extract_error,
    is_response_message,
    is_turn_completed,
    is_turn_failed,
    make_error_response,
    make_request,
)
from .transport import StdioTransport, Transport, WebSocketTransport


@dataclass(slots=True)
class _StepRecord:
    event_index: int
    step: ConversationStep


@dataclass(slots=True)
class _TurnSession:
    thread_id: str
    turn_id: str
    raw_events: list[dict[str, Any]] = field(default_factory=list)
    completed_agent_messages: list[tuple[str | None, str]] = field(default_factory=list)
    completed_item_ids: set[str] = field(default_factory=set)
    step_records: list[_StepRecord] = field(default_factory=list)
    step_item_ids: set[str] = field(default_factory=set)
    completed: bool = False
    failed: bool = False
    failure_message: str | None = None
    interrupted: bool = False


class ThreadHandle:
    """Thread-scoped high-level API wrapper bound to one `thread_id`."""

    def __init__(
        self,
        client: CodexClient,
        thread_id: str,
        *,
        defaults: ThreadConfig | None = None,
    ) -> None:
        self._client = client
        self._thread_id = thread_id
        self._defaults = defaults if defaults is not None else ThreadConfig()

    @property
    def thread_id(self) -> str:
        """Thread id for this handle."""
        return self._thread_id

    @property
    def defaults(self) -> ThreadConfig:
        """Current local default configuration snapshot for this thread handle."""
        return self._defaults

    async def chat_once(
        self,
        text: str | None = None,
        *,
        user: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        inactivity_timeout: float | None = None,
        continuation: ChatContinuation | None = None,
        turn_overrides: TurnOverrides | None = None,
    ) -> ChatResult:
        """Send one message on this thread and return final assistant output."""
        return await self._client.chat_once(
            text,
            thread_id=self._thread_id,
            user=user,
            metadata=metadata,
            inactivity_timeout=inactivity_timeout,
            continuation=continuation,
            turn_overrides=turn_overrides,
        )

    async def chat(
        self,
        text: str | None = None,
        *,
        user: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        inactivity_timeout: float | None = None,
        continuation: ChatContinuation | None = None,
        turn_overrides: TurnOverrides | None = None,
    ) -> AsyncIterator[ConversationStep]:
        """Stream completed, non-delta steps for one message on this thread."""
        async for step in self._client.chat(
            text,
            thread_id=self._thread_id,
            user=user,
            metadata=metadata,
            inactivity_timeout=inactivity_timeout,
            continuation=continuation,
            turn_overrides=turn_overrides,
        ):
            yield step

    async def fork(self, *, overrides: ThreadConfig | None = None) -> ThreadHandle:
        """Fork this thread into a new thread handle."""
        merged = _merge_thread_config(self._defaults, overrides)
        return await self._client.fork_thread(self._thread_id, overrides=merged)

    async def update_defaults(self, overrides: ThreadConfig) -> None:
        """Apply thread-level overrides and update local defaults snapshot."""
        await self._client.set_thread_defaults(self._thread_id, overrides)
        self._defaults = _merge_thread_config(self._defaults, overrides)

    async def read(self, *, include_turns: bool = True) -> Any:
        """Read server-side thread state."""
        return await self._client.request(
            THREAD_READ_METHOD,
            {"threadId": self._thread_id, "includeTurns": include_turns},
        )


class CodexClient:
    """High-level async client for Codex app-server."""

    def __init__(
        self,
        transport: Transport,
        *,
        request_timeout: float = 30.0,
        inactivity_timeout: float | None = 180.0,
        strict: bool = False,
    ) -> None:
        """Create a client bound to a transport.

        Args:
            transport: Connected or connectable transport instance.
            request_timeout: Default timeout for request/response calls.
            inactivity_timeout: Turn inactivity timeout in seconds. If None,
                turn waits can run indefinitely until terminal events.
            strict: If True, fail on certain protocol ambiguities.
        """
        self._transport = transport
        self._request_timeout = request_timeout
        self._inactivity_timeout = inactivity_timeout
        self._strict = strict
        self._initialized = False

        self._next_request_id = 1
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._notifications: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._deferred_notifications: list[dict[str, Any]] = []
        self._turn_sessions: dict[str, _TurnSession] = {}

        self._send_lock = asyncio.Lock()
        self._receiver_task: asyncio.Task[None] | None = None
        self._started = False
        self._closed = False

    @classmethod
    def connect_stdio(
        cls,
        *,
        command: Sequence[str] | None = None,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        connect_timeout: float = 30.0,
        request_timeout: float = 30.0,
        inactivity_timeout: float | None = 180.0,
        strict: bool = False,
    ) -> CodexClient:
        """Create an unstarted client configured for stdio transport."""
        resolved_command = list(command) if command is not None else _default_stdio_command()
        transport = StdioTransport(
            resolved_command,
            cwd=cwd,
            env=env,
            connect_timeout=connect_timeout,
        )
        client = cls(
            transport,
            request_timeout=request_timeout,
            inactivity_timeout=inactivity_timeout,
            strict=strict,
        )
        return client

    @classmethod
    def connect_websocket(
        cls,
        *,
        url: str | None = None,
        token: str | None = None,
        headers: Mapping[str, str] | None = None,
        connect_timeout: float = 30.0,
        request_timeout: float = 30.0,
        inactivity_timeout: float | None = 180.0,
        strict: bool = False,
    ) -> CodexClient:
        """Create an unstarted client configured for websocket transport."""
        resolved_url = url or os.getenv("CODEX_APP_SERVER_WS_URL") or "ws://127.0.0.1:8765"
        resolved_token = token or os.getenv("CODEX_APP_SERVER_TOKEN")
        resolved_headers = dict(headers) if headers is not None else {}
        if resolved_token and "Authorization" not in resolved_headers:
            resolved_headers["Authorization"] = f"Bearer {resolved_token}"

        transport = WebSocketTransport(
            resolved_url,
            headers=resolved_headers,
            connect_timeout=connect_timeout,
        )
        client = cls(
            transport,
            request_timeout=request_timeout,
            inactivity_timeout=inactivity_timeout,
            strict=strict,
        )
        return client

    async def start(self) -> CodexClient:
        """Connect transport and start background receive loop once."""
        if self._closed:
            raise CodexTransportError("client is closed")
        if self._started:
            return self
        await self._transport.connect()
        self._start_receiver()
        self._started = True
        return self

    async def __aenter__(self) -> CodexClient:
        """Support `async with CodexClient(...)` usage."""
        return await self.start()

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        """Close client on context-manager exit."""
        await self.close()

    async def close(self) -> None:
        """Stop receive loop, fail pending requests, and close transport."""
        if self._closed:
            return
        self._closed = True

        if self._receiver_task is not None:
            self._receiver_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._receiver_task
            self._receiver_task = None

        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(CodexTransportError("client is closing"))
        self._pending.clear()

        self._turn_sessions.clear()
        self._deferred_notifications.clear()

        await self._transport.close()
        self._started = False

    async def initialize(
        self,
        params: Mapping[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> InitializeResult:
        """
        Perform the app-server initialize handshake and cache client initialization state.

        This method is optional for normal chat usage because `chat_once()` and `chat()`
        initialize automatically on first use. Call it explicitly when you want to fail
        fast on handshake issues or inspect server protocol/capabilities metadata.

        :param params: Optional initialize request payload. The payload is merged with
            library defaults via `_prepare_initialize_params()`, including capability
            opt-out defaults when applicable.
        :param timeout: Optional per-request timeout override in seconds.
        :return: Parsed initialize result containing normalized protocol/server fields
            and raw initialize payload.
        """
        payload = _prepare_initialize_params(params)
        result = await self.request(INITIALIZE_METHOD, payload, timeout=timeout)
        result_dict = result if isinstance(result, dict) else {"value": result}
        self._initialized = True
        return InitializeResult(
            protocol_version=_find_first_string_by_exact_keys(
                result_dict,
                {"protocolversion", "protocol_version"},
            ),
            server_info=_find_first_dict_by_exact_key(result_dict, {"serverinfo", "server_info"}),
            capabilities=_find_first_dict_by_exact_key(result_dict, {"capabilities"}),
            raw=result_dict,
        )

    async def request(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> Any:
        """Send a JSON-RPC request and await response result."""
        if self._closed:
            raise CodexTransportError("client is closed")

        request_id = self._next_request_id
        self._next_request_id += 1

        message = make_request(request_id, method, dict(params) if params is not None else None)
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[request_id] = future

        async with self._send_lock:
            await self._transport.send(message)

        timeout_seconds = timeout if timeout is not None else self._request_timeout
        try:
            response = await asyncio.wait_for(future, timeout=timeout_seconds)
        except asyncio.TimeoutError as exc:
            self._pending.pop(request_id, None)
            raise CodexTimeoutError(
                f"request timed out for method={method!r} after {timeout_seconds:.1f}s"
            ) from exc

        error = extract_error(response)
        if error is not None:
            code = error.get("code")
            message_text = str(error.get("message", "JSON-RPC error"))
            data = error.get("data")
            raise CodexProtocolError(
                f"{method} failed: {message_text}",
                code=code if isinstance(code, int) else None,
                data=data,
            )
        return response.get("result")

    async def start_thread(self, config: ThreadConfig | None = None) -> ThreadHandle:
        """Create a new thread with optional thread-level configuration."""
        if not self._initialized:
            await self.initialize()

        params = _thread_config_to_params(config)
        result = await self.request(THREAD_START_METHOD, params)
        thread_id = _extract_thread_id(result)
        if not thread_id:
            raise CodexProtocolError("thread/start succeeded but no thread id found")
        return ThreadHandle(self, thread_id, defaults=config if config is not None else ThreadConfig())

    async def resume_thread(
        self,
        thread_id: str,
        *,
        overrides: ThreadConfig | None = None,
    ) -> ThreadHandle:
        """Resume an existing thread and optionally apply thread-level overrides."""
        if not self._initialized:
            await self.initialize()

        params: dict[str, Any] = {"threadId": thread_id}
        params.update(_thread_config_to_params(overrides))
        result = await self.request(THREAD_RESUME_METHOD, params)
        resolved_thread_id = _extract_thread_id(result) or thread_id
        return ThreadHandle(
            self,
            resolved_thread_id,
            defaults=overrides if overrides is not None else ThreadConfig(),
        )

    async def fork_thread(
        self,
        thread_id: str,
        *,
        overrides: ThreadConfig | None = None,
    ) -> ThreadHandle:
        """Fork an existing thread into a new thread with optional overrides."""
        if not self._initialized:
            await self.initialize()

        params: dict[str, Any] = {"threadId": thread_id}
        params.update(_thread_config_to_params(overrides))
        result = await self.request(THREAD_FORK_METHOD, params)
        forked_thread_id = _extract_thread_id(result)
        if not forked_thread_id:
            raise CodexProtocolError("thread/fork succeeded but no forked thread id found")
        return ThreadHandle(
            self,
            forked_thread_id,
            defaults=overrides if overrides is not None else ThreadConfig(),
        )

    async def set_thread_defaults(self, thread_id: str, overrides: ThreadConfig) -> None:
        """Apply thread-level overrides to an existing thread."""
        if not self._initialized:
            await self.initialize()
        params: dict[str, Any] = {"threadId": thread_id}
        params.update(_thread_config_to_params(overrides))
        await self.request(THREAD_RESUME_METHOD, params)

    async def chat_once(
        self,
        text: str | None = None,
        thread_id: str | None = None,
        *,
        user: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        thread_config: ThreadConfig | None = None,
        turn_overrides: TurnOverrides | None = None,
        inactivity_timeout: float | None = None,
        continuation: ChatContinuation | None = None,
    ) -> ChatResult:
        """Send one user message and wait for final assistant output.

        On inactivity timeout this raises `CodexTurnInactiveError` with a
        continuation token; call `chat_once(continuation=...)` to resume.
        """
        if continuation is not None:
            if text is not None:
                raise ValueError("text must be omitted when continuation is provided")
            if thread_config is not None:
                raise ValueError("thread_config cannot be used with continuation")
            if turn_overrides is not None:
                raise ValueError("turn_overrides cannot be used with continuation")
            session = self._get_continuation_session(continuation, expected_mode="once")
            active_thread_id = session.thread_id
        else:
            if text is None:
                raise ValueError("text is required when continuation is not provided")
            active_thread_id, session = await self._start_chat_turn(
                text=text,
                thread_id=thread_id,
                user=user,
                metadata=metadata,
                thread_config=thread_config,
                turn_overrides=turn_overrides,
            )

        cursor = continuation.cursor if continuation is not None else len(session.raw_events)
        timeout_value = self._resolve_inactivity_timeout(inactivity_timeout)

        while True:
            if session.failed:
                self._cleanup_turn_state(session.turn_id)
                raise CodexProtocolError(session.failure_message or "turn failed")

            if session.completed:
                break

            event = await self._await_turn_event_or_timeout(
                session=session,
                timeout_value=timeout_value,
                cursor=cursor,
                mode="once",
            )

            if _is_transport_error_event(event):
                message = _find_first_string_by_exact_keys(event, {"message"})
                raise CodexTransportError(message or "transport failed")

            self._apply_event_to_session(session, event)
            cursor = len(session.raw_events)

        assistant_item_id: str | None = None
        final_text = ""
        completion_source: Literal["item_completed", "thread_read_fallback"] | None = None

        if session.completed_agent_messages:
            assistant_item_id, final_text = session.completed_agent_messages[-1]
            completion_source = "item_completed"
        else:
            fallback_message = await self._read_turn_agent_message(
                thread_id=active_thread_id,
                turn_id=session.turn_id,
            )
            if fallback_message is not None:
                assistant_item_id, final_text = fallback_message
                completion_source = "thread_read_fallback"

        final_text = final_text.strip()
        if not final_text:
            self._cleanup_turn_state(session.turn_id)
            raise CodexProtocolError(
                "turn completed but no final assistant message could be resolved"
            )

        result = ChatResult(
            thread_id=active_thread_id,
            turn_id=session.turn_id,
            final_text=final_text,
            raw_events=list(session.raw_events),
            assistant_item_id=assistant_item_id,
            completion_source=completion_source,
        )
        self._cleanup_turn_state(session.turn_id)
        return result

    async def chat(
        self,
        text: str | None = None,
        thread_id: str | None = None,
        *,
        user: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        thread_config: ThreadConfig | None = None,
        turn_overrides: TurnOverrides | None = None,
        inactivity_timeout: float | None = None,
        continuation: ChatContinuation | None = None,
    ) -> AsyncIterator[ConversationStep]:
        """Stream completed, non-delta conversation steps for one turn.

        On inactivity timeout this raises `CodexTurnInactiveError` with a
        continuation token; call `chat(continuation=...)` to resume the same turn.
        """
        if continuation is not None:
            if text is not None:
                raise ValueError("text must be omitted when continuation is provided")
            if thread_config is not None:
                raise ValueError("thread_config cannot be used with continuation")
            if turn_overrides is not None:
                raise ValueError("turn_overrides cannot be used with continuation")
            session = self._get_continuation_session(
                continuation,
                expected_mode="stream",
            )
            cursor = max(0, continuation.cursor)
            active_thread_id = session.thread_id
        else:
            if text is None:
                raise ValueError("text is required when continuation is not provided")
            active_thread_id, session = await self._start_chat_turn(
                text=text,
                thread_id=thread_id,
                user=user,
                metadata=metadata,
                thread_config=thread_config,
                turn_overrides=turn_overrides,
            )
            cursor = len(session.raw_events)

        timeout_value = self._resolve_inactivity_timeout(inactivity_timeout)

        for record in session.step_records:
            if record.event_index >= cursor:
                yield record.step
        if cursor < len(session.raw_events):
            cursor = len(session.raw_events)

        while True:
            if session.failed:
                self._cleanup_turn_state(session.turn_id)
                raise CodexProtocolError(session.failure_message or "turn failed")

            if session.completed:
                for fallback_step in await self._read_turn_steps(
                    thread_id=active_thread_id,
                    turn_id=session.turn_id,
                ):
                    if fallback_step.item_id is None:
                        continue
                    if fallback_step.item_id in session.step_item_ids:
                        continue
                    session.step_item_ids.add(fallback_step.item_id)
                    yield fallback_step
                self._cleanup_turn_state(session.turn_id)
                return

            event = await self._await_turn_event_or_timeout(
                session=session,
                timeout_value=timeout_value,
                cursor=cursor,
                mode="stream",
            )

            if _is_transport_error_event(event):
                message = _find_first_string_by_exact_keys(event, {"message"})
                raise CodexTransportError(message or "transport failed")

            step_count_before = len(session.step_records)
            self._apply_event_to_session(session, event)
            cursor = len(session.raw_events)

            for record in session.step_records[step_count_before:]:
                yield record.step

    async def cancel(
        self,
        continuation: ChatContinuation,
        *,
        timeout: float | None = None,
    ) -> CancelResult:
        """Interrupt a running turn and return unread data for that continuation."""
        wait_timeout = timeout if timeout is not None else self._request_timeout
        turn_id = continuation.turn_id
        thread_id = continuation.thread_id
        cursor = max(0, continuation.cursor)

        session = self._turn_sessions.get(turn_id)

        if session is None:
            was_interrupted = False
            with contextlib.suppress(
                CodexProtocolError,
                CodexTimeoutError,
                CodexTransportError,
            ):
                await self.interrupt_turn(turn_id, timeout=timeout)
                was_interrupted = True
            self._drop_deferred_for_turn(turn_id)
            return CancelResult(
                thread_id=thread_id,
                turn_id=turn_id,
                was_interrupted=was_interrupted,
            )

        if session.thread_id != thread_id:
            raise CodexProtocolError("continuation thread_id does not match active turn session")

        was_interrupted = False
        if not session.completed and not session.failed:
            with contextlib.suppress(
                CodexProtocolError,
                CodexTimeoutError,
                CodexTransportError,
            ):
                await self.interrupt_turn(turn_id, timeout=timeout)
                was_interrupted = True
                session.interrupted = True

            await self._pump_turn_session(session, max_wait=wait_timeout)

        unread_events = list(session.raw_events[cursor:])
        unread_steps = [
            record.step for record in session.step_records if record.event_index >= cursor
        ]

        result = CancelResult(
            thread_id=session.thread_id,
            turn_id=session.turn_id,
            steps=unread_steps,
            raw_events=unread_events,
            was_completed=session.completed,
            was_interrupted=was_interrupted,
        )

        self._cleanup_turn_state(turn_id)
        return result

    async def interrupt_turn(self, turn_id: str, *, timeout: float | None = None) -> None:
        """Send best-effort `turn/interrupt` for a running turn."""
        await self.request(
            TURN_INTERRUPT_METHOD,
            {"turnId": turn_id},
            timeout=timeout,
        )

    async def _read_turn_agent_message(
        self,
        *,
        thread_id: str,
        turn_id: str | None,
    ) -> tuple[str | None, str] | None:
        """Read thread state and return final assistant message for a turn."""
        steps = await self._read_turn_steps(thread_id=thread_id, turn_id=turn_id)
        final_message: tuple[str | None, str] | None = None
        for step in steps:
            if step.item_type != "agentMessage":
                continue
            final_message = (step.item_id, step.text or "")
        return final_message

    async def _read_turn_steps(
        self,
        *,
        thread_id: str,
        turn_id: str | None,
    ) -> list[ConversationStep]:
        """Read thread state and return completed steps for target turn."""
        response = await self.request(
            THREAD_READ_METHOD,
            {"threadId": thread_id, "includeTurns": True},
        )
        if not isinstance(response, dict):
            return []

        thread = response.get("thread")
        if not isinstance(thread, dict):
            return []

        turns = thread.get("turns")
        if not isinstance(turns, list):
            return []

        target_turn: dict[str, Any] | None = None
        if turn_id is not None:
            for turn in turns:
                if isinstance(turn, dict) and turn.get("id") == turn_id:
                    target_turn = turn
                    break
        elif turns:
            last_turn = turns[-1]
            if isinstance(last_turn, dict):
                target_turn = last_turn

        if target_turn is None:
            return []

        items = target_turn.get("items")
        if not isinstance(items, list):
            return []

        resolved_thread_id = thread.get("id")
        if not isinstance(resolved_thread_id, str):
            resolved_thread_id = thread_id
        resolved_turn_id = target_turn.get("id")
        if not isinstance(resolved_turn_id, str):
            resolved_turn_id = turn_id or ""

        steps: list[ConversationStep] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            step = _step_from_item(
                thread_id=resolved_thread_id,
                turn_id=resolved_turn_id,
                item=item,
            )
            if step is not None:
                steps.append(step)

        return steps

    async def _start_chat_turn(
        self,
        *,
        text: str,
        thread_id: str | None,
        user: str | None,
        metadata: Mapping[str, Any] | None,
        thread_config: ThreadConfig | None,
        turn_overrides: TurnOverrides | None,
    ) -> tuple[str, _TurnSession]:
        if not self._initialized:
            await self.initialize()

        active_thread_id = await self._prepare_thread_context(
            thread_id=thread_id,
            metadata=metadata,
            thread_config=thread_config,
        )

        turn_params: dict[str, Any] = {
            "threadId": active_thread_id,
            "input": [{"type": "text", "text": text}],
        }
        if user:
            turn_params["user"] = user
        if metadata:
            turn_params["metadata"] = dict(metadata)
        turn_params.update(_turn_overrides_to_params(turn_overrides))

        turn_result = await self.request(TURN_START_METHOD, turn_params)
        turn_id = _extract_turn_id(turn_result)
        if not turn_id:
            raise CodexProtocolError("turn/start succeeded but no turn id found")

        session = _TurnSession(thread_id=active_thread_id, turn_id=turn_id)
        self._turn_sessions[turn_id] = session
        return active_thread_id, session

    async def _prepare_thread_context(
        self,
        *,
        thread_id: str | None,
        metadata: Mapping[str, Any] | None,
        thread_config: ThreadConfig | None,
    ) -> str:
        """Start or resume a thread and return the active thread id."""
        if thread_id is None:
            thread_params = _thread_config_to_params(thread_config)
            if metadata:
                thread_params["metadata"] = dict(metadata)
            thread_result = await self.request(THREAD_START_METHOD, thread_params)
            active_thread_id = _extract_thread_id(thread_result)
            if not active_thread_id:
                raise CodexProtocolError("thread/start succeeded but no thread id found")
            return active_thread_id

        resume_params: dict[str, Any] = {"threadId": thread_id}
        resume_params.update(_thread_config_to_params(thread_config))
        try:
            await self.request(THREAD_RESUME_METHOD, resume_params)
        except CodexProtocolError:
            if self._strict:
                raise
        return thread_id

    def _get_continuation_session(
        self,
        continuation: ChatContinuation,
        *,
        expected_mode: Literal["once", "stream"],
    ) -> _TurnSession:
        if continuation.mode != expected_mode:
            raise CodexProtocolError(
                f"continuation mode mismatch: expected {expected_mode!r}, got {continuation.mode!r}"
            )

        session = self._turn_sessions.get(continuation.turn_id)
        if session is None:
            raise CodexProtocolError("continuation is no longer available in this client instance")

        if session.thread_id != continuation.thread_id:
            raise CodexProtocolError("continuation thread_id does not match active turn session")

        return session

    async def _pump_turn_session(
        self,
        session: _TurnSession,
        *,
        max_wait: float | None,
    ) -> None:
        if session.completed or session.failed:
            return

        loop = asyncio.get_running_loop()
        deadline = None if max_wait is None else (loop.time() + max_wait)

        while not session.completed and not session.failed:
            timeout_value: float | None = None
            if deadline is not None:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    return
                timeout_value = remaining

            try:
                event = await self._receive_turn_event(
                    session.turn_id,
                    inactivity_timeout=timeout_value,
                )
            except asyncio.TimeoutError:
                return

            if _is_transport_error_event(event):
                return

            self._apply_event_to_session(session, event)

    async def _receive_turn_event(
        self,
        turn_id: str,
        *,
        inactivity_timeout: float | None,
    ) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        deadline = None
        if inactivity_timeout is not None:
            deadline = loop.time() + inactivity_timeout

        while True:
            deferred_idx = self._find_deferred_for_turn(turn_id)
            if deferred_idx is not None:
                return self._deferred_notifications.pop(deferred_idx)

            wait_timeout: float | None = None
            if deadline is not None:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise asyncio.TimeoutError
                wait_timeout = remaining

            if wait_timeout is None:
                event = await self._notifications.get()
            else:
                event = await asyncio.wait_for(
                    self._notifications.get(),
                    timeout=wait_timeout,
                )

            if _is_transport_error_event(event) or self._event_is_for_turn(event, turn_id):
                return event

            self._deferred_notifications.append(event)

    async def _await_turn_event_or_timeout(
        self,
        *,
        session: _TurnSession,
        timeout_value: float | None,
        cursor: int,
        mode: Literal["once", "stream"],
    ) -> dict[str, Any]:
        if timeout_value is None:
            return await self._receive_turn_event(
                session.turn_id,
                inactivity_timeout=None,
            )

        try:
            return await self._receive_turn_event(
                session.turn_id,
                inactivity_timeout=timeout_value,
            )
        except asyncio.TimeoutError as exc:
            raise CodexTurnInactiveError(
                f"turn became inactive for {timeout_value:.1f}s",
                continuation=self._make_continuation(
                    session,
                    cursor=cursor,
                    mode=mode,
                ),
                idle_seconds=timeout_value,
            ) from exc

    def _find_deferred_for_turn(self, turn_id: str) -> int | None:
        for idx, event in enumerate(self._deferred_notifications):
            if _is_transport_error_event(event) or self._event_is_for_turn(event, turn_id):
                return idx
        return None

    def _event_is_for_turn(self, event: dict[str, Any], turn_id: str) -> bool:
        if _event_mentions_turn_id(event, turn_id):
            return True

        method = event.get("method")
        if not isinstance(method, str):
            return False

        if not (is_turn_completed(method) or is_turn_failed(method)):
            return False

        params = event.get("params")
        if not isinstance(params, Mapping):
            return True

        has_direct_turn = (
            _find_first_string_by_exact_keys(params, {"turnid", "turn_id"}) is not None
        )
        turn_obj = _find_first_dict_by_exact_key(params, {"turn"})
        has_turn_obj = False
        if turn_obj is not None:
            has_turn_obj = _find_first_string_by_exact_keys(turn_obj, {"id"}) is not None

        return not has_direct_turn and not has_turn_obj

    def _apply_event_to_session(self, session: _TurnSession, event: dict[str, Any]) -> None:
        method = event.get("method")
        if not isinstance(method, str):
            return

        session.raw_events.append(event)
        event_index = len(session.raw_events) - 1

        completed_message = _extract_completed_agent_message(method, event)
        if completed_message is not None:
            item_id, _ = completed_message
            if item_id is None or item_id not in session.completed_item_ids:
                if item_id is not None:
                    session.completed_item_ids.add(item_id)
                session.completed_agent_messages.append(completed_message)

        step = _extract_completed_step(
            method,
            event,
            fallback_thread_id=session.thread_id,
            fallback_turn_id=session.turn_id,
        )
        if step is not None:
            if step.item_id is None or step.item_id not in session.step_item_ids:
                if step.item_id is not None:
                    session.step_item_ids.add(step.item_id)
                session.step_records.append(_StepRecord(event_index=event_index, step=step))

        if is_turn_failed(method):
            details = _find_first_string_by_exact_keys(event, {"message", "error"})
            session.failed = True
            session.failure_message = details or "turn failed"

        if is_turn_completed(method):
            session.completed = True

    def _cleanup_turn_state(self, turn_id: str) -> None:
        self._turn_sessions.pop(turn_id, None)
        self._drop_deferred_for_turn(turn_id)

    def _drop_deferred_for_turn(self, turn_id: str) -> None:
        retained: list[dict[str, Any]] = []
        for event in self._deferred_notifications:
            if self._event_is_for_turn(event, turn_id):
                continue
            retained.append(event)
        self._deferred_notifications = retained

    def _resolve_inactivity_timeout(self, timeout: float | None) -> float | None:
        return timeout if timeout is not None else self._inactivity_timeout

    def _make_continuation(
        self,
        session: _TurnSession,
        *,
        cursor: int,
        mode: Literal["once", "stream"],
    ) -> ChatContinuation:
        return ChatContinuation(
            thread_id=session.thread_id,
            turn_id=session.turn_id,
            cursor=max(0, cursor),
            mode=mode,
        )

    def _start_receiver(self) -> None:
        """Start background receive loop exactly once."""
        if self._receiver_task is not None:
            return
        self._receiver_task = asyncio.create_task(self._receiver_loop())

    async def _receiver_loop(self) -> None:
        """Route incoming transport messages to request futures or notification queue."""
        try:
            while not self._closed:
                payload = await self._transport.recv()

                if is_response_message(payload):
                    response_id = payload.get("id")
                    if isinstance(response_id, int):
                        future = self._pending.pop(response_id, None)
                        if future is not None and not future.done():
                            future.set_result(payload)
                    continue

                method = payload.get("method")
                if not isinstance(method, str):
                    continue

                if "id" in payload and payload.get("id") is not None:
                    request_id = payload["id"]
                    if isinstance(request_id, (int, str)):
                        error_response = make_error_response(
                            request_id,
                            -32601,
                            "Client does not implement server-initiated requests.",
                        )
                        async with self._send_lock:
                            await self._transport.send(error_response)

                await self._notifications.put(payload)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if self._closed:
                return
            transport_error = CodexTransportError(f"receiver loop failed: {exc}")
            for future in list(self._pending.values()):
                if not future.done():
                    future.set_exception(transport_error)
            self._pending.clear()
            await self._notifications.put(
                {
                    "jsonrpc": "2.0",
                    "method": "__transport_error__",
                    "params": {"message": str(exc)},
                }
            )


def _is_unset(value: Any) -> bool:
    return isinstance(value, UnsetType)


def _thread_config_to_params(config: ThreadConfig | None) -> dict[str, Any]:
    """Encode `ThreadConfig` into protocol params (camelCase), omitting UNSET."""
    if config is None:
        return {}

    mapping: tuple[tuple[str, str], ...] = (
        ("cwd", "cwd"),
        ("base_instructions", "baseInstructions"),
        ("developer_instructions", "developerInstructions"),
        ("model", "model"),
        ("model_provider", "modelProvider"),
        ("approval_policy", "approvalPolicy"),
        ("sandbox", "sandbox"),
        ("personality", "personality"),
        ("ephemeral", "ephemeral"),
        ("config", "config"),
    )
    params: dict[str, Any] = {}
    for attr_name, key_name in mapping:
        value = getattr(config, attr_name)
        if _is_unset(value):
            continue
        params[key_name] = value
    return params


def _turn_overrides_to_params(overrides: TurnOverrides | None) -> dict[str, Any]:
    """Encode `TurnOverrides` into protocol params (camelCase), omitting UNSET."""
    if overrides is None:
        return {}

    mapping: tuple[tuple[str, str], ...] = (
        ("cwd", "cwd"),
        ("model", "model"),
        ("effort", "effort"),
        ("summary", "summary"),
        ("sandbox_policy", "sandboxPolicy"),
        ("personality", "personality"),
        ("approval_policy", "approvalPolicy"),
        ("output_schema", "outputSchema"),
    )
    params: dict[str, Any] = {}
    for attr_name, key_name in mapping:
        value = getattr(overrides, attr_name)
        if _is_unset(value):
            continue
        params[key_name] = value
    return params


def _merge_thread_config(base: ThreadConfig, override: ThreadConfig | None) -> ThreadConfig:
    """Merge two thread configs where override values replace non-UNSET base values."""
    if override is None:
        return ThreadConfig(
            cwd=base.cwd,
            base_instructions=base.base_instructions,
            developer_instructions=base.developer_instructions,
            model=base.model,
            model_provider=base.model_provider,
            approval_policy=base.approval_policy,
            sandbox=base.sandbox,
            personality=base.personality,
            ephemeral=base.ephemeral,
            config=base.config,
        )

    def pick(base_value: Any, override_value: Any) -> Any:
        if _is_unset(override_value):
            return base_value
        return override_value

    return ThreadConfig(
        cwd=pick(base.cwd, override.cwd),
        base_instructions=pick(base.base_instructions, override.base_instructions),
        developer_instructions=pick(
            base.developer_instructions, override.developer_instructions
        ),
        model=pick(base.model, override.model),
        model_provider=pick(base.model_provider, override.model_provider),
        approval_policy=pick(base.approval_policy, override.approval_policy),
        sandbox=pick(base.sandbox, override.sandbox),
        personality=pick(base.personality, override.personality),
        ephemeral=pick(base.ephemeral, override.ephemeral),
        config=pick(base.config, override.config),
    )


def _default_stdio_command() -> list[str]:
    """Return default app-server command for stdio mode."""
    from_env = os.getenv("CODEX_APP_SERVER_CMD")
    if from_env:
        return shlex.split(from_env)
    return ["codex", "app-server"]


def _prepare_initialize_params(
    params: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """
    Merge caller-provided initialize params with library default params.

    Merge behavior:
    - start with `_default_initialize_params()`;
    - shallow-merge top-level keys from caller `params`;
    - when caller provides `capabilities` as a mapping and omits
      `optOutNotificationMethods`, inject the default opt-out list;
    - if caller provides explicit `capabilities.optOutNotificationMethods`, keep it.

    :param params: Optional caller initialize payload.
    :return: Final initialize payload sent to the server.
    """
    payload = _default_initialize_params()
    if params is None:
        return payload

    params_dict = dict(params)
    payload.update(params_dict)

    if "capabilities" not in params_dict:
        return payload

    capabilities = payload.get("capabilities")
    if capabilities is None:
        return payload
    if not isinstance(capabilities, Mapping):
        return payload

    capabilities_dict = dict(capabilities)
    if "optOutNotificationMethods" not in capabilities_dict:
        capabilities_dict["optOutNotificationMethods"] = list(DEFAULT_OPT_OUT_NOTIFICATION_METHODS)
    payload["capabilities"] = capabilities_dict
    return payload


def _default_initialize_params() -> dict[str, Any]:
    """
    Build the default initialize payload for Codex app-server.

    The default payload currently includes:
    - `protocolVersion`: protocol version string sent by this client;
    - `clientInfo`: client name/version metadata;
    - `capabilities.optOutNotificationMethods`: compat event opt-outs used by
      default to reduce duplicate/legacy notification streams.

    :return: Default initialize payload map.
    """
    return {
        "protocolVersion": "1",
        "clientInfo": {
            "name": "codex-app-server-client",
            "version": "0.1.0",
        },
        "capabilities": {
            "optOutNotificationMethods": list(DEFAULT_OPT_OUT_NOTIFICATION_METHODS),
        },
    }


def _extract_thread_id(payload: Any) -> str | None:
    """Extract thread id from a nested response payload, best effort."""
    if not isinstance(payload, (dict, list)):
        return None
    direct = _find_first_string_by_exact_keys(payload, {"threadid", "thread_id"})
    if direct:
        return direct
    thread_obj = _find_first_dict_by_exact_key(payload, {"thread"})
    if thread_obj:
        thread_from_obj = _find_first_string_by_exact_keys(thread_obj, {"id"})
        if thread_from_obj:
            return thread_from_obj
    return _find_first_string_by_exact_keys(payload, {"id"})


def _extract_turn_id(payload: Any) -> str | None:
    """Extract turn id from a nested response payload, best effort."""
    if not isinstance(payload, (dict, list)):
        return None
    direct = _find_first_string_by_exact_keys(payload, {"turnid", "turn_id"})
    if direct:
        return direct
    turn_obj = _find_first_dict_by_exact_key(payload, {"turn"})
    if turn_obj:
        turn_from_obj = _find_first_string_by_exact_keys(turn_obj, {"id"})
        if turn_from_obj:
            return turn_from_obj
    return None


def _extract_completed_agent_message(
    method: str,
    payload: dict[str, Any],
) -> tuple[str | None, str] | None:
    """Extract final assistant text from an `item/completed` event."""
    if method != ITEM_COMPLETED_METHOD:
        return None

    params = payload.get("params")
    if not isinstance(params, Mapping):
        return None

    item = params.get("item")
    if not isinstance(item, Mapping):
        return None

    item_type = item.get("type")
    if item_type != "agentMessage":
        return None

    item_id_raw = item.get("id")
    item_id: str | None = item_id_raw if isinstance(item_id_raw, str) else None
    text = _extract_item_text(item)
    if text is None:
        return None
    return item_id, text


def _extract_completed_step(
    method: str,
    payload: dict[str, Any],
    *,
    fallback_thread_id: str | None = None,
    fallback_turn_id: str | None = None,
) -> ConversationStep | None:
    """Build a completed conversation step from an `item/completed` event."""
    if method != ITEM_COMPLETED_METHOD:
        return None

    params = payload.get("params")
    if not isinstance(params, Mapping):
        return None

    item = params.get("item")
    if not isinstance(item, Mapping):
        return None

    thread_id = _find_first_string_by_exact_keys(params, {"threadid", "thread_id"})
    if thread_id is None:
        thread_obj = _find_first_dict_by_exact_key(params, {"thread"})
        if thread_obj is not None:
            thread_id = _find_first_string_by_exact_keys(thread_obj, {"id"})

    turn_id = _find_first_string_by_exact_keys(params, {"turnid", "turn_id"})
    if turn_id is None:
        turn_obj = _find_first_dict_by_exact_key(params, {"turn"})
        if turn_obj is not None:
            turn_id = _find_first_string_by_exact_keys(turn_obj, {"id"})

    if thread_id is None:
        thread_id = fallback_thread_id
    if turn_id is None:
        turn_id = fallback_turn_id

    if not thread_id or not turn_id:
        return None

    return _step_from_item(
        thread_id=thread_id,
        turn_id=turn_id,
        item=dict(item),
        data={"params": dict(params), "item": dict(item)},
    )


def _step_from_item(
    *,
    thread_id: str,
    turn_id: str,
    item: Mapping[str, Any],
    data: Mapping[str, Any] | None = None,
) -> ConversationStep | None:
    """Map raw item payload to a normalized `ConversationStep`."""
    item_type_obj = item.get("type")
    if not isinstance(item_type_obj, str):
        return None

    step_type_map = {
        "reasoning": "thinking",
        "commandExecution": "exec",
        "agentMessage": "codex",
        "mcpToolCall": "tool",
        "fileChange": "file",
    }
    step_type = step_type_map.get(item_type_obj, item_type_obj)

    text = _extract_item_text(item)

    item_id_obj = item.get("id")
    item_id = item_id_obj if isinstance(item_id_obj, str) else None

    payload_data: dict[str, Any] = {}
    if data is not None:
        payload_data.update(dict(data))
    payload_data.setdefault("item", dict(item))

    return ConversationStep(
        thread_id=thread_id,
        turn_id=turn_id,
        item_id=item_id,
        step_type=step_type,
        item_type=item_type_obj,
        text=text,
        data=payload_data,
    )


def _extract_item_text(item: Mapping[str, Any]) -> str | None:
    """Best-effort text extraction from a completed item payload."""
    item_type = item.get("type")

    if item_type == "agentMessage":
        text = item.get("text")
        if isinstance(text, str):
            return text
        content = item.get("content")
        if isinstance(content, str):
            return content

    if item_type == "reasoning":
        parts: list[str] = []
        summary = item.get("summary")
        if isinstance(summary, list):
            parts.extend(str(v) for v in summary if isinstance(v, str))
        content = item.get("content")
        if isinstance(content, list):
            parts.extend(str(v) for v in content if isinstance(v, str))
        if parts:
            return "\n".join(parts)

    if item_type == "commandExecution":
        command = item.get("command")
        if isinstance(command, str) and command:
            return command

    for key in ("text", "message", "content", "summary"):
        value = item.get(key)
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            parts = [v for v in value if isinstance(v, str)]
            if parts:
                return "\n".join(parts)

    return None


def _event_mentions_turn_id(payload: Any, turn_id: str) -> bool:
    """Return True when payload references target turn id."""
    if not isinstance(payload, (dict, list)):
        return False

    direct = _find_first_string_by_exact_keys(payload, {"turnid", "turn_id"})
    if direct == turn_id:
        return True

    turn_obj = _find_first_dict_by_exact_key(payload, {"turn"})
    if turn_obj:
        nested_id = _find_first_string_by_exact_keys(turn_obj, {"id"})
        if nested_id == turn_id:
            return True

    return False


def _find_first_string_by_exact_keys(
    payload: Any,
    keys_lower: set[str],
) -> str | None:
    """Depth-first search for first string whose key matches provided names."""
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            if key.lower() in keys_lower and isinstance(value, str):
                return value
        for value in payload.values():
            found = _find_first_string_by_exact_keys(value, keys_lower)
            if found is not None:
                return found
        return None

    if isinstance(payload, list):
        for item in payload:
            found = _find_first_string_by_exact_keys(item, keys_lower)
            if found is not None:
                return found

    return None


def _find_first_dict_by_exact_key(
    payload: Any,
    keys_lower: set[str],
) -> dict[str, Any] | None:
    """Depth-first search for first dict value under matching key name."""
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            if key.lower() in keys_lower and isinstance(value, Mapping):
                return dict(value)
        for value in payload.values():
            found = _find_first_dict_by_exact_key(value, keys_lower)
            if found is not None:
                return found
        return None

    if isinstance(payload, list):
        for item in payload:
            found = _find_first_dict_by_exact_key(item, keys_lower)
            if found is not None:
                return found

    return None


def _is_transport_error_event(payload: dict[str, Any]) -> bool:
    method = payload.get("method")
    return isinstance(method, str) and method == "__transport_error__"
