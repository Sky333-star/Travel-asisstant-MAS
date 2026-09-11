"""Presenter agent -- turns the validated plan into something worth reading.

The plan is already correct by the time it reaches here. This node's job is to
make it *legible*: a narrative that frames the trip, day titles that say what
each day is, highlights that name real places, and packing notes driven by the
actual forecast.

It also handles the case the rest of the graph works hard to avoid: a plan that
never passed validation. Rather than dumping a broken plan or an error, it
presents the best attempt with its unresolved issues stated plainly at the top.
Being honest about a compromised plan is far more useful than hiding it.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from ...llm import structured_call
from ...observability import PLAN_REVISIONS, STAGE_ENTERED, langfuse_hook, span, time_agent
from ...schemas.plan import Severity, TripPlan
from ...schemas.travel import TravelBrief
from ..prompts import ITINERARY_WRITER, with_today
from ..state import AgentState

logger = logging.getLogger(__name__)


class PresentationOutput(BaseModel):
    narrative: str = Field(default="", max_length=2000)
    day_titles: dict[str, str] = Field(default_factory=dict)
    highlights: list[str] = Field(default_factory=list)
    packing_notes: list[str] = Field(default_factory=list)


def _fallback_packing_notes(plan: TripPlan, brief: TravelBrief) -> list[str]:
    """Packing advice derived from the forecast numbers, with no LLM."""
    notes: list[str] = []
    summary = (plan.weather or {}).get("summary") or {}
    days = (plan.weather or {}).get("days") or []

    high = summary.get("avg_high_c")
    low = summary.get("avg_low_c")
    rainy = summary.get("rainy_days", 0) or 0

    if high is not None:
        if high >= 28:
            notes.append(
                f"Daytime highs average {high:.0f}C -- light, breathable clothing "
                f"and a refillable water bottle."
            )
        elif high >= 20:
            notes.append(f"Comfortable {high:.0f}C days; a light layer for evenings.")
        elif high >= 10:
            notes.append(f"Cool at {high:.0f}C -- bring a proper jacket.")
        else:
            notes.append(f"Cold, averaging {high:.0f}C -- insulated coat, hat and gloves.")

    if low is not None and low <= 5:
        notes.append(f"Nights drop to about {low:.0f}C, so pack warm layers.")

    if rainy >= 2:
        notes.append(
            f"{rainy:.0f} days show rain -- a compact umbrella or waterproof shell."
        )

    uv_values = [d.get("uv_index_max") for d in days if d.get("uv_index_max") is not None]
    if uv_values and max(uv_values) >= 8:
        notes.append(
            f"UV index peaks at {max(uv_values):.0f} -- high-factor sunscreen and a hat."
        )

    walking = sum(d.total_walking_km for d in plan.days)
    if walking > 20:
        notes.append(
            f"The itinerary covers about {walking:.0f} km on foot -- broken-in "
            f"walking shoes matter here."
        )

    if brief.constraints.accessibility_required:
        notes.append(
            "Confirm step-free access directly with each venue; OpenStreetMap "
            "accessibility tagging is incomplete."
        )
    if "asthma" in brief.constraints.health_notes:
        notes.append("Carry your inhaler; air quality varies day to day.")

    if plan.briefing.currency_code and plan.briefing.currency_code != "EUR":
        notes.append(
            f"Local currency is the {plan.briefing.currency_name or plan.briefing.currency_code}"
            f" ({plan.briefing.currency_code})."
        )
    if plan.briefing.drives_on == "left":
        notes.append("Traffic drives on the left -- worth remembering when crossing.")

    return notes


def _fallback_highlights(plan: TripPlan) -> list[str]:
    """Highlights taken straight from the scheduled itinerary."""
    highlights: list[str] = []
    for day in plan.days:
        for activity in day.activities:
            highlights.append(f"{activity.name} on day {day.day_number}")
            break
        if len(highlights) >= 4:
            break

    if plan.hotel:
        highlights.append(
            f"Staying at {plan.hotel.name}"
            + (
                f", {plan.hotel.distance_from_centre_km:.1f} km from the centre"
                if plan.hotel.distance_from_centre_km is not None else ""
            )
        )
    return highlights[:5]


def _fallback_narrative(plan: TripPlan, brief: TravelBrief) -> str:
    """Deterministic trip summary used when no LLM is available."""
    parts: list[str] = [
        f"{plan.nights} nights in {plan.destination}"
        + (f", {plan.country}" if plan.country else "")
        + f", from {plan.start_date} to {plan.end_date}"
        + f" for {plan.travellers} traveller{'s' if plan.travellers != 1 else ''}."
    ]

    if plan.outbound_flight:
        out = plan.outbound_flight
        route = " to ".join([out.legs[0].from_iata, out.legs[-1].to_iata]) if out.legs else ""
        parts.append(
            f"Outbound {route} takes about {out.duration_hours}h"
            + (f" with {out.stops} stop" if out.stops else " non-stop")
            + "."
        )

    summary = (plan.weather or {}).get("summary") or {}
    if summary.get("avg_high_c") is not None:
        parts.append(
            f"Expect highs around {summary['avg_high_c']:.0f}C"
            + (
                f" with {summary.get('rainy_days', 0)} rainy day(s)"
                if summary.get("rainy_days") else " and little rain"
            )
            + "."
        )

    parts.append(
        f"Estimated total cost is EUR {plan.costs.total_eur:,.0f} "
        f"(EUR {plan.costs.per_person_eur:,.0f} per person)."
    )

    if plan.validation and plan.validation.warnings:
        parts.append(
            f"There {'is' if len(plan.validation.warnings) == 1 else 'are'} "
            f"{len(plan.validation.warnings)} thing(s) worth reviewing below."
        )

    return " ".join(parts)


async def _llm_presentation(
    plan: TripPlan, brief: TravelBrief, trace: object
) -> PresentationOutput | None:
    day_lines = []
    for day in plan.days:
        activities = ", ".join(a.name for a in day.activities) or "nothing scheduled"
        weather = next((n for n in day.notes if "C" in n), "")
        day_lines.append(
            f"Day {day.day_number} ({day.day_date}): {activities}. "
            f"{day.total_walking_km:.1f} km walking. {weather}"
        )

    issues = ""
    if plan.validation and plan.validation.issues:
        issues = "\n".join(f"- {i.message}" for i in plan.validation.issues[:6])

    return await structured_call(
        PresentationOutput,
        system_prompt=with_today(ITINERARY_WRITER),
        user_prompt=(
            f"Traveller asked: {brief.raw_query!r}\n"
            f"Interests: {', '.join(brief.interests) or 'unspecified'}. "
            f"Pace: {brief.pace}. Purpose: {brief.trip_purpose or 'unstated'}.\n\n"
            f"Destination: {plan.destination}, {plan.country}\n"
            f"Dates: {plan.start_date} to {plan.end_date} ({plan.nights} nights), "
            f"{plan.travellers} traveller(s)\n"
            f"Flights: outbound "
            f"{plan.outbound_flight.duration_hours if plan.outbound_flight else '?'}h "
            f"with {plan.outbound_flight.stops if plan.outbound_flight else '?'} stop(s)\n"
            f"Hotel: {plan.hotel.name if plan.hotel else 'none'}\n"
            f"Total cost: EUR {plan.costs.total_eur:,.0f}\n\n"
            f"Itinerary:\n" + "\n".join(day_lines) + "\n\n"
            + (f"Known issues to acknowledge honestly:\n{issues}\n\n" if issues else "")
            + "Write the narrative, a title for each day (day_titles keyed by day "
            "number as a string, e.g. \"1\"), highlights, and packing_notes."
        ),
        purpose="present_plan",
        temperature=0.6,
        max_tokens=1100,
        trace=trace,
    )


def _compose_message(plan: TripPlan, brief: TravelBrief) -> str:
    """The chat message that accompanies the plan card."""
    lines: list[str] = []

    if plan.validation and not plan.validation.passed:
        blockers = plan.validation.blockers
        lines.append(
            f"Here's the best plan I could build for {plan.destination}, but "
            f"{'it still has' if blockers else 'note'} "
            f"{len(blockers)} unresolved issue{'s' if len(blockers) != 1 else ''} "
            f"after {plan.revision} revision{'s' if plan.revision != 1 else ''}:"
        )
        for issue in blockers[:3]:
            lines.append(f"  - {issue.message}")
        lines.append("")

    lines.append(plan.narrative or _fallback_narrative(plan, brief))

    if plan.validation and plan.validation.warnings:
        lines.append("")
        lines.append("Worth knowing:")
        for issue in plan.validation.warnings[:4]:
            lines.append(f"  - {issue.message}")

    return "\n".join(lines)


async def presenter_node(state: AgentState) -> AgentState:
    """Write the narrative and finalise the plan for display."""
    with time_agent("presenter"):
        STAGE_ENTERED.labels(stage="presenting").inc()

        plan = state.get("plan")
        brief = state.get("brief")
        revision = state.get("revision", 0)

        if plan is None or brief is None:
            return AgentState(
                stage="error",
                errors=["No plan to present."],
                final_message="I wasn't able to build a plan. Could you try rephrasing?",
            )

        with span("agent.presenter", **{"present.revision": revision}) as current:
            presentation = await _llm_presentation(plan, brief, state.get("langfuse_trace"))

            if presentation is not None and presentation.narrative.strip():
                plan.narrative = presentation.narrative.strip()
                plan.highlights = presentation.highlights[:5] or _fallback_highlights(plan)
                plan.packing_notes = (
                    presentation.packing_notes[:6] or _fallback_packing_notes(plan, brief)
                )
                for day in plan.days:
                    title = presentation.day_titles.get(str(day.day_number))
                    if title:
                        day.title = title.strip()[:80]
                current.set_attribute("present.generated_by", "llm")
            else:
                plan.narrative = _fallback_narrative(plan, brief)
                plan.highlights = _fallback_highlights(plan)
                plan.packing_notes = _fallback_packing_notes(plan, brief)
                current.set_attribute("present.generated_by", "rules")

            # Surface anything the planner had to compromise on.
            selection_note = (state.get("scratch") or {}).get("selection_note")
            if selection_note:
                plan.warnings.insert(0, selection_note)
            for assumption in (state.get("scratch") or {}).get("assumptions", []):
                plan.warnings.append(f"Assumption: {assumption}.")
            for note in brief.notes:
                if note not in plan.warnings:
                    plan.warnings.append(note)

            message = _compose_message(plan, brief)
            PLAN_REVISIONS.observe(revision)

            current.set_attribute("present.total_eur", plan.costs.total_eur)
            current.set_attribute(
                "present.passed", bool(plan.validation and plan.validation.passed)
            )

        langfuse_hook.log_span(
            state.get("langfuse_trace"),
            name="presenter",
            output_data={"narrative": plan.narrative[:500], "revision": revision},
        )
        langfuse_hook.finalise(
            state.get("langfuse_trace"),
            output={
                "destination": plan.destination,
                "total_eur": plan.costs.total_eur,
                "revisions": revision,
                "validation_passed": bool(plan.validation and plan.validation.passed),
            },
        )

        logger.info(
            "Presented plan for %s after %d revision(s).", plan.destination, revision
        )

        return AgentState(
            plan=plan,
            stage="done",
            final_message=message,
            messages=[{"role": "assistant", "content": message}],
        )


def severity_of(issue: object) -> str:
    """Normalise a severity that may be an enum or a string."""
    value = getattr(issue, "severity", None)
    return value.value if isinstance(value, Severity) else str(value or "info")
