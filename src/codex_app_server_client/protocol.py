from __future__ import annotations

from typing import Any

JSONRPC_VERSION = "2.0"

INITIALIZE_METHOD = "initialize"
THREAD_START_METHOD = "thread/start"
THREAD_RESUME_METHOD = "thread/resume"
TURN_START_METHOD = "turn/start"
TURN_INTERRUPT_METHOD = "turn/interrupt"

TURN_COMPLETED_METHODS = frozenset(
    {
        "turn/completed",
        "turn.completed",
        "turnCompleted",
    }
)

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
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {
        "jsonrpc": JSONRPC_VERSION,
        "id": request_id,
        "error": error,
    }


def is_response_message(payload: dict[str, Any]) -> bool:
    return "id" in payload and "method" not in payload


def extract_error(payload: dict[str, Any]) -> dict[str, Any] | None:
    error = payload.get("error")
    if isinstance(error, dict):
        return error
    return None


def is_turn_completed(method: str) -> bool:
    return method in TURN_COMPLETED_METHODS


def is_turn_failed(method: str) -> bool:
    return method in TURN_FAILED_METHODS
