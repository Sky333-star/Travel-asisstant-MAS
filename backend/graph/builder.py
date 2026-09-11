"""Graph assembly -- wiring the agents into an executable workflow.

THE SHAPE
---------

    intake -> query_analyst -> [clarifier] -> destination_scout
       -> weather_analyst -> recommender -> selection (HUMAN GATE)
       -> itinerary_planner -> plan_validator
             |                      |
             +--- revise (loop) ----+
                                    |
                             presenter -> END

Two conditional edges carry the intelligence:

* **After ``query_analyst``** -- go to the clarifier only when something
  genuinely blocking is missing. Most requests skip it entirely.
* **After ``plan_validator``** -- ship the plan, or loop back to the planner
  with the validator's fix hints. The loop is bounded by
  ``MAX_PLAN_REVISIONS``; on exhaustion it presents the best attempt with the
  remaining issues stated rather than looping forever.

CHECKPOINTING
-------------
A SQLite checkpointer persists state after every node. That is what makes the
two human-in-the-loop gates work: at an ``interrupt()`` the process can stop
entirely, and a request arriving minutes later resumes from exactly that point
with full state. It also means a crash mid-plan loses one node, not the
conversation.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from langgraph.graph import END, START, StateGraph

from ..config import settings
from .nodes import (
    clarifier_node,
    destination_scout_node,
    intake_node,
    itinerary_planner_node,
    plan_validator_node,
    presenter_node,
    query_analyst_node,
    recommender_node,
    selection_node,
    weather_analyst_node,
)
from .state import AgentState

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Conditional routing
# --------------------------------------------------------------------------


def route_after_analysis(state: AgentState) -> Literal["clarify", "scout", "fail"]:
    """Clarify only when a genuinely blocking field is missing."""
    brief = state.get("brief")
    if brief is None:
        return "fail"
    if brief.missing_critical:
        logger.debug("Routing to clarifier for: %s", brief.missing_critical)
        return "clarify"
    return "scout"


def route_after_clarification(state: AgentState) -> Literal["clarify", "scout"]:
    """Loop the clarifier while fields remain and the round budget allows."""
    from .nodes.clarifier import MAX_CLARIFICATION_ROUNDS

    brief = state.get("brief")
    rounds = state.get("clarification_rounds", 0)

    if brief is not None and brief.missing_critical and rounds < MAX_CLARIFICATION_ROUNDS:
        return "clarify"
    return "scout"


def route_after_validation(state: AgentState) -> Literal["revise", "present"]:
    """Ship the plan, or send it back to the planner with fix hints.

    Three conditions end the loop: the plan passed, the revision budget is
    exhausted, or the validator produced no actionable fix. The last case
    matters -- looping on an issue no lever can address (a destination with
    almost no mapped POIs, say) would burn the budget for nothing.
    """
    validation = state.get("validation")
    revision = state.get("revision", 0)

    if validation is None or validation.passed:
        return "present"

    if revision >= settings.max_plan_revisions:
        logger.info(
            "Revision budget (%d) exhausted; presenting best attempt.",
            settings.max_plan_revisions,
        )
        return "present"

    if not state.get("fix_actions"):
        logger.info("Validator found blockers but no actionable fix; presenting.")
        return "present"

    logger.info(
        "Revision %d -> %d with fixes: %s",
        revision, revision + 1, state.get("fix_actions"),
    )
    return "revise"


async def increment_revision(state: AgentState) -> AgentState:
    """Bump the revision counter between validator and planner.

    A tiny dedicated node rather than a side effect inside the planner: it
    keeps the counter's ownership obvious and makes the loop legible in a
    rendered graph.
    """
    return AgentState(revision=state.get("revision", 0) + 1, stage="revising")


# --------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------


def build_graph() -> StateGraph:
    """Construct the agent graph (uncompiled)."""
    graph = StateGraph(AgentState)

    graph.add_node("intake", intake_node)
    graph.add_node("query_analyst", query_analyst_node)
    graph.add_node("clarifier", clarifier_node)
    graph.add_node("destination_scout", destination_scout_node)
    graph.add_node("weather_analyst", weather_analyst_node)
    graph.add_node("recommender", recommender_node)
    graph.add_node("selection", selection_node)
    graph.add_node("itinerary_planner", itinerary_planner_node)
    graph.add_node("plan_validator", plan_validator_node)
    graph.add_node("bump_revision", increment_revision)
    graph.add_node("presenter", presenter_node)

    graph.add_edge(START, "intake")
    graph.add_edge("intake", "query_analyst")

    graph.add_conditional_edges(
        "query_analyst",
        route_after_analysis,
        {"clarify": "clarifier", "scout": "destination_scout", "fail": END},
    )
    graph.add_conditional_edges(
        "clarifier",
        route_after_clarification,
        {"clarify": "clarifier", "scout": "destination_scout"},
    )

    graph.add_edge("destination_scout", "weather_analyst")
    graph.add_edge("weather_analyst", "recommender")
    graph.add_edge("recommender", "selection")
    graph.add_edge("selection", "itinerary_planner")
    graph.add_edge("itinerary_planner", "plan_validator")

    graph.add_conditional_edges(
        "plan_validator",
        route_after_validation,
        {"revise": "bump_revision", "present": "presenter"},
    )
    graph.add_edge("bump_revision", "itinerary_planner")
    graph.add_edge("presenter", END)

    return graph


_compiled: Any = None
_checkpointer_cm: Any = None


async def get_compiled_graph() -> Any:
    """Return the compiled graph with a persistent SQLite checkpointer.

    The checkpointer is opened once for the process lifetime. ``AsyncSqliteSaver``
    is an async context manager, so we enter it manually and keep the handle for
    :func:`close_graph` to release on shutdown.
    """
    global _compiled, _checkpointer_cm

    if _compiled is not None:
        return _compiled

    checkpointer = None
    try:
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        _checkpointer_cm = AsyncSqliteSaver.from_conn_string(str(settings.checkpoint_path))
        checkpointer = await _checkpointer_cm.__aenter__()
        logger.info("Checkpointer ready at %s", settings.checkpoint_path)
    except Exception as exc:
        # Without a checkpointer the graph still runs, but interrupts cannot
        # resume -- so this is a loud warning, not a debug line.
        logger.warning(
            "SQLite checkpointer unavailable (%s). Human-in-the-loop resume "
            "will not work in this process.",
            exc,
        )

    _compiled = build_graph().compile(checkpointer=checkpointer)
    return _compiled


async def close_graph() -> None:
    """Release the checkpointer on shutdown."""
    global _compiled, _checkpointer_cm
    if _checkpointer_cm is not None:
        try:
            await _checkpointer_cm.__aexit__(None, None, None)
        except Exception as exc:  # pragma: no cover
            logger.debug("Checkpointer close raised: %s", exc)
        _checkpointer_cm = None
    _compiled = None


def render_mermaid() -> str:
    """Mermaid source for the graph, used in the docs and the /api/graph route."""
    return """graph TD
    START([User message]) --> intake[Intake<br/>normalise + trace]
    intake --> analyst[Query Analyst<br/>LLM + rules -> TravelBrief]
    analyst -->|missing origin/dates| clarifier{{Clarifier<br/>HUMAN GATE}}
    analyst -->|complete| scout[Destination Scout<br/>catalogue + LLM + geocode verify]
    clarifier -->|still missing| clarifier
    clarifier -->|resolved| scout
    scout --> weather[Weather Analyst<br/>Open-Meteo scoring]
    weather --> recommender[Recommender<br/>rank + write pitch]
    recommender --> selection{{Selection<br/>HUMAN GATE}}
    selection --> planner[Itinerary Planner<br/>flights + hotels + POIs]
    planner --> validator[Plan Validator<br/>rules + LLM critic]
    validator -->|blockers + budget left| bump[Bump revision]
    bump --> planner
    validator -->|passed / budget spent| presenter[Presenter<br/>narrative + packing]
    presenter --> END([Plan delivered])
"""
