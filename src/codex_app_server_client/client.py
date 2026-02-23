from __future__ import annotations

import asyncio
import contextlib
import os
import shlex
from collections.abc import Mapping, Sequence
from typing import Any

from .errors import CodexProtocolError, CodexTimeoutError, CodexTransportError
from .models import ChatResult, InitializeResult
from .protocol import (
    INITIALIZE_METHOD,
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


class CodexClient:
    def __init__(
        self,
        transport: Transport,
        *,
        request_timeout: float = 30.0,
        turn_timeout: float = 180.0,
        strict: bool = False,
    ) -> None:
        self._transport = transport
        self._request_timeout = request_timeout
        self._turn_timeout = turn_timeout
        self._strict = strict
        self._initialized = False

        self._next_request_id = 1
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._notifications: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._send_lock = asyncio.Lock()
        self._receiver_task: asyncio.Task[None] | None = None
        self._closed = False

    @classmethod
    async def connect_stdio(
        cls,
        *,
        command: Sequence[str] | None = None,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        connect_timeout: float = 30.0,
        request_timeout: float = 30.0,
        turn_timeout: float = 180.0,
        strict: bool = False,
    ) -> CodexClient:
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
            turn_timeout=turn_timeout,
            strict=strict,
        )
        await client.start()
        return client

    @classmethod
    async def connect_websocket(
        cls,
        *,
        url: str | None = None,
        token: str | None = None,
        headers: Mapping[str, str] | None = None,
        connect_timeout: float = 30.0,
        request_timeout: float = 30.0,
        turn_timeout: float = 180.0,
        strict: bool = False,
    ) -> CodexClient:
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
            turn_timeout=turn_timeout,
            strict=strict,
        )
        await client.start()
        return client

    async def start(self) -> CodexClient:
        await self._transport.connect()
        self._start_receiver()
        return self

    async def __aenter__(self) -> CodexClient:
        return await self.start()

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.close()

    async def close(self) -> None:
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

        await self._transport.close()

    async def initialize(
        self,
        params: Mapping[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> InitializeResult:
        payload = dict(params) if params is not None else _default_initialize_params()
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

    async def chat_once(
        self,
        text: str,
        thread_id: str | None = None,
        *,
        user: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        timeout: float | None = None,
    ) -> ChatResult:
        if not self._initialized:
            await self.initialize()

        active_thread_id = thread_id
        if active_thread_id is None:
            thread_result = await self.request(
                THREAD_START_METHOD,
                {"metadata": dict(metadata)} if metadata else {},
            )
            active_thread_id = _extract_thread_id(thread_result)
            if not active_thread_id:
                raise CodexProtocolError("thread/start succeeded but no thread id found")
        else:
            try:
                await self.request(THREAD_RESUME_METHOD, {"threadId": active_thread_id})
            except CodexProtocolError:
                if self._strict:
                    raise

        turn_params: dict[str, Any] = {
            "threadId": active_thread_id,
            "input": [{"type": "text", "text": text}],
        }
        if user:
            turn_params["user"] = user
        if metadata:
            turn_params["metadata"] = dict(metadata)

        turn_result = await self.request(TURN_START_METHOD, turn_params)
        turn_id = _extract_turn_id(turn_result)
        events, fragments = await self._collect_turn_events(
            turn_id=turn_id,
            timeout=timeout if timeout is not None else self._turn_timeout,
        )

        final_text = "".join(fragments).strip()
        if not final_text:
            final_text = "".join(_extract_text_fragments(turn_result)).strip()

        return ChatResult(
            thread_id=active_thread_id,
            turn_id=turn_id or "",
            final_text=final_text,
            raw_events=events,
        )

    async def interrupt_turn(self, turn_id: str, *, timeout: float | None = None) -> None:
        await self.request(
            TURN_INTERRUPT_METHOD,
            {"turnId": turn_id},
            timeout=timeout,
        )

    def _start_receiver(self) -> None:
        if self._receiver_task is not None:
            return
        self._receiver_task = asyncio.create_task(self._receiver_loop())

    async def _receiver_loop(self) -> None:
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

    async def _collect_turn_events(
        self,
        *,
        turn_id: str | None,
        timeout: float,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        raw_events: list[dict[str, Any]] = []
        fragments: list[str] = []

        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise CodexTimeoutError(
                    f"turn completion not observed within {timeout:.1f}s"
                )

            event = await asyncio.wait_for(self._notifications.get(), timeout=remaining)
            method = event.get("method")
            if not isinstance(method, str):
                continue

            if method == "__transport_error__":
                message = _find_first_string_by_exact_keys(event, {"message"}) or "transport failed"
                raise CodexTransportError(message)

            matches_turn = True
            if turn_id:
                matches_turn = _event_mentions_turn_id(event, turn_id)
            if turn_id and not matches_turn and not is_turn_completed(method):
                continue

            raw_events.append(event)

            if is_turn_failed(method):
                details = _find_first_string_by_exact_keys(event, {"message", "error"})
                raise CodexProtocolError(details or "turn failed")

            fragments.extend(_extract_text_fragments(event))

            if is_turn_completed(method):
                if turn_id and not matches_turn and self._strict:
                    continue
                return raw_events, fragments


def _default_stdio_command() -> list[str]:
    from_env = os.getenv("CODEX_APP_SERVER_CMD")
    if from_env:
        return shlex.split(from_env)
    return ["codex", "app-server"]


def _default_initialize_params() -> dict[str, Any]:
    return {
        "protocolVersion": "1",
        "clientInfo": {
            "name": "codex-app-server-client",
            "version": "0.1.0",
        },
        "capabilities": {},
    }


def _extract_thread_id(payload: Any) -> str | None:
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


def _find_first_string_by_exact_keys(
    payload: Any,
    keys_lower: set[str],
) -> str | None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key.lower() in keys_lower and isinstance(value, str):
                return value
        for value in payload.values():
            found = _find_first_string_by_exact_keys(value, keys_lower)
            if found:
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = _find_first_string_by_exact_keys(item, keys_lower)
            if found:
                return found
    return None


def _find_first_dict_by_exact_key(
    payload: Any,
    keys_lower: set[str],
) -> dict[str, Any] | None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key.lower() in keys_lower and isinstance(value, dict):
                return value
        for value in payload.values():
            found = _find_first_dict_by_exact_key(value, keys_lower)
            if found:
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = _find_first_dict_by_exact_key(item, keys_lower)
            if found:
                return found
    return None


def _event_mentions_turn_id(event: dict[str, Any], turn_id: str) -> bool:
    return _payload_mentions_turn_id(event.get("params"), turn_id)


def _payload_mentions_turn_id(payload: Any, turn_id: str) -> bool:
    if isinstance(payload, dict):
        for key, value in payload.items():
            key_lower = key.lower()
            if "turn" in key_lower and isinstance(value, str) and value == turn_id:
                return True
            if _payload_mentions_turn_id(value, turn_id):
                return True
    elif isinstance(payload, list):
        for item in payload:
            if _payload_mentions_turn_id(item, turn_id):
                return True
    return False


def _extract_text_fragments(payload: Any) -> list[str]:
    fragments: list[str] = []
    _collect_text(payload, fragments)
    return fragments


def _collect_text(payload: Any, out: list[str]) -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            key_lower = key.lower()
            if isinstance(value, str) and (
                key_lower in {"delta", "text", "content", "message", "finaltext", "final_text"}
                or "text" in key_lower
                or "delta" in key_lower
            ):
                out.append(value)
            else:
                _collect_text(value, out)
    elif isinstance(payload, list):
        for item in payload:
            _collect_text(item, out)
