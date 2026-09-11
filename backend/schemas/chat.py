"""HTTP/SSE wire models -- the contract between FastAPI and the Next.js UI.

Kept separate from the domain models so that the transport shape can evolve
(adding a field to an SSE event, say) without touching agent internals.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

from .plan import TripPlan, ValidationReport
from .travel import ClarificationRequest, Recommendation, TravelBrief


def _now() -> str:
    return datetime.now(UTC).isoformat()


class InputMode(str, Enum):
    TEXT = "text"
    VOICE = "voice"


class ChatRequest(BaseModel):
    """A user turn. ``thread_id`` is what makes the conversation stateful."""

    message: str = Field(min_length=1, max_length=4000)
    thread_id: str | None = None
    input_mode: InputMode = InputMode.TEXT
    locale: str = "en"


class ResumeRequest(BaseModel):
    """Answer to a pending question, resuming an interrupted graph run.

    LangGraph pauses the graph at an ``interrupt()``; this endpoint supplies
    the value that ``interrupt()`` returns and lets execution continue from
    exactly that point, with all prior state intact.
    """

    thread_id: str
    value: str = Field(min_length=1, max_length=2000)
    selected_index: int | None = Field(default=None, ge=0, le=20)


class TranscribeResponse(BaseModel):
    text: str
    model: str
    duration_ms: float | None = None
    provider: str = "huggingface"


class StageName(str, Enum):
    """Graph stages, surfaced to the UI as a progress timeline."""

    INTAKE = "intake"
    ANALYSING = "analysing"
    CLARIFYING = "clarifying"
    SCOUTING = "scouting"
    WEATHER = "weather"
    RECOMMENDING = "recommending"
    AWAITING_CHOICE = "awaiting_choice"
    PLANNING = "planning"
    VALIDATING = "validating"
    REVISING = "revising"
    PRESENTING = "presenting"
    DONE = "done"
    ERROR = "error"


class EventType(str, Enum):
    STAGE = "stage"
    MESSAGE = "message"
    BRIEF = "brief"
    RECOMMENDATION = "recommendation"
    QUESTION = "question"
    PLAN = "plan"
    VALIDATION = "validation"
    TOOL = "tool"
    TRACE = "trace"
    ERROR = "error"
    DONE = "done"


class StreamEvent(BaseModel):
    """One Server-Sent Event.

    A single event type with a discriminating ``type`` field keeps the
    frontend reducer small: it switches once and every branch has a typed
    payload.
    """

    type: EventType
    thread_id: str
    at: str = Field(default_factory=_now)
    stage: StageName | None = None
    text: str | None = None
    payload: dict[str, Any] | None = None

    def to_sse(self) -> str:
        """Serialise as an SSE frame."""
        return f"event: {self.type.value}\ndata: {self.model_dump_json(exclude_none=True)}\n\n"


class SessionSnapshot(BaseModel):
    """Everything the UI needs to rehydrate a conversation after a reload."""

    thread_id: str
    stage: StageName = StageName.INTAKE
    awaiting_input: bool = False
    pending_question: ClarificationRequest | None = None
    brief: TravelBrief | None = None
    recommendation: Recommendation | None = None
    plan: TripPlan | None = None
    validation: ValidationReport | None = None
    messages: list[dict[str, str]] = Field(default_factory=list)
    revision: int = 0
    updated_at: str = Field(default_factory=_now)


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"] = "ok"
    version: str = "1.0.0"
    llm_available: bool = False
    llm_model: str | None = None
    mcp_servers: dict[str, str] = Field(default_factory=dict)
    observability: dict[str, bool] = Field(default_factory=dict)
    checked_at: str = Field(default_factory=_now)
