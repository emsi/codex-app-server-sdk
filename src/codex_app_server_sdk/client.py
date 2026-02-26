from __future__ import annotations

import asyncio
import contextlib
import os
import shlex
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Coroutine, Literal

from .errors import (
    CodexProtocolError,
    CodexTimeoutError,
    CodexTransportError,
    CodexTurnInactiveError,
)
from .models import (
    ApprovalRequest,
    CancelResult,
    ChatContinuation,
    ChatResult,
    CommandApprovalDecision,
    CommandApprovalRequest,
    CommandApprovalWithExecpolicyAmendment,
    ConversationStep,
    FileChangeApprovalDecision,
    FileChangeApprovalRequest,
    InitializeResult,
    ThreadConfig,
    TurnOverrides,
    UnsetType,
)
from .protocol import (
    COMMAND_EXEC_METHOD,
    CONFIG_BATCH_WRITE_METHOD,
    CONFIG_READ_METHOD,
    CONFIG_REQUIREMENTS_READ_METHOD,
    CONFIG_VALUE_WRITE_METHOD,
    DEFAULT_OPT_OUT_NOTIFICATION_METHODS,
    INITIALIZE_METHOD,
    ITEM_COMMAND_EXECUTION_REQUEST_APPROVAL_METHOD,
    ITEM_COMPLETED_METHOD,
    ITEM_FILE_CHANGE_REQUEST_APPROVAL_METHOD,
    MODEL_LIST_METHOD,
    REVIEW_START_METHOD,
    THREAD_ARCHIVE_METHOD,
    THREAD_COMPACT_START_METHOD,
    THREAD_FORK_METHOD,
    THREAD_LIST_METHOD,
    THREAD_NAME_SET_METHOD,
    THREAD_READ_METHOD,
    THREAD_ROLLBACK_METHOD,
    THREAD_RESUME_METHOD,
    THREAD_START_METHOD,
    THREAD_UNARCHIVE_METHOD,
    TURN_INTERRUPT_METHOD,
    TURN_STEER_METHOD,
    TURN_START_METHOD,
    extract_error,
    is_response_message,
    is_turn_completed,
    is_turn_failed,
    make_error_response,
    make_result_response,
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


_APPROVAL_QUEUE_STOP = object()


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
        """Send one message on this bound thread and return the final assistant output.

        Args:
            text: User text for a new turn. Must be omitted when resuming with
                `continuation`.
            user: Optional user label forwarded on `turn/start`.
            metadata: Optional per-turn metadata forwarded on `turn/start`.
            inactivity_timeout: Optional per-call inactivity timeout override.
                `None` uses the client-level inactivity timeout policy.
            continuation: Continuation token from `CodexTurnInactiveError` for
                resuming the same running turn.
            turn_overrides: Optional per-turn override payload for `turn/start`.

        Returns:
            Buffered final turn result for this thread.

        Raises:
            ValueError: If continuation constraints are violated.
            CodexTurnInactiveError: If the turn remains inactive longer than the
                resolved inactivity timeout.
            CodexProtocolError: If protocol/server reports turn failure.
            CodexTransportError: If transport fails while waiting for turn events.

        Notes:
            When `continuation` is provided, `text`, `user`, `metadata`, and
            `turn_overrides` cannot be provided in the same call.
        """
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
        """Stream completed, non-delta steps for one message on this bound thread.

        Args:
            text: User text for a new turn. Must be omitted when resuming with
                `continuation`.
            user: Optional user label forwarded on `turn/start`.
            metadata: Optional per-turn metadata forwarded on `turn/start`.
            inactivity_timeout: Optional per-call inactivity timeout override.
                `None` uses the client-level inactivity timeout policy.
            continuation: Continuation token from `CodexTurnInactiveError` for
                resuming the same running turn.
            turn_overrides: Optional per-turn override payload for `turn/start`.

        Yields:
            Completed conversation step blocks as they arrive.

        Raises:
            ValueError: If continuation constraints are violated.
            CodexTurnInactiveError: If the turn remains inactive longer than the
                resolved inactivity timeout.
            CodexProtocolError: If protocol/server reports turn failure.
            CodexTransportError: If transport fails while waiting for turn events.

        Notes:
            When `continuation` is provided, `text`, `user`, `metadata`, and
            `turn_overrides` cannot be provided in the same call.
        """
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
        """Fork this thread into a new thread handle.

        Args:
            overrides: Optional thread-level overrides applied to the forked thread.

        Returns:
            New `ThreadHandle` bound to the forked thread id.
        """
        merged = _merge_thread_config(self._defaults, overrides)
        return await self._client.fork_thread(self._thread_id, overrides=merged)

    async def update_defaults(self, overrides: ThreadConfig) -> None:
        """Apply thread-level defaults and update this handle's local snapshot.

        Args:
            overrides: Thread-level fields to apply via `thread/resume`.
        """
        await self._client.set_thread_defaults(self._thread_id, overrides)
        self._defaults = _merge_thread_config(self._defaults, overrides)

    async def read(self, *, include_turns: bool = True) -> Any:
        """Read server-side thread state for this handle's thread.

        Args:
            include_turns: Whether response should include turn history.

        Returns:
            Raw `thread/read` response payload.
        """
        return await self._client.read_thread(self._thread_id, include_turns=include_turns)

    async def set_name(self, name: str) -> None:
        """Set user-facing name for this thread.

        Args:
            name: Thread display name.
        """
        await self._client.set_thread_name(self._thread_id, name)

    async def archive(self) -> None:
        """Archive this thread via `thread/archive`."""
        await self._client.archive_thread(self._thread_id)

    async def unarchive(self) -> None:
        """Unarchive this thread via `thread/unarchive`."""
        await self._client.unarchive_thread(self._thread_id)

    async def compact(self) -> Any:
        """Start context compaction for this thread.

        Returns:
            Raw `thread/compact/start` response payload.
        """
        return await self._client.compact_thread(self._thread_id)

    async def rollback(self, num_turns: int) -> Any:
        """Rollback the last turns for this thread.

        Args:
            num_turns: Number of most recent turns to drop.

        Returns:
            Raw `thread/rollback` response payload.
        """
        return await self._client.rollback_thread(self._thread_id, num_turns=num_turns)

    async def start_review(
        self,
        target: Mapping[str, Any],
        *,
        delivery: Literal["inline", "detached"] | None = None,
    ) -> Any:
        """Start review mode against this thread.

        Args:
            target: Review target payload accepted by `review/start`.
            delivery: Optional review delivery mode.

        Returns:
            Raw `review/start` response payload.
        """
        return await self._client.start_review(
            thread_id=self._thread_id,
            target=target,
            delivery=delivery,
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
        self._approval_requests: asyncio.Queue[ApprovalRequest | object] = asyncio.Queue()
        self._pending_approval_requests: dict[int | str, ApprovalRequest] = {}
        self._approval_handler: (
            Callable[
                [ApprovalRequest],
                Awaitable[CommandApprovalDecision | FileChangeApprovalDecision],
            ]
            | None
        ) = None
        self._background_tasks: set[asyncio.Task[Any]] = set()

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
        """Create an unstarted client configured for stdio transport.

        Args:
            command: Optional command argv. Defaults to `CODEX_APP_SERVER_CMD`
                or `["codex", "app-server"]`.
            cwd: Optional subprocess working directory.
            env: Optional subprocess environment overrides.
            connect_timeout: Subprocess spawn timeout in seconds.
            request_timeout: Default request/response timeout in seconds.
            inactivity_timeout: Default turn inactivity timeout in seconds.
                If `None`, turn waits are unbounded by inactivity.
            strict: Enable strict protocol behavior for ambiguous cases.

        Returns:
            Unstarted `CodexClient` using `StdioTransport`.
        """
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
        """Create an unstarted client configured for websocket transport.

        Args:
            url: Optional websocket URL. Defaults to `CODEX_APP_SERVER_WS_URL`
                or `ws://127.0.0.1:8765`.
            token: Optional bearer token. Defaults to `CODEX_APP_SERVER_TOKEN`.
            headers: Optional extra websocket headers.
            connect_timeout: Websocket handshake timeout in seconds.
            request_timeout: Default request/response timeout in seconds.
            inactivity_timeout: Default turn inactivity timeout in seconds.
                If `None`, turn waits are unbounded by inactivity.
            strict: Enable strict protocol behavior for ambiguous cases.

        Returns:
            Unstarted `CodexClient` using `WebSocketTransport`.
        """
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
        """Connect transport and start the background receiver loop.

        Returns:
            `self` for fluent usage.

        Raises:
            CodexTransportError: If client is closed or transport connection fails.
        """
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
        """Stop receiver, fail pending requests, clear turn state, and close transport."""
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

        for task in list(self._background_tasks):
            task.cancel()
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
        self._background_tasks.clear()

        self._pending_approval_requests.clear()
        self._approval_handler = None

        self._turn_sessions.clear()
        self._deferred_notifications.clear()
        self._approval_requests.put_nowait(_APPROVAL_QUEUE_STOP)

        await self._transport.close()
        self._started = False

    async def initialize(
        self,
        params: Mapping[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> InitializeResult:
        """Perform initialize handshake and cache client initialization state.

        This method is optional for normal chat usage because `chat_once()` and `chat()`
        initialize automatically on first use. Call it explicitly when you want to fail
        fast on handshake issues or inspect server protocol/capabilities metadata.

        Args:
            params: Optional initialize request payload. The payload is merged with
                library defaults via `_prepare_initialize_params()`, including capability
                opt-out defaults when applicable.
            timeout: Optional per-request timeout override in seconds.

        Returns:
            Parsed initialize result containing normalized protocol/server fields
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
        """Send one JSON-RPC request and return its `result`.

        Args:
            method: JSON-RPC method name.
            params: Optional request parameters.
            timeout: Optional per-call timeout override in seconds. If omitted,
                client default `request_timeout` is used.

        Returns:
            JSON-RPC `result` payload.

        Raises:
            CodexTransportError: If client is closed or transport fails.
            CodexTimeoutError: If no response arrives within timeout.
            CodexProtocolError: If response contains JSON-RPC error payload.
        """
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

    def set_approval_handler(
        self,
        handler: (
            Callable[
                [ApprovalRequest],
                Awaitable[CommandApprovalDecision | FileChangeApprovalDecision],
            ]
            | None
        ),
    ) -> None:
        """Set or clear async handler for v2 approval requests.

        The handler is invoked for:
        - `item/commandExecution/requestApproval`
        - `item/fileChange/requestApproval`

        If no handler is configured, requests are auto-declined.
        """
        self._approval_handler = handler

    async def approval_requests(self) -> AsyncIterator[ApprovalRequest]:
        """Yield parsed approval requests from the server.

        This stream is observational; automatic callback handling (or auto-decline
        default) still applies.
        """
        while True:
            item = await self._approval_requests.get()
            if item is _APPROVAL_QUEUE_STOP:
                self._approval_requests.put_nowait(_APPROVAL_QUEUE_STOP)
                return
            if isinstance(item, (CommandApprovalRequest, FileChangeApprovalRequest)):
                yield item

    async def respond_approval(
        self,
        request: ApprovalRequest,
        decision: CommandApprovalDecision | FileChangeApprovalDecision,
    ) -> None:
        """Respond to one pending approval request."""
        pending = self._pending_approval_requests.get(request.request_id)
        if pending is None:
            raise CodexProtocolError("approval request is no longer pending")

        if type(pending) is not type(request):
            raise CodexProtocolError("approval request type mismatch")

        self._pending_approval_requests.pop(request.request_id, None)
        result_payload = _encode_approval_result(request, decision)
        response = make_result_response(request.request_id, result_payload)
        async with self._send_lock:
            await self._transport.send(response)

    async def approve_approval(
        self,
        request: ApprovalRequest,
        *,
        for_session: bool = False,
        execpolicy_amendment: Sequence[str] | None = None,
    ) -> None:
        """Convenience helper to approve an approval request."""
        if isinstance(request, FileChangeApprovalRequest):
            if execpolicy_amendment is not None:
                raise ValueError(
                    "execpolicy_amendment is not applicable to file-change approvals"
                )
            decision: FileChangeApprovalDecision = (
                "accept_for_session" if for_session else "accept"
            )
            await self.respond_approval(request, decision)
            return

        if execpolicy_amendment is not None:
            decision_cmd: CommandApprovalDecision = CommandApprovalWithExecpolicyAmendment(
                execpolicy_amendment=list(execpolicy_amendment)
            )
        else:
            decision_cmd = "accept_for_session" if for_session else "accept"
        await self.respond_approval(request, decision_cmd)

    async def decline_approval(self, request: ApprovalRequest) -> None:
        """Convenience helper to decline an approval request and continue turn."""
        await self.respond_approval(request, "decline")

    async def cancel_approval(self, request: ApprovalRequest) -> None:
        """Convenience helper to decline an approval request and cancel turn."""
        await self.respond_approval(request, "cancel")

    async def start_thread(self, config: ThreadConfig | None = None) -> ThreadHandle:
        """Create a new thread and return a bound handle.

        Args:
            config: Optional thread-level configuration for `thread/start`.

        Returns:
            `ThreadHandle` bound to created thread id.

        Raises:
            CodexProtocolError: If server response lacks thread id.
        """
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
        """Resume an existing thread and return a bound handle.

        Args:
            thread_id: Existing thread id to resume.
            overrides: Optional thread-level overrides applied on resume.

        Returns:
            `ThreadHandle` bound to resumed thread id.
        """
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
        """Fork an existing thread and return a handle for the fork.

        Args:
            thread_id: Source thread id to fork from.
            overrides: Optional thread-level overrides for the forked thread.

        Returns:
            `ThreadHandle` bound to forked thread id.

        Raises:
            CodexProtocolError: If server response lacks forked thread id.
        """
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
        """Apply thread-level defaults to an existing thread.

        Args:
            thread_id: Target thread id.
            overrides: Thread-level fields applied through `thread/resume`.
        """
        if not self._initialized:
            await self.initialize()
        params: dict[str, Any] = {"threadId": thread_id}
        params.update(_thread_config_to_params(overrides))
        await self.request(THREAD_RESUME_METHOD, params)

    async def read_thread(self, thread_id: str, *, include_turns: bool = True) -> Any:
        """Read server-side thread state.

        Args:
            thread_id: Target thread id.
            include_turns: Whether returned thread payload should include turns.

        Returns:
            Raw `thread/read` response payload.
        """
        return await self.request(
            THREAD_READ_METHOD,
            {"threadId": thread_id, "includeTurns": include_turns},
        )

    async def list_threads(
        self,
        *,
        archived: bool | None = None,
        cursor: str | None = None,
        cwd: str | None = None,
        limit: int | None = None,
        model_providers: Sequence[str] | None = None,
        sort_key: Literal["created_at", "updated_at"] | None = None,
        sort_direction: Literal["asc", "desc"] | None = None,
    ) -> Any:
        """List threads with optional filters and pagination.

        Args:
            archived: Optional archived-state filter.
            cursor: Optional pagination cursor.
            cwd: Optional working-directory filter.
            limit: Optional page size.
            model_providers: Optional model provider filter list.
            sort_key: Optional sort key (`created_at` or `updated_at`).
            sort_direction: Optional sort direction (`asc` or `desc`).

        Returns:
            Raw `thread/list` response payload.
        """
        params = _filter_none(
            {
                "archived": archived,
                "cursor": cursor,
                "cwd": cwd,
                "limit": limit,
                "modelProviders": list(model_providers)
                if model_providers is not None
                else None,
                "sortKey": sort_key,
                "sortDirection": sort_direction,
            }
        )
        return await self.request(THREAD_LIST_METHOD, params)

    async def set_thread_name(self, thread_id: str, name: str) -> None:
        """Set user-facing thread name.

        Args:
            thread_id: Target thread id.
            name: Thread display name.
        """
        await self.request(THREAD_NAME_SET_METHOD, {"threadId": thread_id, "name": name})

    async def archive_thread(self, thread_id: str) -> None:
        """Archive a thread.

        Args:
            thread_id: Target thread id.
        """
        await self.request(THREAD_ARCHIVE_METHOD, {"threadId": thread_id})

    async def unarchive_thread(self, thread_id: str) -> None:
        """Unarchive a thread.

        Args:
            thread_id: Target thread id.
        """
        await self.request(THREAD_UNARCHIVE_METHOD, {"threadId": thread_id})

    async def compact_thread(self, thread_id: str) -> Any:
        """Start compaction for thread history.

        Args:
            thread_id: Target thread id.

        Returns:
            Raw `thread/compact/start` response payload.
        """
        return await self.request(THREAD_COMPACT_START_METHOD, {"threadId": thread_id})

    async def rollback_thread(self, thread_id: str, *, num_turns: int) -> Any:
        """Drop the most recent turns from thread history.

        Args:
            thread_id: Target thread id.
            num_turns: Number of latest turns to remove.

        Returns:
            Raw `thread/rollback` response payload.
        """
        return await self.request(
            THREAD_ROLLBACK_METHOD,
            {"threadId": thread_id, "numTurns": num_turns},
        )

    async def list_models(
        self,
        *,
        cursor: str | None = None,
        include_hidden: bool | None = None,
        limit: int | None = None,
    ) -> Any:
        """List available models.

        Args:
            cursor: Optional pagination cursor.
            include_hidden: Optional hidden-model inclusion flag.
            limit: Optional page size.

        Returns:
            Raw `model/list` response payload.
        """
        params = _filter_none(
            {
                "cursor": cursor,
                "includeHidden": include_hidden,
                "limit": limit,
            }
        )
        return await self.request(MODEL_LIST_METHOD, params)

    async def steer_turn(
        self,
        *,
        thread_id: str,
        expected_turn_id: str,
        input_items: Sequence[Mapping[str, Any]],
    ) -> Any:
        """Steer an active turn with additional input items.

        Args:
            thread_id: Target thread id.
            expected_turn_id: Running turn id expected by server.
            input_items: Additional input items forwarded as `input`.

        Returns:
            Raw `turn/steer` response payload.
        """
        return await self.request(
            TURN_STEER_METHOD,
            {
                "threadId": thread_id,
                "expectedTurnId": expected_turn_id,
                "input": [dict(item) for item in input_items],
            },
        )

    async def start_review(
        self,
        *,
        thread_id: str,
        target: Mapping[str, Any],
        delivery: Literal["inline", "detached"] | None = None,
    ) -> Any:
        """Start review mode for a thread.

        Args:
            thread_id: Target thread id.
            target: Review target payload.
            delivery: Optional delivery mode (`inline` or `detached`).

        Returns:
            Raw `review/start` response payload.
        """
        params = {
            "threadId": thread_id,
            "target": dict(target),
        }
        if delivery is not None:
            params["delivery"] = delivery
        return await self.request(REVIEW_START_METHOD, params)

    async def exec_command(
        self,
        command: Sequence[str],
        *,
        cwd: str | None = None,
        sandbox_policy: Mapping[str, Any] | None = None,
        timeout_ms: int | None = None,
    ) -> Any:
        """Execute one command through `command/exec`.

        Args:
            command: Command argv list.
            cwd: Optional command working directory.
            sandbox_policy: Optional sandbox policy payload.
            timeout_ms: Optional command timeout in milliseconds.

        Returns:
            Raw `command/exec` response payload.
        """
        params: dict[str, Any] = {"command": list(command)}
        if cwd is not None:
            params["cwd"] = cwd
        if sandbox_policy is not None:
            params["sandboxPolicy"] = dict(sandbox_policy)
        if timeout_ms is not None:
            params["timeoutMs"] = timeout_ms
        return await self.request(COMMAND_EXEC_METHOD, params)

    async def read_config(
        self,
        *,
        cwd: str | None = None,
        include_layers: bool = False,
    ) -> Any:
        """Read effective config and optional config layers.

        Args:
            cwd: Optional working directory used for config resolution.
            include_layers: Include per-layer config data when true.

        Returns:
            Raw `config/read` response payload.
        """
        params: dict[str, Any] = {"includeLayers": include_layers}
        if cwd is not None:
            params["cwd"] = cwd
        return await self.request(CONFIG_READ_METHOD, params)

    async def read_config_requirements(self) -> Any:
        """Read config requirements/constraints.

        Returns:
            Raw `configRequirements/read` response payload.
        """
        return await self.request(CONFIG_REQUIREMENTS_READ_METHOD)

    async def write_config_value(
        self,
        *,
        key_path: str,
        value: Any,
        merge_strategy: Literal["replace", "upsert"] = "upsert",
        expected_version: str | None = None,
        file_path: str | None = None,
    ) -> Any:
        """Write one config key path.

        Args:
            key_path: Dot-path key to write.
            value: Value to write.
            merge_strategy: Merge behavior (`replace` or `upsert`).
            expected_version: Optional optimistic-lock version.
            file_path: Optional target config file path.

        Returns:
            Raw `config/value/write` response payload.
        """
        params: dict[str, Any] = {
            "keyPath": key_path,
            "mergeStrategy": merge_strategy,
            "value": value,
        }
        if expected_version is not None:
            params["expectedVersion"] = expected_version
        if file_path is not None:
            params["filePath"] = file_path
        return await self.request(CONFIG_VALUE_WRITE_METHOD, params)

    async def batch_write_config(
        self,
        edits: Sequence[Mapping[str, Any]],
        *,
        expected_version: str | None = None,
        file_path: str | None = None,
    ) -> Any:
        """Write multiple config edits atomically.

        Args:
            edits: Sequence of edit payload objects.
            expected_version: Optional optimistic-lock version.
            file_path: Optional target config file path.

        Returns:
            Raw `config/batchWrite` response payload.
        """
        params: dict[str, Any] = {"edits": [dict(edit) for edit in edits]}
        if expected_version is not None:
            params["expectedVersion"] = expected_version
        if file_path is not None:
            params["filePath"] = file_path
        return await self.request(CONFIG_BATCH_WRITE_METHOD, params)

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

        Args:
            text: User text for a new turn. Must be omitted when resuming with
                `continuation`.
            thread_id: Existing thread id for the turn. If omitted, a new
                thread is started.
            user: Optional user label forwarded on `turn/start`.
            metadata: Optional per-turn metadata forwarded on `turn/start`.
            thread_config: Optional thread-level overrides applied to thread
                start/resume context.
            turn_overrides: Optional per-turn override payload forwarded on
                `turn/start`.
            inactivity_timeout: Optional per-call inactivity timeout override in
                seconds. `None` uses client default; if resolved value is `None`,
                wait is unbounded by inactivity.
            continuation: Continuation token from `CodexTurnInactiveError` for
                resuming the same running turn.

        Returns:
            `ChatResult` with final assistant text and raw consumed events.

        Raises:
            ValueError: If required inputs are missing or continuation
                constraints are violated.
            CodexTurnInactiveError: If no matching turn events arrive before
                timeout. Includes resumable continuation token.
            CodexProtocolError: If turn fails or completion cannot be resolved.
            CodexTransportError: If transport fails while receiving events.

        Notes:
            When `continuation` is provided, `text`, `thread_id`, `user`,
            `metadata`, `thread_config`, and `turn_overrides` cannot be
            provided in the same call.
        """
        if continuation is not None:
            if text is not None:
                raise ValueError("text must be omitted when continuation is provided")
            if thread_id is not None:
                raise ValueError("thread_id cannot be used with continuation")
            if user is not None:
                raise ValueError("user cannot be used with continuation")
            if metadata is not None:
                raise ValueError("metadata cannot be used with continuation")
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

        Args:
            text: User text for a new turn. Must be omitted when resuming with
                `continuation`.
            thread_id: Existing thread id for the turn. If omitted, a new
                thread is started.
            user: Optional user label forwarded on `turn/start`.
            metadata: Optional per-turn metadata forwarded on `turn/start`.
            thread_config: Optional thread-level overrides applied to thread
                start/resume context.
            turn_overrides: Optional per-turn override payload forwarded on
                `turn/start`.
            inactivity_timeout: Optional per-call inactivity timeout override in
                seconds. `None` uses client default; if resolved value is `None`,
                wait is unbounded by inactivity.
            continuation: Continuation token from `CodexTurnInactiveError` for
                resuming the same running turn.

        Yields:
            Completed non-delta step blocks (`ConversationStep`), sourced from
            live `item/completed` notifications.

        Raises:
            ValueError: If required inputs are missing or continuation
                constraints are violated.
            CodexTurnInactiveError: If no matching turn events arrive before
                timeout. Includes resumable continuation token.
            CodexProtocolError: If turn fails.
            CodexTransportError: If transport fails while receiving events.

        Notes:
            Streaming is live-notification based and intentionally does not
            backfill from `thread/read` snapshots for the same turn.

            When `continuation` is provided, `text`, `thread_id`, `user`,
            `metadata`, `thread_config`, and `turn_overrides` cannot be
            provided in the same call.
        """
        if continuation is not None:
            if text is not None:
                raise ValueError("text must be omitted when continuation is provided")
            if thread_id is not None:
                raise ValueError("thread_id cannot be used with continuation")
            if user is not None:
                raise ValueError("user cannot be used with continuation")
            if metadata is not None:
                raise ValueError("metadata cannot be used with continuation")
            if thread_config is not None:
                raise ValueError("thread_config cannot be used with continuation")
            if turn_overrides is not None:
                raise ValueError("turn_overrides cannot be used with continuation")
            session = self._get_continuation_session(
                continuation,
                expected_mode="stream",
            )
            cursor = max(0, continuation.cursor)
        else:
            if text is None:
                raise ValueError("text is required when continuation is not provided")
            _, session = await self._start_chat_turn(
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
        """Interrupt a running turn and return unread data since continuation cursor.

        Args:
            continuation: Continuation token for the active turn session.
            timeout: Optional timeout for interrupt/drain wait. Defaults to
                client request timeout.

        Returns:
            `CancelResult` containing unread steps/events and terminal flags.

        Notes:
            Internal turn state is cleaned after cancel so the thread can be
            reused for new turns.
        """
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
        """Send best-effort `turn/interrupt` for a running turn.

        Args:
            turn_id: Running turn id to interrupt.
            timeout: Optional per-request timeout override in seconds.
        """
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
        thread_config: ThreadConfig | None,
    ) -> str:
        """Start or resume a thread and return the active thread id."""
        if thread_id is None:
            thread_params = _thread_config_to_params(thread_config)
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
        for request_id, request in list(self._pending_approval_requests.items()):
            if request.turn_id == turn_id:
                self._pending_approval_requests.pop(request_id, None)
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
                        handled = await self._handle_server_request(
                            request_id=request_id,
                            method=method,
                            payload=payload,
                        )
                        if handled:
                            await self._notifications.put(payload)
                            continue
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

    async def _handle_server_request(
        self,
        *,
        request_id: int | str,
        method: str,
        payload: dict[str, Any],
    ) -> bool:
        if method not in {
            ITEM_COMMAND_EXECUTION_REQUEST_APPROVAL_METHOD,
            ITEM_FILE_CHANGE_REQUEST_APPROVAL_METHOD,
        }:
            return False

        params = payload.get("params")
        if not isinstance(params, Mapping):
            error = make_error_response(
                request_id,
                -32602,
                f"{method} received invalid params",
            )
            async with self._send_lock:
                await self._transport.send(error)
            return True

        try:
            request = _parse_approval_request(
                request_id=request_id,
                method=method,
                params=params,
            )
        except CodexProtocolError as exc:
            error = make_error_response(request_id, -32602, str(exc))
            async with self._send_lock:
                await self._transport.send(error)
            return True

        self._pending_approval_requests[request_id] = request
        await self._approval_requests.put(request)

        if self._approval_handler is None:
            self._spawn_background_task(self._auto_decline_approval(request))
        else:
            self._spawn_background_task(self._run_approval_handler(request))
        return True

    def _spawn_background_task(self, coro: Coroutine[Any, Any, Any]) -> None:
        task: asyncio.Task[Any] = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _auto_decline_approval(self, request: ApprovalRequest) -> None:
        with contextlib.suppress(CodexProtocolError, CodexTransportError):
            await self.respond_approval(request, "decline")

    async def _run_approval_handler(self, request: ApprovalRequest) -> None:
        handler = self._approval_handler
        if handler is None:
            await self._auto_decline_approval(request)
            return

        decision: CommandApprovalDecision | FileChangeApprovalDecision
        try:
            decision = await handler(request)
        except Exception:
            decision = "decline"

        try:
            await self.respond_approval(request, decision)
        except ValueError:
            with contextlib.suppress(CodexProtocolError, CodexTransportError):
                await self.respond_approval(request, "decline")
        except CodexProtocolError:
            # Already handled (for example by explicit caller response).
            return
        except CodexTransportError:
            return


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


def _filter_none(values: Mapping[str, Any]) -> dict[str, Any]:
    """Return dict without keys whose values are None."""
    return {key: value for key, value in values.items() if value is not None}


def _default_stdio_command() -> list[str]:
    """Return default app-server command for stdio mode."""
    from_env = os.getenv("CODEX_APP_SERVER_CMD")
    if from_env:
        return shlex.split(from_env)
    return ["codex", "app-server"]


def _prepare_initialize_params(
    params: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Merge caller-provided initialize params with library defaults.

    Merge behavior:
    - start with `_default_initialize_params()`;
    - shallow-merge top-level keys from caller `params`;
    - when caller provides `capabilities` as a mapping and omits
      `optOutNotificationMethods`, inject the default opt-out list;
    - if caller provides explicit `capabilities.optOutNotificationMethods`, keep it.

    Args:
        params: Optional caller initialize payload.

    Returns:
        Final initialize payload sent to the server.
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
    """Build the default initialize payload for Codex app-server.

    The default payload currently includes:
    - `protocolVersion`: protocol version string sent by this client;
    - `clientInfo`: client name/version metadata;
    - `capabilities.optOutNotificationMethods`: compat event opt-outs used by
      default to reduce duplicate/legacy notification streams.

    Returns:
        Default initialize payload map.
    """
    return {
        "protocolVersion": "1",
        "clientInfo": {
            "name": "codex-app-server-sdk",
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


def _parse_approval_request(
    *,
    request_id: int | str,
    method: str,
    params: Mapping[str, Any],
) -> ApprovalRequest:
    if method == ITEM_COMMAND_EXECUTION_REQUEST_APPROVAL_METHOD:
        command_actions_value = params.get("commandActions")
        command_actions: list[dict[str, Any]] | None = None
        if isinstance(command_actions_value, list):
            command_actions = [
                dict(action) for action in command_actions_value if isinstance(action, Mapping)
            ]

        amendment_value = params.get("proposedExecpolicyAmendment")
        proposed_execpolicy_amendment: list[str] | None = None
        if isinstance(amendment_value, list):
            proposed_execpolicy_amendment = [
                token for token in amendment_value if isinstance(token, str)
            ]

        return CommandApprovalRequest(
            request_id=request_id,
            thread_id=_require_string_field(params, "threadId", method),
            turn_id=_require_string_field(params, "turnId", method),
            item_id=_require_string_field(params, "itemId", method),
            approval_id=_optional_string(params.get("approvalId")),
            reason=_optional_string(params.get("reason")),
            command=_optional_string(params.get("command")),
            cwd=_optional_string(params.get("cwd")),
            command_actions=command_actions,
            proposed_execpolicy_amendment=proposed_execpolicy_amendment,
        )

    if method == ITEM_FILE_CHANGE_REQUEST_APPROVAL_METHOD:
        return FileChangeApprovalRequest(
            request_id=request_id,
            thread_id=_require_string_field(params, "threadId", method),
            turn_id=_require_string_field(params, "turnId", method),
            item_id=_require_string_field(params, "itemId", method),
            grant_root=_optional_string(params.get("grantRoot")),
            reason=_optional_string(params.get("reason")),
        )

    raise CodexProtocolError(f"unsupported server request method: {method}")


def _encode_approval_result(
    request: ApprovalRequest,
    decision: CommandApprovalDecision | FileChangeApprovalDecision,
) -> dict[str, Any]:
    if isinstance(request, CommandApprovalRequest):
        if isinstance(decision, CommandApprovalWithExecpolicyAmendment):
            encoded_decision: Any = {
                "acceptWithExecpolicyAmendment": {
                    "execpolicy_amendment": list(decision.execpolicy_amendment),
                }
            }
        else:
            encoded_decision = _encode_simple_approval_decision(decision)
        return {"decision": encoded_decision}

    if isinstance(decision, CommandApprovalWithExecpolicyAmendment):
        raise ValueError("execpolicy amendment decision is invalid for file-change approvals")

    return {"decision": _encode_simple_approval_decision(decision)}


def _encode_simple_approval_decision(
    decision: str,
) -> str:
    mapping = {
        "accept": "accept",
        "accept_for_session": "acceptForSession",
        "decline": "decline",
        "cancel": "cancel",
    }
    mapped = mapping.get(decision)
    if mapped is None:
        raise ValueError(f"unsupported approval decision: {decision!r}")
    return mapped


def _require_string_field(params: Mapping[str, Any], key: str, method: str) -> str:
    value = params.get(key)
    if not isinstance(value, str) or not value:
        raise CodexProtocolError(f"{method} missing required string field: {key}")
    return value


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) else None


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
