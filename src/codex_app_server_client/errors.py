from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .models import ChatContinuation


class CodexError(Exception):
    """Base exception for the codex-app-server-client package."""


class CodexTransportError(CodexError):
    """Raised when the underlying transport fails or disconnects unexpectedly."""


class CodexTimeoutError(CodexError):
    """Raised when a request or turn wait exceeds its timeout policy."""


class CodexTurnInactiveError(CodexTimeoutError):
    """Raised when a running turn emits no matching events for too long."""

    def __init__(
        self,
        message: str,
        *,
        continuation: ChatContinuation,
        idle_seconds: float,
    ) -> None:
        super().__init__(message)
        self.continuation = continuation
        self.idle_seconds = idle_seconds


class CodexProtocolError(CodexError):
    """Raised when JSON-RPC or app-server protocol reports an error."""

    def __init__(
        self,
        message: str,
        *,
        code: int | None = None,
        data: Any = None,
    ) -> None:
        """Create a protocol error.

        Args:
            message: Human-readable description.
            code: Optional JSON-RPC error code.
            data: Optional protocol-provided error payload.
        """
        super().__init__(message)
        self.code = code
        self.data = data
