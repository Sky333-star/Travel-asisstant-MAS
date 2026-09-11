"""Destination Scout agent -- generates the candidate set.

Candidates come from three sources, in priority order:

1. **Destinations the user named.** If someone says "I'm thinking Kyoto or
   Lisbon", those go in first and are never dropped. The system is an
   assistant, not a contrarian.
2. **The curated catalogue**, filtered by interest, season and budget tier via
   the geo MCP server. This is the deterministic floor -- it works with no LLM
   and never invents a coordinate.
3. **LLM suggestions**, which add the long tail the catalogue cannot hold.
   Every LLM-suggested city is *verified through the geocoder* before it is
   allowed into the candidate set; a city the geocoder cannot find is dropped
   rather than shown. This is the guard that keeps a hallucinated place name
   out of a user-facing recommendation.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field

from ...llm import structured_call
from ...mcp import tools as mcp_tools
from ...observability import STAGE_ENTERED, langfuse_hook, span, time_agent
from ...schemas.travel import DestinationCandidate, TravelBrief
from ..prompts import DESTINATION_SCOUT, with_today
from ..scoring import interest_overlap_score, season_fit_score
from ..state import AgentState

logger = logging.getLogger(__name__)

MAX_CANDIDATES = 6


class ScoutSuggestion(BaseModel):
    """One LLM-proposed destination."""

    city: str
    country: str | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    reason: str = ""
    tags: list[str] = Field(default_factory=list)


class ScoutOutput(BaseModel):
    """The scout's re-ranking plus its own additions."""

    ranked_cities: list[str] = Field(default_factory=list)
    additional: list[ScoutSuggestion] = Field(default_factory=list)
    reasons: dict[str, str] = Field(default_factory=dict)


def _candidate_from_catalogue(entry: dict[str, Any], brief: TravelBrief) -> DestinationCandidate:
    """Convert a geo-server catalogue entry into a scored candidate shell."""
    match = entry.get("match") or {}
    interest_score, matched = interest_overlap_score(
        entry.get("tags") or [], brief.interests
    )
    month = brief.dates.start.month if brief.dates.start else None

    return DestinationCandidate(
        city=entry.get("city") or "Unknown",
        country=entry.get("country"),
        iso2=entry.get("iso2"),
        region=entry.get("region"),
        latitude=float(entry.get("lat") or 0.0),
        longitude=float(entry.get("lon") or 0.0),
        blurb=entry.get("blurb"),
        tags=list(entry.get("tags") or []),
        budget_tier=entry.get("budget_tier"),
        best_months=list(entry.get("best_months") or []),
        interest_score=match.get("interest_score", interest_score),
        season_score=season_fit_score(entry.get("best_months") or [], month),
        matched_interests=match.get("matched_interests") or matched,
        source="catalogue",
    )


async def _verify_suggestion(
    suggestion: ScoutSuggestion, brief: TravelBrief, tool_trace: list
) -> DestinationCandidate | None:
    """Turn an LLM suggestion into a candidate, or reject it.

    A suggestion is accepted only if the geocoder confirms the place exists.
    We prefer the geocoder's coordinates over the model's even when the model
    supplied some -- an LLM's recalled latitude is frequently off by degrees,
    and a wrong coordinate silently produces the wrong city's weather.
    """
    hit = await mcp_tools.geocode(f"{suggestion.city} {suggestion.country or ''}".strip(),
                                  tool_trace)
    if not hit or hit.get("latitude") is None:
        logger.info("Dropping unverifiable LLM suggestion: %s", suggestion.city)
        return None

    month = brief.dates.start.month if brief.dates.start else None
    tags = [t.lower() for t in suggestion.tags]
    interest_score, matched = interest_overlap_score(tags, brief.interests)

    return DestinationCandidate(
        city=hit.get("name") or suggestion.city,
        country=hit.get("country") or suggestion.country,
        iso2=hit.get("country_code"),
        region=None,
        latitude=float(hit["latitude"]),
        longitude=float(hit["longitude"]),
        blurb=suggestion.reason or None,
        tags=tags,
        budget_tier=None,
        best_months=[],
        interest_score=interest_score,
        season_score=season_fit_score([], month),
        matched_interests=matched,
        why=[suggestion.reason] if suggestion.reason else [],
        source="llm",
    )


async def _llm_scout(
    brief: TravelBrief, catalogue: list[DestinationCandidate], trace: object
) -> ScoutOutput | None:
    """Ask the LLM to re-rank the shortlist and add anything obviously missing."""
    shortlist = [
        {
            "city": c.city,
            "country": c.country,
            "tags": c.tags[:8],
            "budget_tier": c.budget_tier,
            "matched_interests": c.matched_interests,
        }
        for c in catalogue
    ]

    return await structured_call(
        ScoutOutput,
        system_prompt=with_today(DESTINATION_SCOUT),
        user_prompt=(
            "Traveller brief:\n"
            f"- Origin: {brief.origin or 'unknown'}\n"
            f"- Dates: {brief.dates.start} to {brief.dates.end} "
            f"({brief.dates.duration_nights or '?'} nights)\n"
            f"- Party: {brief.party.adults} adults, {brief.party.children} children\n"
            f"- Interests: {', '.join(brief.interests) or 'not specified'}\n"
            f"- Climate preference: {brief.climate_preference}\n"
            f"- Pace: {brief.pace} | Style: {brief.style}\n"
            f"- Budget: {brief.budget.amount_eur or brief.budget.amount or 'not stated'} "
            f"{'EUR' if brief.budget.amount_eur else brief.budget.currency}"
            f"{' per person' if brief.budget.per_person else ' total'}\n"
            f"- Purpose: {brief.trip_purpose or 'not stated'}\n"
            f"- Must avoid: {', '.join(brief.constraints.avoid_cities) or 'nothing'}\n"
            f"- Max flight hours: {brief.constraints.max_flight_hours or 'no limit'}\n"
            f"- Original message: {brief.raw_query!r}\n\n"
            f"Catalogue shortlist:\n{shortlist}\n\n"
            "Re-rank these by fit to this traveller (ranked_cities, using the "
            "exact city names given), add up to 3 better options the shortlist "
            "missed (additional), and give one concrete reason per city "
            "(reasons, keyed by city name)."
        ),
        purpose="scout_destinations",
        temperature=0.5,
        max_tokens=1100,
        trace=trace,
    )


async def destination_scout_node(state: AgentState) -> AgentState:
    """Build the candidate destination set."""
    with time_agent("destination_scout"):
        STAGE_ENTERED.labels(stage="scouting").inc()

        brief = state.get("brief")
        trace = state.get("langfuse_trace")
        tool_trace: list = []

        if brief is None:
            return AgentState(stage="error", errors=["No brief available to scout from."])

        month = brief.dates.start.month if brief.dates.start else None
        nights = brief.dates.duration_nights or 6
        budget_eur = brief.budget.amount_eur

        # Per-day budget drives the catalogue's price-tier filter.
        per_day = None
        if budget_eur:
            people = max(1, brief.party.total)
            total_eur = budget_eur if brief.budget.per_person else budget_eur / people
            # Roughly half a trip's cost is ground spend; the rest is airfare.
            per_day = (total_eur * 0.5) / max(1, nights)

        with span(
            "agent.destination_scout",
            **{"scout.month": month or 0, "scout.interests": ",".join(brief.interests)},
        ) as current:
            candidates: list[DestinationCandidate] = []
            seen: set[str] = set()

            # ---- 1. Destinations the user named come first ----
            for name in brief.named_destinations[:3]:
                record = await mcp_tools.lookup_destination(name, tool_trace)
                if not record or record.get("lat") is None:
                    continue
                interest_score, matched = interest_overlap_score(
                    record.get("tags") or [], brief.interests
                )
                candidate = DestinationCandidate(
                    city=record.get("city") or name,
                    country=record.get("country"),
                    iso2=record.get("iso2"),
                    region=record.get("region"),
                    latitude=float(record["lat"]),
                    longitude=float(record["lon"]),
                    blurb=record.get("blurb"),
                    tags=list(record.get("tags") or []),
                    budget_tier=record.get("budget_tier"),
                    best_months=list(record.get("best_months") or []),
                    interest_score=interest_score,
                    season_score=season_fit_score(record.get("best_months") or [], month),
                    matched_interests=matched,
                    why=["You asked about this destination."],
                    source="user_named",
                )
                if candidate.city.lower() not in seen:
                    seen.add(candidate.city.lower())
                    candidates.append(candidate)

            # ---- 2. Catalogue candidates ----
            raw = await mcp_tools.suggest_destinations(
                interests=brief.interests,
                travel_month=month,
                budget_per_day_eur=per_day,
                regions=brief.regions,
                exclude_countries=brief.constraints.avoid_countries,
                exclude_cities=brief.constraints.avoid_cities,
                limit=MAX_CANDIDATES + 2,
                trace=tool_trace,
            )
            catalogue = [_candidate_from_catalogue(entry, brief) for entry in raw]
            for candidate in catalogue:
                if candidate.city.lower() not in seen and candidate.latitude:
                    seen.add(candidate.city.lower())
                    candidates.append(candidate)

            # ---- 3. LLM re-ranking and additions ----
            scout_output = await _llm_scout(brief, candidates[:MAX_CANDIDATES + 2], trace)
            if scout_output is not None:
                by_city = {c.city.lower(): c for c in candidates}

                for suggestion in scout_output.additional[:3]:
                    if suggestion.city.lower() in seen:
                        continue
                    verified = await _verify_suggestion(suggestion, brief, tool_trace)
                    if verified is not None:
                        seen.add(verified.city.lower())
                        candidates.append(verified)
                        by_city[verified.city.lower()] = verified

                for city, reason in (scout_output.reasons or {}).items():
                    target = by_city.get(city.lower())
                    if target is not None and reason:
                        target.why = [reason] + [w for w in target.why if w != reason]

                # Apply the LLM's ordering, keeping unranked entries behind it.
                if scout_output.ranked_cities:
                    order = {
                        city.lower(): i
                        for i, city in enumerate(scout_output.ranked_cities)
                    }
                    candidates.sort(key=lambda c: order.get(c.city.lower(), 999))
                current.set_attribute("scout.llm_used", True)
            else:
                current.set_attribute("scout.llm_used", False)

            # A user-named destination always stays in the set, even if the
            # LLM ranked it last -- dropping it would be ignoring the user.
            named = [c for c in candidates if c.source == "user_named"]
            others = [c for c in candidates if c.source != "user_named"]
            candidates = named + others[: max(0, MAX_CANDIDATES - len(named))]

            current.set_attribute("scout.candidate_count", len(candidates))

        langfuse_hook.log_span(
            trace,
            name="destination_scout",
            input_data={"interests": brief.interests, "month": month},
            output_data={"candidates": [c.city for c in candidates]},
        )

        logger.info("Scouted %d candidates: %s",
                    len(candidates), [c.city for c in candidates])

        if not candidates:
            return AgentState(
                stage="error",
                errors=["No destination candidates could be generated."],
                tool_trace=tool_trace,
            )

        return AgentState(
            candidates=candidates, stage="weather", tool_trace=tool_trace
        )
