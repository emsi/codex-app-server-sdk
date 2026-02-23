from __future__ import annotations

import asyncio
import json
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from typing import Any

import websockets

from .errors import CodexTransportError


class Transport(ABC):
    """Abstract transport interface for JSON-RPC message exchange."""

    @abstractmethod
    async def connect(self) -> None:
        """Open transport resources and establish connection."""
        raise NotImplementedError

    @abstractmethod
    async def send(self, payload: Mapping[str, Any]) -> None:
        """Send one JSON-serializable message."""
        raise NotImplementedError

    @abstractmethod
    async def recv(self) -> dict[str, Any]:
        """Receive one JSON message as a dictionary."""
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        """Close transport resources."""
        raise NotImplementedError


class StdioTransport(Transport):
    """JSON-RPC transport over a subprocess stdin/stdout pipe."""

    def __init__(
        self,
        command: Sequence[str],
        *,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        connect_timeout: float = 30.0,
    ) -> None:
        """Configure stdio transport.

        Args:
            command: Command argv used to start the app-server process.
            cwd: Optional subprocess working directory.
            env: Optional environment overrides for subprocess.
            connect_timeout: Timeout for subprocess creation.
        """
        if not command:
            raise ValueError("stdio command must not be empty")
        self._command = list(command)
        self._cwd = cwd
        self._env = dict(env) if env is not None else None
        self._connect_timeout = connect_timeout
        self._proc: asyncio.subprocess.Process | None = None

    async def connect(self) -> None:
        """Start subprocess if not already running."""
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
        """Write one JSON line to subprocess stdin."""
        if self._proc is None or self._proc.stdin is None:
            raise CodexTransportError("stdio transport is not connected")
        line = json.dumps(dict(payload), separators=(",", ":")) + "\n"
        try:
            self._proc.stdin.write(line.encode("utf-8"))
            await self._proc.stdin.drain()
        except Exception as exc:
            raise CodexTransportError("failed writing to stdio transport") from exc

    async def recv(self) -> dict[str, Any]:
        """Read one JSON line from subprocess stdout."""
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
            raise CodexTransportError(
                "received invalid JSON from stdio transport"
            ) from exc

    async def close(self) -> None:
        """Terminate subprocess and release handles."""
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
    """JSON-RPC transport over a websocket connection."""

    def __init__(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        connect_timeout: float = 30.0,
    ) -> None:
        """Configure websocket transport.

        Args:
            url: Websocket endpoint URL.
            headers: Optional request headers, including auth.
            connect_timeout: Timeout for websocket handshake.
        """
        self._url = url
        self._headers = dict(headers) if headers is not None else None
        self._connect_timeout = connect_timeout
        self._socket: Any = None

    async def connect(self) -> None:
        """Open websocket if not already connected."""
        if self._socket is not None:
            return
        try:
            self._socket = await asyncio.wait_for(
                websockets.connect(
                    self._url,
                    additional_headers=self._headers,
                    compression=None,
                ),
                timeout=self._connect_timeout,
            )
        except Exception as exc:
            raise CodexTransportError(
                "failed to connect websocket transport: "
                f"{self._url} ({exc.__class__.__name__}: {exc})"
            ) from exc

    async def send(self, payload: Mapping[str, Any]) -> None:
        """Send one JSON text frame over websocket."""
        if self._socket is None:
            raise CodexTransportError("websocket transport is not connected")
        try:
            await self._socket.send(json.dumps(dict(payload), separators=(",", ":")))
        except Exception as exc:
            raise CodexTransportError("failed writing to websocket transport") from exc

    async def recv(self) -> dict[str, Any]:
        """Receive and decode one websocket frame as JSON."""
        if self._socket is None:
            raise CodexTransportError("websocket transport is not connected")
        try:
            message = await self._socket.recv()
        except Exception as exc:
            raise CodexTransportError(
                "failed reading from websocket transport"
            ) from exc

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
        """Close websocket connection."""
        if self._socket is None:
            return
        socket = self._socket
        self._socket = None
        try:
            await socket.close()
        except Exception:
            pass
