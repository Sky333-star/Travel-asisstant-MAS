"""Query Analyst agent -- turns a messy request into a structured brief.

This is the node that determines whether everything downstream is working from
reality or from a hallucination, so it runs a belt-and-braces strategy:

1. The **rule-based parser** always runs. It is instant, free and cannot
   invent a date.
2. The **LLM** runs in parallel when a key is configured, adding the
   interpretation that regexes cannot do -- reading "our anniversary" as a
   romantic trip for two, or "somewhere I can actually switch off" as a
   relaxed pace.
3. The two are **merged**, with rule-extracted literals filling any gap the
   model left. The merge is what makes the LLM strictly additive: it can
   improve the brief but not lose a value the regex already found.

The node also normalises the budget to EUR through the currency MCP server,
because every cost model downstream is EUR-denominated.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date

from ...llm import build_brief, merge_briefs, structured_call
from ...mcp import tools as mcp_tools
from ...observability import STAGE_ENTERED, langfuse_hook, span, time_agent
from ...schemas.travel import TravelBrief
from ..prompts import QUERY_ANALYST, with_today
from ..state import AgentState

logger = logging.getLogger(__name__)


async def _llm_brief(text: str, trace: object) -> TravelBrief | None:
    """Ask the LLM for a structured brief. Returns ``None`` on any failure."""
    return await structured_call(
        TravelBrief,
        system_prompt=with_today(QUERY_ANALYST),
        user_prompt=(
            "Traveller's message:\n"
            f"---\n{text}\n---\n\n"
            "Produce the structured travel brief."
        ),
        purpose="analyse_query",
        temperature=0.1,
        max_tokens=1200,
        trace=trace,
    )


async def _normalise_budget(brief: TravelBrief, trace_list: list) -> None:
    """Convert a non-EUR budget into EUR in place, using real ECB rates."""
    budget = brief.budget
    if budget.amount is None:
        return
    if budget.currency == "EUR":
        budget.amount_eur = budget.amount
        return

    converted = await mcp_tools.convert_budget_to_eur(
        budget.amount, budget.currency, trace_list
    )
    if converted is not None:
        budget.amount_eur = round(converted, 2)
        brief.notes.append(
            f"Budget of {budget.amount:,.0f} {budget.currency} converted to "
            f"EUR {budget.amount_eur:,.0f} at today's ECB rate."
        )
    else:
        # Falling back to the raw number would misprice the trip badly for a
        # weak currency, so we say what we did rather than pretend.
        budget.amount_eur = None
        brief.notes.append(
            f"Could not convert {budget.currency} to EUR; budget checks will "
            "be approximate."
        )


async def _resolve_origin(brief: TravelBrief, trace_list: list) -> None:
    """Attach coordinates to the stated origin so flight distance is real."""
    if not brief.origin or brief.origin_lat is not None:
        return
    hit = await mcp_tools.geocode(brief.origin, trace_list)
    if hit:
        brief.origin_lat = hit.get("latitude")
        brief.origin_lon = hit.get("longitude")
        # Prefer the canonical name: "nyc" becomes "New York".
        if hit.get("name") and hit["name"].lower() != brief.origin.lower():
            brief.notes.append(f"Interpreted origin '{brief.origin}' as {hit['name']}.")
            brief.origin = hit["name"]
    else:
        brief.notes.append(
            f"Could not locate '{brief.origin}' -- flight estimates may be off."
        )


async def query_analyst_node(state: AgentState) -> AgentState:
    """Produce the :class:`TravelBrief` for this conversation."""
    with time_agent("query_analyst"):
        STAGE_ENTERED.labels(stage="analysing").inc()

        text = state.get("user_input", "")
        trace = state.get("langfuse_trace")
        tool_trace: list = []

        with span("agent.query_analyst", **{"agent.input_length": len(text)}) as current:
            # Rules always run; the LLM runs alongside when available.
            rules_brief = build_brief(text)
            llm_result = await _llm_brief(text, trace)

            if llm_result is not None:
                brief = merge_briefs(rules_brief, llm_result)
                logger.info("Brief built by LLM + rules merge.")
            else:
                brief = rules_brief
                logger.info("Brief built by rules only (LLM unavailable or failed).")

            brief.raw_query = text

            # Carry forward anything already known from earlier turns, so a
            # follow-up like "make it 5 nights" does not lose the origin.
            previous = state.get("brief")
            if previous is not None:
                if not brief.origin and previous.origin:
                    brief.origin = previous.origin
                    brief.origin_lat = previous.origin_lat
                    brief.origin_lon = previous.origin_lon
                if not brief.dates.is_complete and previous.dates.is_complete:
                    brief.dates = previous.dates
                if brief.budget.amount is None and previous.budget.amount is not None:
                    brief.budget = previous.budget
                if not brief.interests and previous.interests:
                    brief.interests = previous.interests
                from ...llm.fallback import compute_missing

                brief.missing_critical = compute_missing(brief)

            # Enrichment that needs the network runs concurrently.
            await asyncio.gather(
                _normalise_budget(brief, tool_trace),
                _resolve_origin(brief, tool_trace),
                return_exceptions=True,
            )

            current.set_attribute("brief.confidence", brief.confidence)
            current.set_attribute("brief.method", brief.extraction_method)
            current.set_attribute("brief.missing", ",".join(brief.missing_critical))
            current.set_attribute("brief.interests", ",".join(brief.interests))

        langfuse_hook.log_span(
            trace,
            name="query_analyst",
            input_data={"text": text},
            output_data=brief.model_dump(mode="json"),
            metadata={"method": brief.extraction_method},
        )

        logger.info(
            "Brief: origin=%s dates=%s..%s interests=%s missing=%s",
            brief.origin,
            brief.dates.start, brief.dates.end,
            brief.interests, brief.missing_critical,
        )

        return AgentState(
            brief=brief,
            stage="clarifying" if brief.missing_critical else "scouting",
            tool_trace=tool_trace,
        )


def default_dates_if_missing(brief: TravelBrief, today: date | None = None) -> None:
    """Fill in a plausible window when the user gave only a duration.

    Called after the clarification loop has done what it can. Six weeks out is
    a deliberate choice: it sits inside the cheapest advance-purchase window
    and outside the 16-day forecast horizon, which makes the resulting weather
    basis honest rather than accidentally optimistic.
    """
    from datetime import timedelta

    today = today or date.today()
    if brief.dates.is_complete:
        return
    nights = brief.dates.duration_nights or 6
    start = today + timedelta(days=42)
    brief.dates.start = start
    brief.dates.end = start + timedelta(days=nights)
    brief.dates.flexible = True
    brief.notes.append(
        f"No dates given, so planning for {start.isoformat()} "
        f"({nights} nights). Tell me your real dates and I will re-plan."
    )
