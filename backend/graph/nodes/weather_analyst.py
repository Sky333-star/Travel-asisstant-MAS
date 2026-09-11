"""Weather Analyst agent -- scores every candidate against the trip window.

This is the node that makes the assistant's recommendations defensible. It
calls the weather MCP server once per candidate and attaches a real comfort
score, the numbers behind it, and the *basis* of that score.

Two details matter more than they look:

* **Basis honesty.** Open-Meteo forecasts run 16 days out. Beyond that the
  server returns multi-year climate normals instead, and that difference is
  propagated all the way to the UI. Presenting a 90-day-out climate average as
  "the forecast" is the most common way travel tools mislead people.
* **Bounded concurrency.** Candidates are scored in parallel, but through a
  semaphore. Open-Meteo is generous, not infinite, and hammering a free
  service that the whole project depends on is a poor trade for two seconds.
"""

from __future__ import annotations

import asyncio
import logging

from ...mcp import tools as mcp_tools
from ...observability import STAGE_ENTERED, langfuse_hook, span, time_agent
from ...schemas.travel import (
    ClimatePreference,
    DestinationCandidate,
    TravelBrief,
    WeatherAssessment,
)
from ..state import AgentState

logger = logging.getLogger(__name__)

# Open-Meteo has no published hard limit, but four concurrent requests is
# plenty to hide latency without being rude.
MAX_CONCURRENT_WEATHER_CALLS = 4


def _preference_for(brief: TravelBrief) -> str:
    """Map the brief's climate preference onto the weather server's vocabulary."""
    preference = brief.climate_preference
    value = preference.value if isinstance(preference, ClimatePreference) else preference
    if value and value != ClimatePreference.ANY.value:
        return value

    # Nothing stated: infer from interests, else default to mild.
    interests = {i.lower() for i in brief.interests}
    if interests & {"beach", "surfing", "diving", "islands"}:
        return "warm"
    if interests & {"skiing"}:
        return "snow"
    if interests & {"hiking", "mountains"}:
        return "mild"
    return "mild"


async def _score_one(
    candidate: DestinationCandidate,
    brief: TravelBrief,
    preference: str,
    semaphore: asyncio.Semaphore,
    tool_trace: list,
    check_air_quality: bool,
) -> DestinationCandidate:
    """Attach a weather assessment to one candidate."""
    async with semaphore:
        result = await mcp_tools.score_weather(
            candidate.latitude,
            candidate.longitude,
            brief.dates.start,
            brief.dates.end,
            preference,
            tool_trace,
        )

        if result and result.get("score") is not None:
            candidate.weather = WeatherAssessment(
                score=result.get("score"),
                basis=result.get("basis", "unavailable"),
                avg_high_c=result.get("avg_high_c"),
                avg_low_c=result.get("avg_low_c"),
                rainy_days=result.get("rainy_days"),
                precipitation_mm=result.get("precipitation_mm"),
                reasons=list(result.get("reasons") or []),
            )
        else:
            candidate.weather = WeatherAssessment(
                basis="unavailable",
                reasons=["Weather data could not be retrieved for this destination."],
            )

        # Only fetch air quality when a stated health note makes it relevant --
        # an extra request per candidate is not free, and most travellers do
        # not need it.
        if check_air_quality:
            air = await mcp_tools.get_air_quality(
                candidate.latitude, candidate.longitude, tool_trace
            )
            if air and air.get("available"):
                candidate.weather.air_quality_band = air.get("aqi_band")
                if air.get("aqi_band") in ("poor", "very poor", "extremely poor"):
                    candidate.weather.reasons.append(
                        f"Current air quality is {air['aqi_band']} "
                        f"(European AQI {air.get('european_aqi')})."
                    )

        return candidate


async def weather_analyst_node(state: AgentState) -> AgentState:
    """Score every candidate's weather for the trip window."""
    with time_agent("weather_analyst"):
        STAGE_ENTERED.labels(stage="weather").inc()

        brief = state.get("brief")
        candidates = list(state.get("candidates") or [])
        tool_trace: list = []

        if brief is None or not candidates:
            return AgentState(stage="recommending")

        if not brief.dates.is_complete:
            # Should not happen (the clarifier fills dates), but scoring
            # without a window would be meaningless rather than merely wrong.
            logger.warning("Weather analyst reached with incomplete dates; skipping.")
            return AgentState(stage="recommending")

        preference = _preference_for(brief)
        health_notes = set(brief.constraints.health_notes)
        check_air = bool(health_notes & {"asthma", "pollen_allergy"})

        semaphore = asyncio.Semaphore(MAX_CONCURRENT_WEATHER_CALLS)

        with span(
            "agent.weather_analyst",
            **{
                "weather.candidates": len(candidates),
                "weather.preference": preference,
                "weather.window": f"{brief.dates.start}..{brief.dates.end}",
            },
        ) as current:
            scored = await asyncio.gather(
                *(
                    _score_one(c, brief, preference, semaphore, tool_trace, check_air)
                    for c in candidates
                ),
                return_exceptions=True,
            )

            results: list[DestinationCandidate] = []
            for candidate, outcome in zip(candidates, scored, strict=True):
                if isinstance(outcome, Exception):
                    logger.warning(
                        "Weather scoring failed for %s: %s", candidate.city, outcome
                    )
                    candidate.weather = WeatherAssessment(
                        basis="unavailable",
                        reasons=["Weather service was unavailable for this destination."],
                    )
                    results.append(candidate)
                else:
                    results.append(outcome)

            with_scores = [c for c in results if c.weather.score is not None]
            current.set_attribute("weather.scored_ok", len(with_scores))
            current.set_attribute(
                "weather.basis_forecast",
                sum(1 for c in with_scores if c.weather.basis == "forecast"),
            )

        langfuse_hook.log_span(
            state.get("langfuse_trace"),
            name="weather_analyst",
            input_data={"preference": preference,
                        "window": f"{brief.dates.start}..{brief.dates.end}"},
            output_data={
                c.city: {"score": c.weather.score, "basis": c.weather.basis}
                for c in results
            },
        )

        logger.info(
            "Weather scored: %s",
            {c.city: c.weather.score for c in results},
        )

        return AgentState(
            candidates=results, stage="recommending", tool_trace=tool_trace
        )
