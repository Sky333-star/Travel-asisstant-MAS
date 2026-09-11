"""The shared state object that flows through the agent graph.

WHY A TYPED DICT WITH REDUCERS
------------------------------
LangGraph merges each node's returned partial state into the running state. For
scalars, last-write-wins is right. For accumulating channels -- the message
history, the tool trace, the event log -- it is wrong: two nodes writing in the
same superstep would clobber each other. Those channels therefore declare an
``Annotated[..., reducer]`` so LangGraph appends instead of replaces.

Getting this wrong is a class of bug that only shows up once nodes run
concurrently, which is exactly when it is hardest to debug. Declaring the
reducers up front avoids it entirely.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from ..schemas.plan import TripPlan, ValidationReport
from ..schemas.travel import (
    ClarificationRequest,
    DestinationCandidate,
    Recommendation,
    ToolInvocation,
    TravelBrief,
)


def _merge_dicts(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    """Shallow-merge reducer for free-form scratch space."""
    return {**(left or {}), **(right or {})}


def _last(left: Any, right: Any) -> Any:
    """Explicit last-write-wins, tolerating ``None`` from a node that abstained."""
    return right if right is not None else left


class AgentState(TypedDict, total=False):
    """Everything the graph knows about one conversation.

    ``total=False`` because nodes return partial updates; LangGraph handles the
    merge. Every field is optional at the type level and populated in stage
    order at runtime.
    """

    # ---- Conversation ----
    thread_id: str
    user_input: str
    input_mode: str
    messages: Annotated[list[dict[str, str]], operator.add]

    # ---- Stage 1: understanding ----
    brief: TravelBrief | None
    clarification: ClarificationRequest | None
    clarification_rounds: int
    awaiting_user: bool

    # ---- Stage 2: candidate generation and scoring ----
    candidates: list[DestinationCandidate]
    recommendation: Recommendation | None

    # ---- Stage 3: selection ----
    selected_destination: DestinationCandidate | None
    selection_raw: str | None

    # ---- Stage 4: planning and validation ----
    plan: TripPlan | None
    validation: ValidationReport | None
    revision: int
    fix_actions: list[str]
    plan_history: Annotated[list[dict[str, Any]], operator.add]

    # ---- Cross-cutting ----
    stage: str
    tool_trace: Annotated[list[ToolInvocation], operator.add]
    errors: Annotated[list[str], operator.add]
    scratch: Annotated[dict[str, Any], _merge_dicts]
    final_message: str | None
    langfuse_trace: Any


def initial_state(
    thread_id: str, user_input: str, input_mode: str = "text"
) -> AgentState:
    """Build a fresh state for a new conversation turn."""
    return AgentState(
        thread_id=thread_id,
        user_input=user_input,
        input_mode=input_mode,
        messages=[{"role": "user", "content": user_input}],
        brief=None,
        clarification=None,
        clarification_rounds=0,
        awaiting_user=False,
        candidates=[],
        recommendation=None,
        selected_destination=None,
        selection_raw=None,
        plan=None,
        validation=None,
        revision=0,
        fix_actions=[],
        plan_history=[],
        stage="intake",
        tool_trace=[],
        errors=[],
        scratch={},
        final_message=None,
        langfuse_trace=None,
    )
