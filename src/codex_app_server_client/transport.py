from __future__ import annotations

import asyncio
import json
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from typing import Any

from .errors import CodexTransportError

try:
    import websockets
except Exception:  # pragma: no cover
    websockets = None


class Transport(ABC):
    @abstractmethod
    async def connect(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def send(self, payload: Mapping[str, Any]) -> None:
        raise NotImplementedError

    @abstractmethod
    async def recv(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        raise NotImplementedError


class StdioTransport(Transport):
    def __init__(
        self,
        command: Sequence[str],
        *,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        connect_timeout: float = 30.0,
    ) -> None:
        if not command:
            raise ValueError("stdio command must not be empty")
        self._command = list(command)
        self._cwd = cwd
        self._env = dict(env) if env is not None else None
        self._connect_timeout = connect_timeout
        self._proc: asyncio.subprocess.Process | None = None

    async def connect(self) -> None:
        if self._proc is not None:
            return
        try:
            self._proc = await asyncio.wait_for(
                asyncio.create_subprocess_exec(
                    *self._command,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                    cwd=self._cwd,
                    env=self._env,
                ),
                timeout=self._connect_timeout,
            )
        except Exception as exc:  # pragma: no cover
            raise CodexTransportError(
                f"failed to start stdio transport command: {self._command!r}"
            ) from exc

    async def send(self, payload: Mapping[str, Any]) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise CodexTransportError("stdio transport is not connected")
        line = json.dumps(dict(payload), separators=(",", ":")) + "\n"
        try:
            self._proc.stdin.write(line.encode("utf-8"))
            await self._proc.stdin.drain()
        except Exception as exc:
            raise CodexTransportError("failed writing to stdio transport") from exc

    async def recv(self) -> dict[str, Any]:
        if self._proc is None or self._proc.stdout is None:
            raise CodexTransportError("stdio transport is not connected")
        try:
            line = await self._proc.stdout.readline()
        except Exception as exc:
            raise CodexTransportError("failed reading from stdio transport") from exc
        if not line:
            raise CodexTransportError("stdio transport closed")
        try:
            return json.loads(line.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise CodexTransportError("received invalid JSON from stdio transport") from exc

    async def close(self) -> None:
        if self._proc is None:
            return

        proc = self._proc
        self._proc = None

        if proc.stdin is not None:
            proc.stdin.close()

        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()


class WebSocketTransport(Transport):
    def __init__(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        connect_timeout: float = 30.0,
    ) -> None:
        self._url = url
        self._headers = dict(headers) if headers is not None else None
        self._connect_timeout = connect_timeout
        self._socket: Any = None

    async def connect(self) -> None:
        if self._socket is not None:
            return
        if websockets is None:  # pragma: no cover
            raise CodexTransportError(
                "websockets dependency is unavailable; install package extras"
            )
        try:
            self._socket = await asyncio.wait_for(
                websockets.connect(
                    self._url,
                    extra_headers=self._headers,
                ),
                timeout=self._connect_timeout,
            )
        except Exception as exc:
            raise CodexTransportError(
                f"failed to connect websocket transport: {self._url}"
            ) from exc

    async def send(self, payload: Mapping[str, Any]) -> None:
        if self._socket is None:
            raise CodexTransportError("websocket transport is not connected")
        try:
            await self._socket.send(json.dumps(dict(payload), separators=(",", ":")))
        except Exception as exc:
            raise CodexTransportError("failed writing to websocket transport") from exc

    async def recv(self) -> dict[str, Any]:
        if self._socket is None:
            raise CodexTransportError("websocket transport is not connected")
        try:
            message = await self._socket.recv()
        except Exception as exc:
            raise CodexTransportError("failed reading from websocket transport") from exc

        if isinstance(message, (bytes, bytearray)):
            text = message.decode("utf-8")
        else:
            text = str(message)
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise CodexTransportError(
                "received invalid JSON from websocket transport"
            ) from exc

    async def close(self) -> None:
        if self._socket is None:
            return
        socket = self._socket
        self._socket = None
        try:
            await socket.close()
        except Exception:
            pass
