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


class ConversationStep(BaseModel):
    """A completed, non-delta conversation step emitted during a turn.

    Attributes:
        thread_id: Parent thread identifier.
        turn_id: Parent turn identifier.
        item_id: Underlying item identifier when provided.
        step_type: Canonical label for UI/clients (e.g. thinking/exec/codex).
        item_type: Raw protocol item type (e.g. reasoning/commandExecution/agentMessage).
        status: Step lifecycle status for this event.
        text: Human-readable text for the step when available.
        data: Full item payload and metadata for advanced consumers.
    """

    thread_id: str
    turn_id: str
    item_id: str | None = None
    step_type: str
    item_type: str | None = None
    status: Literal["completed"] = "completed"
    text: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class ChatContinuation(BaseModel):
    """Opaque continuation token for resuming a timed-out running turn.

    Attributes:
        thread_id: Thread that owns the running turn.
        turn_id: Running turn identifier.
        cursor: Number of turn events already consumed by caller.
        mode: API mode that produced this continuation.
    """

    thread_id: str
    turn_id: str
    cursor: int = 0
    mode: Literal["once", "stream"]


class CancelResult(BaseModel):
    """Result of cancelling a running turn continuation.

    Attributes:
        thread_id: Thread id for the cancelled turn.
        turn_id: Turn id that was cancelled.
        steps: Unread completed step objects accumulated since continuation cursor.
        raw_events: Unread raw events accumulated since continuation cursor.
        was_completed: True if the turn was already completed when cancelling.
        was_interrupted: True if an interrupt request was sent.
    """

    thread_id: str
    turn_id: str
    steps: list[ConversationStep] = Field(default_factory=list)
    raw_events: list[dict[str, Any]] = Field(default_factory=list)
    was_completed: bool = False
    was_interrupted: bool = False
