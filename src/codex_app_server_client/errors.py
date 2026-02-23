from __future__ import annotations

from typing import Any


class CodexError(Exception):
    """Base exception for this package."""


class CodexTransportError(CodexError):
    """Raised when the underlying transport fails."""


class CodexTimeoutError(CodexError):
    """Raised when a request or turn times out."""


class CodexProtocolError(CodexError):
    """Raised when JSON-RPC or app-server protocol returns an error."""

    def __init__(
        self,
        message: str,
        *,
        code: int | None = None,
        data: Any = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.data = data
