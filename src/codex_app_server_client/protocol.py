from __future__ import annotations

from typing import Any

# JSON-RPC protocol version used by Codex app-server envelopes.
JSONRPC_VERSION = "2.0"

# Core request methods used by this client.
INITIALIZE_METHOD = "initialize"
THREAD_START_METHOD = "thread/start"
THREAD_RESUME_METHOD = "thread/resume"
THREAD_READ_METHOD = "thread/read"
TURN_START_METHOD = "turn/start"
TURN_INTERRUPT_METHOD = "turn/interrupt"
ITEM_COMPLETED_METHOD = "item/completed"

DEFAULT_OPT_OUT_NOTIFICATION_METHODS = (
    "codex/event/agent_message_content_delta",
    "codex/event/reasoning_content_delta",
    "codex/event/item_started",
    "codex/event/item_completed",
    "codex/event/task_started",
    "codex/event/task_complete",
)

# Notification method aliases that may signal turn completion.
TURN_COMPLETED_METHODS = frozenset(
    {
        "turn/completed",
        "turn.completed",
        "turnCompleted",
    }
)

# Notification method aliases that may signal turn failure.
TURN_FAILED_METHODS = frozenset(
    {
        "turn/error",
        "turn.failed",
        "turn/failed",
        "turnFailed",
        "turn/errored",
    }
)


def make_request(
    request_id: int,
    method: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a JSON-RPC request envelope."""
    payload: dict[str, Any] = {
        "jsonrpc": JSONRPC_VERSION,
        "id": request_id,
        "method": method,
    }
    if params is not None:
        payload["params"] = params
    return payload


def make_error_response(
    request_id: int | str,
    code: int,
    message: str,
    data: Any = None,
) -> dict[str, Any]:
    """Build a JSON-RPC error response envelope."""
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {
        "jsonrpc": JSONRPC_VERSION,
        "id": request_id,
        "error": error,
    }


def is_response_message(payload: dict[str, Any]) -> bool:
    """Return True when payload is a response (has id, no method)."""
    return "id" in payload and "method" not in payload


def extract_error(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Return JSON-RPC error object if present and valid."""
    error = payload.get("error")
    if isinstance(error, dict):
        return error
    return None


def is_turn_completed(method: str) -> bool:
    """Return True when method name indicates turn completion."""
    return method in TURN_COMPLETED_METHODS


def is_turn_failed(method: str) -> bool:
    """Return True when method name indicates turn failure."""
    return method in TURN_FAILED_METHODS
