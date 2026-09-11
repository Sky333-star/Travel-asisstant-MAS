"""Domain models for the analysis and recommendation half of the graph.

These models are the contract between agents. The Query Analyst produces a
:class:`TravelBrief`; the Destination Scout and Weather Analyst produce
:class:`DestinationCandidate` objects; the Recommender ranks them. Because
every hand-off is a validated Pydantic model rather than a free-form dict, a
malformed LLM response fails loudly at the boundary instead of corrupting
state three nodes later.
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class TripPace(str, Enum):
    """How much a traveller wants packed into each day."""

    RELAXED = "relaxed"
    BALANCED = "balanced"
    PACKED = "packed"


class TravelStyle(str, Enum):
    BUDGET = "budget"
    BALANCED = "balanced"
    COMFORT = "comfort"
    LUXURY = "luxury"


class ClimatePreference(str, Enum):
    WARM = "warm"
    MILD = "mild"
    COOL = "cool"
    SNOW = "snow"
    DRY = "dry"
    ANY = "any"


class DateWindow(BaseModel):
    """A trip's date range, with an explicit flexibility flag.

    ``flexible`` matters: if the user said "sometime in May" we may shift dates
    to dodge bad weather; if they said "the 3rd to the 10th" we must not.
    """

    start: date | None = None
    end: date | None = None
    flexible: bool = False
    duration_nights: int | None = Field(default=None, ge=1, le=90)

    @model_validator(mode="after")
    def _derive(self) -> DateWindow:
        if self.start and self.end:
            if self.end < self.start:
                self.start, self.end = self.end, self.start
            self.duration_nights = (self.end - self.start).days or 1
        return self

    @property
    def is_complete(self) -> bool:
        return self.start is not None and self.end is not None


class Budget(BaseModel):
    """Total trip budget, normalised to EUR for internal arithmetic."""

    amount: float | None = Field(default=None, ge=0)
    currency: str = "EUR"
    amount_eur: float | None = Field(default=None, ge=0)
    per_person: bool = True
    is_hard_limit: bool = True

    @field_validator("currency")
    @classmethod
    def _upper(cls, value: str) -> str:
        return (value or "EUR").strip().upper()[:3] or "EUR"


class PartyComposition(BaseModel):
    adults: int = Field(default=1, ge=1, le=12)
    children: int = Field(default=0, ge=0, le=10)
    infants: int = Field(default=0, ge=0, le=6)

    @property
    def total(self) -> int:
        return self.adults + self.children + self.infants

    @property
    def has_minors(self) -> bool:
        return self.children > 0 or self.infants > 0


class Constraints(BaseModel):
    """Hard and soft requirements that the validator checks the plan against."""

    accessibility_required: bool = False
    dietary: list[str] = Field(default_factory=list)
    avoid_countries: list[str] = Field(default_factory=list)
    avoid_cities: list[str] = Field(default_factory=list)
    max_flight_hours: float | None = Field(default=None, gt=0, le=48)
    max_stops: int | None = Field(default=None, ge=0, le=3)
    must_include: list[str] = Field(default_factory=list)
    health_notes: list[str] = Field(default_factory=list)
    visa_free_only: bool = False


class TravelBrief(BaseModel):
    """Structured understanding of what the traveller actually asked for.

    Produced by the Query Analyst agent from free text (typed or transcribed).
    ``missing_critical`` drives the clarification loop: the graph refuses to
    plan a trip whose origin or dates it had to guess.
    """

    model_config = ConfigDict(use_enum_values=True)

    raw_query: str = ""
    origin: str | None = None
    origin_lat: float | None = None
    origin_lon: float | None = None
    named_destinations: list[str] = Field(default_factory=list)
    regions: list[str] = Field(default_factory=list)
    dates: DateWindow = Field(default_factory=DateWindow)
    party: PartyComposition = Field(default_factory=PartyComposition)
    budget: Budget = Field(default_factory=Budget)
    interests: list[str] = Field(default_factory=list)
    climate_preference: ClimatePreference = ClimatePreference.ANY
    pace: TripPace = TripPace.BALANCED
    style: TravelStyle = TravelStyle.BALANCED
    constraints: Constraints = Field(default_factory=Constraints)
    trip_purpose: str | None = None
    missing_critical: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    extraction_method: Literal["llm", "rules", "merged"] = "rules"
    notes: list[str] = Field(default_factory=list)

    @property
    def is_plannable(self) -> bool:
        """True when enough is known to search flights and score weather."""
        return bool(self.origin) and self.dates.is_complete


class WeatherAssessment(BaseModel):
    """Weather verdict for one candidate over the trip window."""

    score: float | None = Field(default=None, ge=0, le=100)
    basis: Literal["forecast", "climate_normals", "unavailable"] = "unavailable"
    avg_high_c: float | None = None
    avg_low_c: float | None = None
    rainy_days: float | None = None
    precipitation_mm: float | None = None
    reasons: list[str] = Field(default_factory=list)
    air_quality_band: str | None = None


class DestinationCandidate(BaseModel):
    """A place the system is considering, with everything known about it."""

    city: str
    country: str | None = None
    iso2: str | None = None
    region: str | None = None
    latitude: float
    longitude: float
    blurb: str | None = None
    tags: list[str] = Field(default_factory=list)
    budget_tier: int | None = Field(default=None, ge=1, le=4)
    best_months: list[int] = Field(default_factory=list)

    # Scores, all 0-100.
    interest_score: float | None = None
    season_score: float | None = None
    weather: WeatherAssessment = Field(default_factory=WeatherAssessment)
    affordability_score: float | None = None
    composite_score: float | None = None

    matched_interests: list[str] = Field(default_factory=list)
    estimated_flight_eur: float | None = None
    estimated_nightly_eur: float | None = None
    estimated_trip_total_eur: float | None = None
    why: list[str] = Field(default_factory=list)
    source: str = "catalogue"

    @property
    def label(self) -> str:
        return f"{self.city}, {self.country}" if self.country else self.city


class Recommendation(BaseModel):
    """The shortlist presented to the user, plus the reasoning behind it."""

    candidates: list[DestinationCandidate] = Field(default_factory=list)
    headline: str = ""
    rationale: str = ""
    follow_up_question: str = "Which of these would you like me to plan in detail?"
    generated_by: Literal["llm", "rules"] = "rules"

    def top(self) -> DestinationCandidate | None:
        return self.candidates[0] if self.candidates else None


class ClarificationRequest(BaseModel):
    """A question the graph must ask before it can responsibly continue."""

    fields: list[str] = Field(default_factory=list)
    question: str
    examples: list[str] = Field(default_factory=list)
    blocking: bool = True


class ToolInvocation(BaseModel):
    """One MCP tool call, recorded for the trace panel and for debugging."""

    server: str
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    duration_ms: float | None = None
    ok: bool = True
    error: str | None = None
    result_summary: str | None = None
