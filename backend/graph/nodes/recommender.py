"""Recommender agent -- ranks the candidates and presents the shortlist.

Two jobs:

1. **Estimate cost per candidate** so the ranking can weigh affordability
   against fit. This is a *rough* estimate -- one flight query and a lodging
   probe per candidate would be far too slow across six destinations, so a
   distance-based fare model and the city price index are used instead. The
   detailed, per-property numbers come later, for the one destination the user
   actually picks. Spending the expensive calls only on the chosen destination
   is what keeps the interaction responsive.
2. **Write the pitch.** The composite ranking is arithmetic; the rationale is
   prose. The LLM writes it from the real numbers, and a deterministic
   fallback covers the no-key case.
"""

from __future__ import annotations

import logging
from datetime import date

from pydantic import BaseModel, Field

from ...llm import structured_call
from ...observability import STAGE_ENTERED, langfuse_hook, span, time_agent
from ...schemas.travel import DestinationCandidate, Recommendation, TravelBrief
from ..prompts import RECOMMENDER, with_today
from ..scoring import rank_candidates
from ..state import AgentState

logger = logging.getLogger(__name__)

# Mirrors the flight server's model closely enough for ranking, without
# spending a subprocess round trip per candidate.
_FARE_BANDS: tuple[tuple[float, float, float], ...] = (
    (400.0, 48.0, 0.150),
    (1_000.0, 42.0, 0.115),
    (2_500.0, 38.0, 0.082),
    (6_000.0, 55.0, 0.058),
    (10_000.0, 80.0, 0.045),
    (float("inf"), 120.0, 0.038),
)

_TIER_NIGHTLY_EUR = {1: 55.0, 2: 95.0, 3: 150.0, 4: 240.0}
_TIER_DAILY_EUR = {1: 35.0, 2: 60.0, 3: 95.0, 4: 150.0}
_STYLE_MULTIPLIER = {"budget": 0.7, "balanced": 1.0, "comfort": 1.4, "luxury": 2.2}


class RecommenderOutput(BaseModel):
    headline: str = Field(default="", max_length=140)
    rationale: str = Field(default="", max_length=1200)
    follow_up_question: str = Field(default="", max_length=300)


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    import math

    p1, p2 = math.radians(lat1), math.radians(lat2)
    d_phi = p2 - p1
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(d_lambda / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(min(1.0, a)))


def estimate_trip_cost(
    candidate: DestinationCandidate, brief: TravelBrief
) -> tuple[float | None, float | None, float | None]:
    """Rough (flight, nightly, total) EUR estimate for ranking purposes."""
    nights = brief.dates.duration_nights or 6
    people = max(1, brief.party.total)
    style = brief.style if isinstance(brief.style, str) else brief.style.value
    multiplier = _STYLE_MULTIPLIER.get(style, 1.0)

    flight_per_person: float | None = None
    if brief.origin_lat is not None and brief.origin_lon is not None:
        distance = _haversine_km(
            brief.origin_lat, brief.origin_lon, candidate.latitude, candidate.longitude
        )
        fixed, per_km = next((f, k) for upper, f, k in _FARE_BANDS if distance <= upper)
        # Round trip, with a modest seasonal allowance folded in.
        flight_per_person = round((fixed + per_km * distance) * 2 * 1.08, 2)
        candidate.estimated_flight_eur = flight_per_person

    tier = candidate.budget_tier or 2
    nightly = round(_TIER_NIGHTLY_EUR.get(tier, 95.0) * multiplier, 2)
    candidate.estimated_nightly_eur = nightly

    daily_living = _TIER_DAILY_EUR.get(tier, 60.0) * multiplier
    ground = nightly * nights + daily_living * people * (nights + 1)
    total = ground + (flight_per_person or 0.0) * people

    candidate.estimated_trip_total_eur = round(total, 2)
    return flight_per_person, nightly, candidate.estimated_trip_total_eur


def _budget_for_ranking(brief: TravelBrief) -> float | None:
    """The traveller's budget as a whole-trip EUR figure."""
    if brief.budget.amount_eur is None:
        return None
    if brief.budget.per_person:
        return brief.budget.amount_eur * max(1, brief.party.total)
    return brief.budget.amount_eur


def _fallback_rationale(
    candidates: list[DestinationCandidate], brief: TravelBrief
) -> RecommenderOutput:
    """Deterministic pitch used when no LLM is available.

    Built from the same numbers the LLM would see, so the no-key experience is
    plainer but never less accurate.
    """
    if not candidates:
        return RecommenderOutput(
            headline="No destinations matched",
            rationale="I could not find destinations matching those criteria.",
            follow_up_question="Would you like to loosen any of your requirements?",
        )

    top = candidates[0]
    parts: list[str] = []

    interests = ", ".join(brief.interests[:3]) if brief.interests else "your trip"
    parts.append(f"{top.label} is my top pick for {interests}.")

    if top.weather.score is not None:
        basis = (
            "the forecast" if top.weather.basis == "forecast"
            else "historical averages for that month"
        )
        temp = (
            f" with highs around {top.weather.avg_high_c:.0f}C"
            if top.weather.avg_high_c is not None else ""
        )
        parts.append(
            f"Weather scores {top.weather.score:.0f}/100 on {basis}{temp}."
        )

    if top.estimated_trip_total_eur:
        parts.append(
            f"I estimate about EUR {top.estimated_trip_total_eur:,.0f} for the whole trip."
        )

    if len(candidates) > 1:
        second = candidates[1]
        contrast = ""
        if (
            second.estimated_trip_total_eur and top.estimated_trip_total_eur
            and second.estimated_trip_total_eur < top.estimated_trip_total_eur
        ):
            saving = top.estimated_trip_total_eur - second.estimated_trip_total_eur
            contrast = f" and comes in about EUR {saving:,.0f} cheaper"
        elif (
            second.weather.score is not None and top.weather.score is not None
            and second.weather.score > top.weather.score
        ):
            contrast = " and has slightly better weather"
        parts.append(f"{second.label} is the closest alternative{contrast}.")

    return RecommenderOutput(
        headline=f"{top.label} leads a shortlist of {len(candidates)}",
        rationale=" ".join(parts),
        follow_up_question=(
            "Which one should I plan in full? You can name it or say 'the first one'."
        ),
    )


async def _llm_rationale(
    candidates: list[DestinationCandidate], brief: TravelBrief, trace: object
) -> RecommenderOutput | None:
    summary = [
        {
            "city": c.label,
            "composite_score": c.composite_score,
            "weather_score": c.weather.score,
            "weather_basis": c.weather.basis,
            "avg_high_c": c.weather.avg_high_c,
            "rainy_days": c.weather.rainy_days,
            "matched_interests": c.matched_interests,
            "estimated_total_eur": c.estimated_trip_total_eur,
            "in_best_season": (c.season_score or 0) >= 90,
            "note": c.blurb,
        }
        for c in candidates
    ]
    budget = _budget_for_ranking(brief)

    return await structured_call(
        RecommenderOutput,
        system_prompt=with_today(RECOMMENDER),
        user_prompt=(
            f"Traveller: {brief.party.adults} adults, {brief.party.children} children, "
            f"travelling {brief.dates.start} to {brief.dates.end} from "
            f"{brief.origin or 'an unspecified origin'}.\n"
            f"Interests: {', '.join(brief.interests) or 'not specified'}. "
            f"Pace: {brief.pace}. Style: {brief.style}. "
            f"Purpose: {brief.trip_purpose or 'not stated'}.\n"
            f"Whole-trip budget: "
            f"{f'EUR {budget:,.0f}' if budget else 'not stated'}.\n"
            f"Original message: {brief.raw_query!r}\n\n"
            f"Scored candidates (best first):\n{summary}\n\n"
            "Write the headline, rationale and follow-up question."
        ),
        purpose="write_recommendation",
        temperature=0.55,
        max_tokens=600,
        trace=trace,
    )


async def recommender_node(state: AgentState) -> AgentState:
    """Rank candidates and compose the shortlist message."""
    with time_agent("recommender"):
        STAGE_ENTERED.labels(stage="recommending").inc()

        brief = state.get("brief")
        candidates = list(state.get("candidates") or [])
        trace = state.get("langfuse_trace")
        tool_trace: list = []

        if brief is None or not candidates:
            return AgentState(
                stage="error", errors=["Nothing to recommend."], tool_trace=tool_trace
            )

        with span("agent.recommender", **{"recommend.count": len(candidates)}) as current:
            for candidate in candidates:
                estimate_trip_cost(candidate, brief)

            budget_eur = _budget_for_ranking(brief)
            ranked, weights = rank_candidates(candidates, brief, budget_eur)

            output = await _llm_rationale(ranked, brief, trace)
            generated_by = "llm"
            if output is None or not output.rationale.strip():
                output = _fallback_rationale(ranked, brief)
                generated_by = "rules"

            recommendation = Recommendation(
                candidates=ranked,
                headline=output.headline or f"{ranked[0].label} leads the shortlist",
                rationale=output.rationale,
                follow_up_question=(
                    output.follow_up_question
                    or "Which of these would you like me to plan in detail?"
                ),
                generated_by=generated_by,
            )

            current.set_attribute("recommend.top", ranked[0].city)
            current.set_attribute("recommend.top_score", ranked[0].composite_score or 0)
            current.set_attribute("recommend.generated_by", generated_by)

        langfuse_hook.log_span(
            trace,
            name="recommender",
            input_data={"weights": weights.as_dict()},
            output_data={
                "ranking": [(c.city, c.composite_score) for c in ranked],
                "headline": recommendation.headline,
            },
        )

        logger.info(
            "Ranked: %s (weights=%s)",
            [(c.city, c.composite_score) for c in ranked], weights.as_dict(),
        )

        return AgentState(
            candidates=ranked,
            recommendation=recommendation,
            stage="awaiting_choice",
            tool_trace=tool_trace,
            scratch={
                "score_weights": weights.as_dict(),
                "weight_rationale": weights.rationale,
                "budget_eur_total": budget_eur,
            },
        )


def month_of(value: date | None) -> int | None:
    return value.month if value else None
