from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class InitializeResult(BaseModel):
    """Parsed result for the `initialize` handshake response.

    Attributes:
        protocol_version: Protocol version echoed by server, if present.
        server_info: Optional server identity/details object.
        capabilities: Optional capability map returned by server.
        raw: Full raw initialize result payload.
    """

    protocol_version: str | None = None
    server_info: dict[str, Any] | None = None
    capabilities: dict[str, Any] | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class ChatResult(BaseModel):
    """Buffered result for a single chat turn.

    Attributes:
        thread_id: Thread identifier used for the turn.
        turn_id: Turn identifier returned by server.
        final_text: Best-effort final assistant text assembled from events.
        raw_events: Raw JSON-RPC notifications consumed for the turn.
        assistant_item_id: Assistant item id used for final text when known.
        completion_source: Source used to determine final text.
    """

    thread_id: str
    turn_id: str
    final_text: str
    raw_events: list[dict[str, Any]] = Field(default_factory=list)
    assistant_item_id: str | None = None
    completion_source: Literal["item_completed", "thread_read_fallback"] | None = None
