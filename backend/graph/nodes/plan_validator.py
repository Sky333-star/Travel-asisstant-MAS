"""Plan Validator agent -- the critic that decides whether a plan ships.

A two-tier design, because the two kinds of mistake need different tools:

**Tier 1 -- deterministic rules.** Budget arithmetic, layover minimums,
flight-time limits, walking distances, accessibility requirements, past dates.
These are objective, cheap, and an LLM would be *worse* at them. They run
always, including with no API key, and they are what make the validator
trustworthy.

**Tier 2 -- LLM critique.** Whether the plan actually delivers what the
traveller asked for; whether the sequencing makes sense for a human; what a
knowledgeable friend would notice. These are judgement calls that rules cannot
express. This tier is additive: it can raise issues, but a missing LLM never
weakens the hard checks.

Every issue carries a ``fix_hint`` naming the lever the planner should pull.
That is what turns "this plan is 300 EUR over budget" into a replan that is
actually cheaper, instead of a second identical plan.
"""

from __future__ import annotations

import logging
from datetime import date

from pydantic import BaseModel, Field

from ...llm import structured_call
from ...observability import (
    STAGE_ENTERED,
    VALIDATION_ISSUES,
    VALIDATION_RESULT,
    langfuse_hook,
    span,
    time_agent,
)
from ...schemas.plan import (
    FixAction,
    Severity,
    TripPlan,
    ValidationIssue,
    ValidationReport,
)
from ...schemas.travel import TravelBrief
from ..prompts import PLAN_CRITIC, with_today
from ..state import AgentState

logger = logging.getLogger(__name__)

# Tuning constants, gathered here so the validator's judgement is auditable
# rather than scattered through the code.
BUDGET_HARD_TOLERANCE = 0.08     # 8% over a firm budget is still a blocker.
BUDGET_SOFT_TOLERANCE = 0.25     # 25% over a soft budget becomes a blocker.
MIN_LAYOVER_MINUTES = 45         # Below this, a connection is not realistic.
COMFORTABLE_LAYOVER_MINUTES = 60
MAX_DAILY_WALKING_KM = 12.0
POOR_WEATHER_SCORE = 40.0
ISOLATED_WALKABILITY = 25
FAR_FROM_CENTRE_KM = 6.0

_PACE_LIMITS = {"relaxed": 3, "balanced": 4, "packed": 6}


class CriticIssue(BaseModel):
    """One qualitative issue from the LLM critic."""

    title: str = Field(max_length=140)
    detail: str = Field(default="", max_length=600)
    severity: str = Field(default="warning")
    suggested_fix: str = Field(default="none")


class CriticOutput(BaseModel):
    issues: list[CriticIssue] = Field(default_factory=list)
    overall_verdict: str = Field(default="", max_length=500)


def _issue(
    code: str,
    severity: Severity,
    message: str,
    detail: str | None = None,
    fix: FixAction = FixAction.NONE,
    magnitude: float | None = None,
) -> ValidationIssue:
    VALIDATION_ISSUES.labels(code=code, severity=severity.value).inc()
    return ValidationIssue(
        code=code, severity=severity, message=message,
        detail=detail, fix_hint=fix, magnitude=magnitude,
    )


# --------------------------------------------------------------------------
# Tier 1: deterministic checks
# --------------------------------------------------------------------------


def check_budget(plan: TripPlan, brief: TravelBrief) -> list[ValidationIssue]:
    """Is the plan affordable against the stated budget?"""
    issues: list[ValidationIssue] = []
    budget = plan.costs.budget_eur
    if not budget:
        return issues

    total = plan.costs.total_eur
    overage = total - budget
    if overage <= 0:
        return issues

    ratio = overage / budget
    tolerance = BUDGET_HARD_TOLERANCE if brief.budget.is_hard_limit else BUDGET_SOFT_TOLERANCE

    # Attribute the overspend to the biggest line item so the fix hint points
    # somewhere useful rather than just saying "spend less".
    if plan.costs.lodging_eur >= plan.costs.flights_eur:
        fix = FixAction.CHEAPER_HOTEL
        driver = f"lodging is EUR {plan.costs.lodging_eur:,.0f} of the total"
    else:
        fix = FixAction.CHEAPER_FLIGHT
        driver = f"flights are EUR {plan.costs.flights_eur:,.0f} of the total"

    severity = Severity.BLOCKER if ratio > tolerance else Severity.WARNING
    issues.append(
        _issue(
            "budget_exceeded",
            severity,
            f"The plan costs about EUR {total:,.0f}, which is EUR {overage:,.0f} "
            f"({ratio:.0%}) over your budget of EUR {budget:,.0f}.",
            f"Largest driver: {driver}.",
            fix,
            magnitude=round(overage, 2),
        )
    )
    return issues


def check_flights(plan: TripPlan, brief: TravelBrief) -> list[ValidationIssue]:
    """Are the flights realistic and within the traveller's stated limits?"""
    issues: list[ValidationIssue] = []

    if brief.origin and plan.outbound_flight is None:
        issues.append(
            _issue(
                "no_flights_found",
                Severity.WARNING,
                f"I couldn't find flight options from {brief.origin}.",
                "The plan covers the destination only. Check flights separately.",
                FixAction.NONE,
            )
        )
        return issues

    for option in (plan.outbound_flight, plan.inbound_flight):
        if option is None:
            continue
        label = "Outbound" if option.direction == "outbound" else "Return"

        if brief.constraints.max_stops is not None and option.stops > brief.constraints.max_stops:
            issues.append(
                _issue(
                    "too_many_stops",
                    Severity.BLOCKER,
                    f"{label} flight has {option.stops} stop(s); you asked for at "
                    f"most {brief.constraints.max_stops}.",
                    None,
                    FixAction.FEWER_STOPS,
                    magnitude=option.stops - brief.constraints.max_stops,
                )
            )

        if brief.constraints.max_flight_hours and option.total_duration_minutes:
            hours = option.total_duration_minutes / 60.0
            if hours > brief.constraints.max_flight_hours + 0.5:
                issues.append(
                    _issue(
                        "flight_too_long",
                        Severity.BLOCKER,
                        f"{label} journey takes {hours:.1f} hours; you asked for "
                        f"under {brief.constraints.max_flight_hours:.0f}.",
                        None,
                        FixAction.FEWER_STOPS,
                        magnitude=round(hours - brief.constraints.max_flight_hours, 1),
                    )
                )

        for layover in option.layovers:
            if layover.minutes < MIN_LAYOVER_MINUTES:
                issues.append(
                    _issue(
                        "layover_too_short",
                        Severity.BLOCKER,
                        f"{label} connection in {layover.city or layover.airport} is "
                        f"only {layover.minutes} minutes -- not enough to make it.",
                        f"Allow at least {COMFORTABLE_LAYOVER_MINUTES} minutes.",
                        FixAction.LONGER_LAYOVER,
                        magnitude=MIN_LAYOVER_MINUTES - layover.minutes,
                    )
                )
            elif layover.minutes < COMFORTABLE_LAYOVER_MINUTES:
                issues.append(
                    _issue(
                        "layover_tight",
                        Severity.WARNING,
                        f"{label} connection in {layover.city or layover.airport} is "
                        f"{layover.minutes} minutes -- tight if the first leg is late.",
                        None,
                        FixAction.LONGER_LAYOVER,
                    )
                )

    # A return that leaves before the outbound lands is a data error, but a
    # cheap check that would be embarrassing to miss.
    if plan.start_date and plan.end_date and plan.end_date < plan.start_date:
        issues.append(
            _issue(
                "dates_reversed",
                Severity.BLOCKER,
                "The return date falls before the departure date.",
                None,
                FixAction.SHIFT_DATES,
            )
        )

    return issues


def check_lodging(plan: TripPlan, brief: TravelBrief) -> list[ValidationIssue]:
    """Is the accommodation usable for this specific traveller?"""
    issues: list[ValidationIssue] = []
    hotel = plan.hotel

    if hotel is None:
        issues.append(
            _issue(
                "no_lodging",
                Severity.BLOCKER,
                "No accommodation could be found for these dates.",
                "OpenStreetMap may have sparse lodging data for this area.",
                FixAction.CHANGE_DESTINATION,
            )
        )
        return issues

    if brief.constraints.accessibility_required and not hotel.wheelchair_accessible:
        issues.append(
            _issue(
                "lodging_not_accessible",
                Severity.BLOCKER,
                f"{hotel.name} is not tagged as step-free, and you need "
                f"accessible accommodation.",
                "OSM accessibility tagging is incomplete -- confirm with the "
                "property directly if you keep this one.",
                FixAction.ACCESSIBLE_HOTEL,
            )
        )

    if (hotel.distance_from_centre_km or 0) > FAR_FROM_CENTRE_KM:
        issues.append(
            _issue(
                "lodging_far_from_centre",
                Severity.WARNING,
                f"{hotel.name} is {hotel.distance_from_centre_km:.1f} km from the "
                f"centre, so expect a commute to most activities.",
                None,
                FixAction.CLOSER_HOTEL,
                magnitude=hotel.distance_from_centre_km,
            )
        )

    if hotel.walkability_score is not None and hotel.walkability_score < ISOLATED_WALKABILITY:
        issues.append(
            _issue(
                "lodging_isolated",
                Severity.WARNING,
                f"There is very little within walking distance of {hotel.name} -- "
                f"few restaurants, shops or transit stops nearby.",
                f"Walkability score {hotel.walkability_score}/100.",
                FixAction.CLOSER_HOTEL,
            )
        )

    # Room capacity: two per room is the assumption the planner made.
    capacity = hotel.rooms * 2
    if brief.party.total > capacity:
        issues.append(
            _issue(
                "insufficient_room_capacity",
                Severity.WARNING,
                f"{brief.party.total} travellers in {hotel.rooms} room(s) may not "
                f"fit; confirm the room configuration.",
                None,
                FixAction.NONE,
                magnitude=brief.party.total - capacity,
            )
        )

    return issues


def check_itinerary(plan: TripPlan, brief: TravelBrief) -> list[ValidationIssue]:
    """Is the day-by-day plan actually livable?"""
    issues: list[ValidationIssue] = []
    pace = brief.pace if isinstance(brief.pace, str) else brief.pace.value
    limit = _PACE_LIMITS.get(pace, 4)

    if not plan.days:
        issues.append(
            _issue(
                "no_itinerary",
                Severity.WARNING,
                "No day-by-day activities could be built for this destination.",
                "OpenStreetMap may have limited POI coverage here.",
                FixAction.NONE,
            )
        )
        return issues

    overpacked = [d for d in plan.days if len(d.activities) > limit]
    if overpacked:
        issues.append(
            _issue(
                "days_overpacked",
                Severity.WARNING,
                f"{len(overpacked)} day(s) have more than {limit} activities, which "
                f"is a lot for a '{pace}' trip.",
                f"Days affected: {', '.join(str(d.day_number) for d in overpacked)}.",
                FixAction.FEWER_ACTIVITIES,
                magnitude=len(overpacked),
            )
        )

    long_walks = [d for d in plan.days if d.total_walking_km > MAX_DAILY_WALKING_KM]
    if long_walks:
        worst = max(long_walks, key=lambda d: d.total_walking_km)
        severity = (
            Severity.BLOCKER
            if "reduced_mobility" in brief.constraints.health_notes
            or brief.constraints.accessibility_required
            else Severity.WARNING
        )
        issues.append(
            _issue(
                "excessive_walking",
                severity,
                f"Day {worst.day_number} covers about {worst.total_walking_km:.1f} km "
                f"on foot between stops.",
                "Consider public transport or dropping a stop."
                if severity == Severity.WARNING
                else "This is not feasible given the mobility needs you mentioned.",
                FixAction.FEWER_ACTIVITIES,
                magnitude=round(worst.total_walking_km, 1),
            )
        )

    # Empty middle days: arrival and departure days are expected to be light,
    # the ones between are not.
    middle = plan.days[1:-1] if len(plan.days) > 2 else []
    empty = [d for d in middle if not d.activities]
    if empty and len(empty) > len(middle) / 2:
        issues.append(
            _issue(
                "sparse_itinerary",
                Severity.WARNING,
                f"{len(empty)} of {len(middle)} full days have nothing scheduled.",
                "There may be limited mapped attractions matching your interests here.",
                FixAction.NONE,
            )
        )

    # Accessibility of individual stops, where OSM has tagged it.
    if brief.constraints.accessibility_required:
        inaccessible = [
            activity.name
            for day in plan.days
            for activity in day.activities
            if activity.wheelchair == "no"
        ]
        if inaccessible:
            issues.append(
                _issue(
                    "activity_not_accessible",
                    Severity.WARNING,
                    f"{len(inaccessible)} scheduled stop(s) are tagged as not "
                    f"wheelchair accessible.",
                    f"For example: {', '.join(inaccessible[:3])}.",
                    FixAction.FEWER_ACTIVITIES,
                )
            )

    return issues


def check_weather(plan: TripPlan, brief: TravelBrief) -> list[ValidationIssue]:
    """Is the weather workable for what the traveller wants to do?"""
    issues: list[ValidationIssue] = []
    summary = (plan.weather or {}).get("summary") or {}
    days = (plan.weather or {}).get("days") or []

    if not days:
        return issues

    rainy = summary.get("rainy_days", 0) or 0
    total_days = len(days)
    if total_days and rainy / total_days > 0.6:
        outdoor = {"beach", "hiking", "surfing", "diving", "cycling", "mountains"}
        wants_outdoor = bool(set(brief.interests) & outdoor)
        issues.append(
            _issue(
                "predominantly_wet",
                Severity.WARNING if not wants_outdoor else Severity.BLOCKER,
                f"{rainy} of {total_days} days show rain.",
                "Your plans are mostly outdoors, so this materially affects the trip."
                if wants_outdoor
                else "Indoor alternatives are scheduled where possible.",
                FixAction.SHIFT_DATES if brief.dates.flexible else FixAction.NONE,
                magnitude=rainy,
            )
        )

    worst = summary.get("worst_condition") or {}
    if worst.get("severity", 0) >= 3:
        issues.append(
            _issue(
                "severe_weather",
                Severity.WARNING,
                f"At least one day forecasts {worst.get('label', 'severe weather')}.",
                "Keep a flexible indoor option for that day.",
                FixAction.NONE,
            )
        )

    return issues


def check_dates(plan: TripPlan) -> list[ValidationIssue]:
    """Sanity checks on the trip window itself."""
    issues: list[ValidationIssue] = []
    today = date.today()

    if plan.start_date and plan.start_date < today:
        issues.append(
            _issue(
                "departure_in_past",
                Severity.BLOCKER,
                f"The departure date {plan.start_date.isoformat()} has already passed.",
                None,
                FixAction.SHIFT_DATES,
            )
        )
    elif plan.start_date and (plan.start_date - today).days <= 2:
        issues.append(
            _issue(
                "very_short_notice",
                Severity.WARNING,
                "Departure is within 48 hours, so fares are at their most expensive "
                "and availability is thin.",
                None,
                FixAction.NONE,
            )
        )

    return issues


def run_rule_checks(plan: TripPlan, brief: TravelBrief) -> tuple[list[ValidationIssue], list[str]]:
    """Run every deterministic check. Returns issues and the check names run."""
    checks = {
        "dates": check_dates(plan),
        "budget": check_budget(plan, brief),
        "flights": check_flights(plan, brief),
        "lodging": check_lodging(plan, brief),
        "itinerary": check_itinerary(plan, brief),
        "weather": check_weather(plan, brief),
    }
    issues = [issue for group in checks.values() for issue in group]
    return issues, list(checks.keys())


# --------------------------------------------------------------------------
# Tier 2: LLM critique
# --------------------------------------------------------------------------


def _plan_digest(plan: TripPlan, brief: TravelBrief) -> str:
    """Compact plan summary for the critic. Small enough to keep the model focused."""
    lines = [
        f"Destination: {plan.destination}, {plan.country}",
        f"Dates: {plan.start_date} to {plan.end_date} ({plan.nights} nights)",
        f"Travellers: {plan.travellers}",
        f"Traveller wants: interests={', '.join(brief.interests) or 'unspecified'}, "
        f"pace={brief.pace}, style={brief.style}, "
        f"purpose={brief.trip_purpose or 'unstated'}",
        f"Original request: {brief.raw_query!r}",
        f"Total estimated cost: EUR {plan.costs.total_eur:,.0f} "
        f"(budget: {f'EUR {plan.costs.budget_eur:,.0f}' if plan.costs.budget_eur else 'none'})",
    ]

    if plan.outbound_flight:
        out = plan.outbound_flight
        lines.append(
            f"Outbound: {out.stops} stop(s), {out.duration_hours}h, "
            f"arrives {out.legs[-1].arrive_local if out.legs else '?'}"
        )
    if plan.inbound_flight:
        back = plan.inbound_flight
        lines.append(
            f"Return: {back.stops} stop(s), "
            f"departs {back.legs[0].depart_local if back.legs else '?'}"
        )
    if plan.hotel:
        lines.append(
            f"Hotel: {plan.hotel.name} ({plan.hotel.lodging_type}, "
            f"{plan.hotel.stars} star, {plan.hotel.distance_from_centre_km} km from centre)"
        )

    for day in plan.days:
        activities = ", ".join(
            f"{a.name} ({a.start_time})" for a in day.activities
        ) or "nothing scheduled"
        weather_note = next(
            (n for n in day.notes if "C." in n or "C /" in n), ""
        )
        lines.append(f"Day {day.day_number} ({day.day_date}): {activities}. {weather_note}")

    return "\n".join(lines)


async def _llm_critique(
    plan: TripPlan, brief: TravelBrief, rule_issues: list[ValidationIssue], trace: object
) -> CriticOutput | None:
    already = [f"{i.code}: {i.message}" for i in rule_issues]
    return await structured_call(
        CriticOutput,
        system_prompt=with_today(PLAN_CRITIC),
        user_prompt=(
            f"{_plan_digest(plan, brief)}\n\n"
            f"Issues the rule engine already found (do not repeat these):\n"
            f"{chr(10).join(already) if already else 'none'}\n\n"
            "What else is wrong with this plan? severity must be 'blocker', "
            "'warning' or 'info'. suggested_fix must be one of: cheaper_flight, "
            "cheaper_hotel, closer_hotel, fewer_activities, shorten_trip, "
            "fewer_stops, longer_layover, shift_dates, change_destination, none."
        ),
        purpose="critique_plan",
        temperature=0.3,
        max_tokens=800,
        trace=trace,
    )


def _merge_critic_issues(critique: CriticOutput) -> list[ValidationIssue]:
    """Convert critic output into validation issues, defensively."""
    issues: list[ValidationIssue] = []
    valid_fixes = {action.value for action in FixAction}

    for raw in critique.issues[:6]:
        severity_text = (raw.severity or "warning").strip().lower()
        severity = {
            "blocker": Severity.BLOCKER,
            "warning": Severity.WARNING,
            "info": Severity.INFO,
        }.get(severity_text, Severity.WARNING)

        # An LLM declaring a blocker gets demoted to a warning. The model has
        # no way to verify hard constraints, and letting it force a replan
        # loop on a subjective judgement wastes the revision budget.
        if severity == Severity.BLOCKER:
            severity = Severity.WARNING

        fix_text = (raw.suggested_fix or "none").strip().lower()
        fix = FixAction(fix_text) if fix_text in valid_fixes else FixAction.NONE

        issues.append(
            _issue("critic_" + fix_text if fix_text != "none" else "critic_observation",
                   severity, raw.title, raw.detail or None, fix)
        )
    return issues


# --------------------------------------------------------------------------
# Node
# --------------------------------------------------------------------------


def compute_score(issues: list[ValidationIssue]) -> float:
    """Overall plan quality, 0-100."""
    penalty = 0.0
    for issue in issues:
        severity = (
            issue.severity.value if isinstance(issue.severity, Severity) else issue.severity
        )
        penalty += {"blocker": 26.0, "warning": 8.0, "info": 2.0}.get(severity, 5.0)
    return round(max(0.0, 100.0 - penalty), 1)


def summarise(issues: list[ValidationIssue], passed: bool) -> str:
    blockers = sum(
        1 for i in issues
        if (i.severity.value if isinstance(i.severity, Severity) else i.severity) == "blocker"
    )
    warnings = sum(
        1 for i in issues
        if (i.severity.value if isinstance(i.severity, Severity) else i.severity) == "warning"
    )
    if passed and not issues:
        return "The plan passed every check with no issues."
    if passed:
        return (
            f"The plan is workable with {warnings} thing(s) worth knowing about."
        )
    return f"The plan has {blockers} blocking issue(s) and {warnings} warning(s)."


async def plan_validator_node(state: AgentState) -> AgentState:
    """Validate the current plan and decide whether to ship or revise."""
    with time_agent("plan_validator"):
        STAGE_ENTERED.labels(stage="validating").inc()

        plan = state.get("plan")
        brief = state.get("brief")
        revision = state.get("revision", 0)

        if plan is None or brief is None:
            return AgentState(stage="error", errors=["Nothing to validate."])

        with span("agent.plan_validator", **{"validate.revision": revision}) as current:
            issues, checks_run = run_rule_checks(plan, brief)

            critique = await _llm_critique(
                plan, brief, issues, state.get("langfuse_trace")
            )
            if critique is not None:
                issues.extend(_merge_critic_issues(critique))
                checks_run.append("llm_critique")

            blockers = [
                i for i in issues
                if (i.severity.value if isinstance(i.severity, Severity) else i.severity)
                == "blocker"
            ]
            passed = not blockers

            report = ValidationReport(
                passed=passed,
                score=compute_score(issues),
                issues=issues,
                checks_run=checks_run,
                revision=revision,
                summary=summarise(issues, passed),
            )

            VALIDATION_RESULT.labels(passed=str(passed).lower()).inc()
            current.set_attribute("validate.passed", passed)
            current.set_attribute("validate.score", report.score)
            current.set_attribute("validate.blockers", len(blockers))
            current.set_attribute("validate.issues", len(issues))

        plan.validation = report

        langfuse_hook.log_span(
            state.get("langfuse_trace"),
            name="plan_validator",
            input_data={"revision": revision},
            output_data={
                "passed": passed,
                "score": report.score,
                "issues": [i.code for i in issues],
            },
        )

        logger.info(
            "Validation revision %d: passed=%s score=%.1f issues=%s",
            revision, passed, report.score, [i.code for i in issues],
        )

        return AgentState(
            plan=plan,
            validation=report,
            fix_actions=report.fix_actions(),
            stage="presenting" if passed else "revising",
        )
