from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, TypeAlias

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


class UnsetType:
    """Sentinel type representing an omitted configuration field."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "UNSET"


UNSET = UnsetType()

#: Approval policy accepted by thread/turn configuration fields.
#:
#: Values:
#: - ``"untrusted"``: require approvals for untrusted actions.
#: - ``"on-failure"``: request approval when an action fails.
#: - ``"on-request"``: request approval only when model asks for it.
#: - ``"never"``: never request approval.
ApprovalPolicy: TypeAlias = Literal["untrusted", "on-failure", "on-request", "never"]

#: Reasoning effort level for per-turn/model behavior.
#:
#: Values are ordered from lowest to highest: ``none``, ``minimal``, ``low``,
#: ``medium``, ``high``, ``xhigh``.
ReasoningEffort: TypeAlias = Literal["none", "minimal", "low", "medium", "high", "xhigh"]

#: Reasoning summary verbosity preference.
#:
#: Values:
#: - ``"auto"``: server/model-selected default
#: - ``"concise"``: short summary
#: - ``"detailed"``: expanded summary
#: - ``"none"``: disable reasoning summary text
ReasoningSummary: TypeAlias = Literal["auto", "concise", "detailed", "none"]


@dataclass(slots=True)
class ThreadConfig:
    """Thread-level configuration overrides used by thread start/resume/fork calls.

    Use `UNSET` (default) to omit a field from the request payload.
    Use `None` to explicitly send JSON `null` where the protocol accepts it.

    Attributes:
        cwd: Thread working directory.
        base_instructions: Base instruction text for the thread.
        developer_instructions: Developer instruction text for the thread.
        model: Model id for thread-level default model selection.
        model_provider: Optional model provider name/identifier.
        approval_policy: Approval policy mode (`untrusted`, `on-failure`,
            `on-request`, `never`).
        sandbox: Sandbox mode/policy selector accepted by the server.
        personality: Optional personality profile name.
        ephemeral: Optional ephemeral-thread flag.
        config: Optional thread-level config map forwarded to the server.
    """

    cwd: str | None | UnsetType = UNSET
    base_instructions: str | None | UnsetType = UNSET
    developer_instructions: str | None | UnsetType = UNSET
    model: str | None | UnsetType = UNSET
    model_provider: str | None | UnsetType = UNSET
    approval_policy: ApprovalPolicy | None | UnsetType = UNSET
    sandbox: str | None | UnsetType = UNSET
    personality: str | None | UnsetType = UNSET
    ephemeral: bool | None | UnsetType = UNSET
    config: dict[str, Any] | None | UnsetType = UNSET


@dataclass(slots=True)
class TurnOverrides:
    """Per-turn override fields forwarded to `turn/start`.

    Use `UNSET` (default) to omit a field from the request payload.
    Use `None` to explicitly send JSON `null` where the protocol accepts it.

    Attributes:
        cwd: Per-turn working directory override.
        model: Per-turn model override.
        effort: Reasoning effort level (`none`..`xhigh`).
        summary: Reasoning summary verbosity (`auto`, `concise`, `detailed`,
            `none`).
        sandbox_policy: Per-turn sandbox policy payload.
        personality: Per-turn personality override.
        approval_policy: Per-turn approval policy override.
        output_schema: Optional structured-output schema override.
    """

    cwd: str | None | UnsetType = UNSET
    model: str | None | UnsetType = UNSET
    effort: ReasoningEffort | None | UnsetType = UNSET
    summary: ReasoningSummary | None | UnsetType = UNSET
    sandbox_policy: dict[str, Any] | None | UnsetType = UNSET
    personality: str | None | UnsetType = UNSET
    approval_policy: ApprovalPolicy | None | UnsetType = UNSET
    output_schema: dict[str, Any] | None | UnsetType = UNSET
