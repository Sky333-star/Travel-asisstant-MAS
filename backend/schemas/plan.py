"""Domain models for the planning and validation half of the graph.

The Itinerary Planner emits a :class:`TripPlan`; the Plan Validator inspects it
and emits a :class:`ValidationReport`. The report is deliberately *machine
actionable*: every issue carries a ``fix_hint`` that the planner reads on the
next revision, which is what turns the critic loop into something that
converges instead of just complaining.
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Severity(str, Enum):
    BLOCKER = "blocker"      # The plan cannot be executed as written.
    WARNING = "warning"      # Executable, but the traveller should know.
    INFO = "info"            # Worth mentioning, no action needed.


class FixAction(str, Enum):
    """The concrete lever the planner should pull on the next revision."""

    CHEAPER_FLIGHT = "cheaper_flight"
    CHEAPER_HOTEL = "cheaper_hotel"
    CLOSER_HOTEL = "closer_hotel"
    ACCESSIBLE_HOTEL = "accessible_hotel"
    FEWER_ACTIVITIES = "fewer_activities"
    SHORTEN_TRIP = "shorten_trip"
    FEWER_STOPS = "fewer_stops"
    LONGER_LAYOVER = "longer_layover"
    SHIFT_DATES = "shift_dates"
    CHANGE_DESTINATION = "change_destination"
    NONE = "none"


class ValidationIssue(BaseModel):
    code: str
    severity: Severity
    message: str
    detail: str | None = None
    fix_hint: FixAction = FixAction.NONE
    magnitude: float | None = None  # e.g. how many EUR over budget.


class ValidationReport(BaseModel):
    """Verdict on a candidate plan."""

    model_config = ConfigDict(use_enum_values=True)

    passed: bool = False
    score: float = Field(default=0.0, ge=0.0, le=100.0)
    issues: list[ValidationIssue] = Field(default_factory=list)
    checks_run: list[str] = Field(default_factory=list)
    revision: int = 0
    summary: str = ""

    @property
    def blockers(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == Severity.BLOCKER.value]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == Severity.WARNING.value]

    def fix_actions(self) -> list[str]:
        """Distinct, ordered fix levers -- blockers first."""
        ordered: list[str] = []
        for bucket in (Severity.BLOCKER.value, Severity.WARNING.value):
            for issue in self.issues:
                if issue.severity == bucket and issue.fix_hint != FixAction.NONE:
                    hint = (
                        issue.fix_hint.value
                        if isinstance(issue.fix_hint, FixAction)
                        else issue.fix_hint
                    )
                    if hint not in ordered:
                        ordered.append(hint)
        return ordered


class FlightLeg(BaseModel):
    from_iata: str
    from_name: str | None = None
    from_city: str | None = None
    to_iata: str
    to_name: str | None = None
    to_city: str | None = None
    carrier_code: str | None = None
    carrier_name: str | None = None
    flight_number: str | None = None
    depart_local: str | None = None
    arrive_local: str | None = None
    duration_minutes: int | None = None
    distance_km: float | None = None


class Layover(BaseModel):
    airport: str
    city: str | None = None
    minutes: int


class FlightOption(BaseModel):
    """One directional itinerary. Prices are modelled -- see ``pricing``."""

    id: str
    direction: Literal["outbound", "inbound"] = "outbound"
    stops: int = 0
    legs: list[FlightLeg] = Field(default_factory=list)
    layovers: list[Layover] = Field(default_factory=list)
    total_duration_minutes: int | None = None
    price_per_person_eur: float | None = None
    price_total_eur: float | None = None
    cabin: str = "economy"
    pricing: Literal["estimated", "live"] = "estimated"
    data_basis: str = "modelled"

    @property
    def duration_hours(self) -> float | None:
        return round(self.total_duration_minutes / 60.0, 1) if self.total_duration_minutes else None


class HotelOption(BaseModel):
    """A real OSM property with a modelled nightly rate."""

    id: str
    name: str
    lodging_type: str = "hotel"
    stars: int | None = None
    stars_source: Literal["osm_tag", "inferred"] = "inferred"
    latitude: float | None = None
    longitude: float | None = None
    distance_from_centre_km: float | None = None
    address: str | None = None
    website: str | None = None
    phone: str | None = None
    amenities: list[str] = Field(default_factory=list)
    wheelchair_accessible: bool = False
    estimated_nightly_eur: float | None = None
    estimated_total_eur: float | None = None
    rooms: int = 1
    walkability_score: int | None = None
    nearest_transit_m: int | None = None
    pricing: Literal["estimated", "live"] = "estimated"
    data_basis: str = "osm_entity_modelled_price"


class Activity(BaseModel):
    name: str
    kind: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    start_time: str | None = None
    duration_minutes: int | None = None
    notes: str | None = None
    opening_hours: str | None = None
    website: str | None = None
    wheelchair: str | None = None
    walk_from_previous_km: float | None = None
    osm_id: str | None = None


class DayPlan(BaseModel):
    day_number: int
    day_date: date
    title: str = ""
    activities: list[Activity] = Field(default_factory=list)
    total_walking_km: float = 0.0
    notes: list[str] = Field(default_factory=list)


class CostBreakdown(BaseModel):
    """Where the money goes. Every figure is EUR and every figure is modelled."""

    flights_eur: float = 0.0
    lodging_eur: float = 0.0
    living_eur: float = 0.0
    activities_eur: float = 0.0
    total_eur: float = 0.0
    per_person_eur: float = 0.0
    budget_eur: float | None = None
    over_under_eur: float | None = None
    currency_note: str | None = None
    local_currency: str | None = None
    total_local: float | None = None


class CountryBriefing(BaseModel):
    """Practical facts shown as "know before you go"."""

    country: str | None = None
    currency_code: str | None = None
    currency_name: str | None = None
    languages: list[str] = Field(default_factory=list)
    timezone: str | None = None
    drives_on: str | None = None
    calling_code: str | None = None
    visa_note: str = (
        "Visa and entry rules are not covered by any keyless data source. "
        "Check your destination's official embassy site before booking."
    )


class TripPlan(BaseModel):
    """The complete, presentable travel plan."""

    model_config = ConfigDict(use_enum_values=True)

    destination: str
    country: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    start_date: date | None = None
    end_date: date | None = None
    nights: int = 0
    travellers: int = 1
    revision: int = 0

    outbound_flight: FlightOption | None = None
    inbound_flight: FlightOption | None = None
    flight_alternatives: list[FlightOption] = Field(default_factory=list)

    hotel: HotelOption | None = None
    hotel_alternatives: list[HotelOption] = Field(default_factory=list)

    days: list[DayPlan] = Field(default_factory=list)
    costs: CostBreakdown = Field(default_factory=CostBreakdown)
    weather: dict[str, Any] = Field(default_factory=dict)
    briefing: CountryBriefing = Field(default_factory=CountryBriefing)

    highlights: list[str] = Field(default_factory=list)
    packing_notes: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    validation: ValidationReport | None = None
    narrative: str = ""

    disclaimer: str = (
        "Flight and lodging prices are modelled estimates built from open data, "
        "not live bookable fares. Properties and airports are real; confirm "
        "prices and availability with the provider before booking."
    )
