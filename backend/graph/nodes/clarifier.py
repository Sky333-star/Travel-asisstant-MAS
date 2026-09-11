"""Clarifier node -- the first human-in-the-loop gate.

When the brief is missing something that would make every downstream number
wrong (a departure city, a date window), the graph stops and asks rather than
guessing. It stops using LangGraph's :func:`interrupt`, which suspends
execution *at this exact point*, persists the whole state to the SQLite
checkpointer, and returns control to the API. When the user answers, the graph
resumes from here with everything intact -- no replaying, no rebuilding.

Guard rails:

* At most ``MAX_CLARIFICATION_ROUNDS`` questions. Interrogating a traveller is
  a worse failure than planning with a stated assumption, so after the limit
  we fill sensible defaults, say so explicitly, and continue.
* Only genuinely blocking fields are asked about. Budget and interests have
  workable defaults and never stop the graph.
"""

from __future__ import annotations

import logging

from langgraph.types import interrupt

from ...llm import text_call
from ...observability import STAGE_ENTERED, span, time_agent
from ...schemas.travel import ClarificationRequest, TravelBrief
from ..prompts import CLARIFIER, with_today
from ..state import AgentState
from .query_analyst import default_dates_if_missing

logger = logging.getLogger(__name__)

MAX_CLARIFICATION_ROUNDS = 2

_FIELD_QUESTIONS: dict[str, tuple[str, list[str]]] = {
    "origin": (
        "which city you'll be flying from",
        ["London", "Berlin", "New York"],
    ),
    "dates": (
        "when you want to travel (dates, or a month and how many nights)",
        ["12-19 May", "first week of June, 5 nights", "sometime in October for a week"],
    ),
}


def build_question(missing: list[str]) -> ClarificationRequest:
    """Compose a single question covering every missing field."""
    parts = [_FIELD_QUESTIONS[f][0] for f in missing if f in _FIELD_QUESTIONS]
    examples = [e for f in missing for e in _FIELD_QUESTIONS.get(f, ("", []))[1]][:3]

    if not parts:
        question = "Could you tell me a little more about your trip?"
    elif len(parts) == 1:
        question = f"Before I can plan properly, I need {parts[0]}."
    else:
        question = (
            f"Before I can plan properly, I need {', '.join(parts[:-1])} "
            f"and {parts[-1]}."
        )

    return ClarificationRequest(
        fields=list(missing), question=question, examples=examples, blocking=True
    )


async def _polish_question(request: ClarificationRequest, raw_query: str) -> str:
    """Let the LLM phrase the question naturally; fall back to the template."""
    polished = await text_call(
        system_prompt=with_today(CLARIFIER),
        user_prompt=(
            f"The traveller said: {raw_query!r}\n"
            f"Still missing: {', '.join(request.fields)}\n"
            f"Example answers you could offer: {', '.join(request.examples)}\n\n"
            "Write the question."
        ),
        purpose="clarify_question",
        tier="fast",
        temperature=0.4,
        max_tokens=140,
    )
    if polished:
        cleaned = polished.strip().strip('"')
        # A model that rambles is worse than the template; cap it.
        if 10 < len(cleaned) <= 320:
            return cleaned
    return request.question


def apply_answer(brief: TravelBrief, answer: str) -> TravelBrief:
    """Re-parse the user's answer and fold it into the existing brief.

    The answer is parsed with the same rule-based extractor used for the
    original message, so "I'm in Madrid, 12-19 May" fills both fields in one
    turn. Only fields that were actually missing are overwritten -- an answer
    mentioning an unrelated city must not move a confirmed origin.
    """
    from ...llm.fallback import build_brief, compute_missing

    parsed = build_brief(answer)
    missing = set(brief.missing_critical)

    if "origin" in missing and parsed.origin:
        brief.origin = parsed.origin
        brief.origin_lat = None  # Force re-geocoding on the next pass.
        brief.origin_lon = None
    if "dates" in missing:
        if parsed.dates.is_complete:
            brief.dates = parsed.dates
        elif parsed.dates.duration_nights:
            brief.dates.duration_nights = parsed.dates.duration_nights

    # An answer often carries bonus detail; take it if we had nothing.
    if parsed.budget.amount is not None and brief.budget.amount is None:
        brief.budget = parsed.budget
    if parsed.interests:
        brief.interests = list(dict.fromkeys(brief.interests + parsed.interests))

    brief.missing_critical = compute_missing(brief)
    return brief


async def clarifier_node(state: AgentState) -> AgentState:
    """Ask for missing critical fields, or proceed with stated assumptions."""
    with time_agent("clarifier"):
        STAGE_ENTERED.labels(stage="clarifying").inc()

        brief = state.get("brief")
        rounds = state.get("clarification_rounds", 0)

        if brief is None:
            return AgentState(stage="scouting")

        if not brief.missing_critical:
            return AgentState(stage="scouting", clarification=None)

        # ---- Budget exhausted: assume, disclose, and move on ----
        if rounds >= MAX_CLARIFICATION_ROUNDS:
            logger.info(
                "Clarification limit reached; continuing with assumptions for %s",
                brief.missing_critical,
            )
            assumptions: list[str] = []
            if "dates" in brief.missing_critical:
                default_dates_if_missing(brief)
                assumptions.append("assumed travel dates about six weeks out")
            if "origin" in brief.missing_critical:
                # Origin genuinely cannot be assumed -- without it every fare
                # is fiction. Plan the destination side and say so.
                brief.notes.append(
                    "No departure city given, so I've left flights out of the "
                    "plan. Tell me where you're flying from and I'll add them."
                )
                assumptions.append("skipped flights (no origin given)")

            brief.missing_critical = []
            return AgentState(
                brief=brief,
                stage="scouting",
                clarification=None,
                scratch={"assumptions": assumptions},
            )

        # ---- Ask ----
        request = build_question(brief.missing_critical)
        request.question = await _polish_question(request, brief.raw_query)

        with span("agent.clarifier", **{"clarify.fields": ",".join(request.fields)}):
            logger.info("Interrupting for clarification: %s", request.fields)

            # Execution suspends here. The value returned is whatever the
            # /api/chat/resume endpoint supplies for this thread.
            answer = interrupt(
                {
                    "type": "clarification",
                    "question": request.question,
                    "fields": request.fields,
                    "examples": request.examples,
                }
            )

        answer_text = answer if isinstance(answer, str) else str(answer or "")
        logger.info("Clarification answered: %s", answer_text[:120])

        updated = apply_answer(brief, answer_text)

        return AgentState(
            brief=updated,
            clarification=None,
            clarification_rounds=rounds + 1,
            awaiting_user=False,
            messages=[
                {"role": "assistant", "content": request.question},
                {"role": "user", "content": answer_text},
            ],
            stage="scouting" if not updated.missing_critical else "clarifying",
        )
