"""Itinerary Planner agent -- assembles the concrete, bookable-shaped plan.

Responsibilities, in order:

1. Find flights for the real origin/destination pair and date window.
2. Find lodging: real OSM properties, filtered by the traveller's constraints.
3. Pull the day-by-day forecast and the points of interest that match the
   stated interests.
4. Lay activities out across days -- geographically clustered so each day is
   walkable, and weather-aware so the rainy day gets the museums.
5. Price the whole thing and produce the country briefing.

THE REVISION LOOP
-----------------
This node also runs on every replan. The validator does not merely say "too
expensive": it emits machine-readable ``fix_actions`` (``cheaper_hotel``,
``fewer_activities``, ``longer_layover``, ...). :func:`_apply_fixes` turns each
into a concrete change to the search parameters. That is what makes the critic
loop converge instead of producing the same rejected plan three times.
"""

from __future__ import annotations

import logging
import math
from datetime import date, datetime, timedelta
from typing import Any

from ...mcp import tools as mcp_tools
from ...observability import STAGE_ENTERED, langfuse_hook, span, time_agent
from ...schemas.plan import (
    Activity,
    CostBreakdown,
    CountryBriefing,
    DayPlan,
    FlightLeg,
    FlightOption,
    HotelOption,
    Layover,
    TripPlan,
)
from ...schemas.travel import DestinationCandidate, TravelBrief
from ..state import AgentState

logger = logging.getLogger(__name__)

# Activities per day by pace. Arrival and departure days get fewer regardless.
_PACE_ACTIVITIES = {"relaxed": 2, "balanced": 3, "packed": 5}

# Interests that make sense to schedule as a daytime stop, mapped to the POI
# categories the geo server understands.
_INTEREST_TO_POI = {
    "museums": "museums", "art": "art", "history": "history",
    "architecture": "architecture", "culture": "culture", "food": "food",
    "nightlife": "nightlife", "nature": "nature", "beach": "beach",
    "hiking": "hiking", "mountains": "mountains", "shopping": "shopping",
    "family": "family", "wellness": "wellness", "wine": "wine",
    "diving": "diving", "skiing": "skiing", "photography": "viewpoints",
}

# Categories that work in the rain. Used to schedule around bad days.
_INDOOR_CATEGORIES = {"museums", "art", "shopping", "wellness", "food", "culture"}

_STYLE_TO_LODGING = {
    "budget": (["hostel", "guest_house", "apartment"], None),
    "balanced": (["hotel", "guest_house", "apartment"], 3),
    "comfort": (["hotel", "apartment"], 4),
    "luxury": (["hotel", "resort"], 5),
}


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    d_phi = p2 - p1
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(d_lambda / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(min(1.0, a)))


def _to_flight_option(raw: dict[str, Any], direction: str) -> FlightOption:
    """Map the flights MCP response onto the typed model."""
    legs = [
        FlightLeg(
            from_iata=(leg.get("from") or {}).get("iata", "???"),
            from_name=(leg.get("from") or {}).get("name"),
            from_city=(leg.get("from") or {}).get("city"),
            to_iata=(leg.get("to") or {}).get("iata", "???"),
            to_name=(leg.get("to") or {}).get("name"),
            to_city=(leg.get("to") or {}).get("city"),
            carrier_code=leg.get("carrier_code"),
            carrier_name=leg.get("carrier_name"),
            flight_number=leg.get("flight_number"),
            depart_local=leg.get("depart_local"),
            arrive_local=leg.get("arrive_local"),
            duration_minutes=leg.get("duration_minutes"),
            distance_km=leg.get("distance_km"),
        )
        for leg in raw.get("legs") or []
    ]
    return FlightOption(
        id=raw.get("id", f"{direction}-unknown"),
        direction=direction,
        stops=int(raw.get("stops") or 0),
        legs=legs,
        layovers=[
            Layover(
                airport=lay.get("airport", "???"),
                city=lay.get("city"),
                minutes=int(lay.get("minutes") or 0),
            )
            for lay in raw.get("layovers") or []
        ],
        total_duration_minutes=raw.get("total_duration_minutes"),
        price_per_person_eur=raw.get("price_per_person_eur"),
        price_total_eur=raw.get("price_total_eur"),
        cabin=raw.get("cabin", "economy"),
        pricing=raw.get("pricing", "estimated"),
        data_basis=raw.get("data_basis", "modelled"),
    )


def _to_hotel_option(raw: dict[str, Any]) -> HotelOption:
    return HotelOption(
        id=raw.get("id", "unknown"),
        name=raw.get("name", "Unnamed property"),
        lodging_type=raw.get("lodging_type", "hotel"),
        stars=raw.get("stars"),
        stars_source=raw.get("stars_source", "inferred"),
        latitude=raw.get("latitude"),
        longitude=raw.get("longitude"),
        distance_from_centre_km=raw.get("distance_from_centre_km"),
        address=raw.get("address"),
        website=raw.get("website"),
        phone=raw.get("phone"),
        amenities=list(raw.get("amenities") or []),
        wheelchair_accessible=bool(raw.get("wheelchair_accessible")),
        estimated_nightly_eur=raw.get("estimated_nightly_eur"),
        estimated_total_eur=raw.get("estimated_total_eur"),
        rooms=int(raw.get("rooms") or 1),
        pricing=raw.get("pricing", "estimated"),
        data_basis=raw.get("data_basis", "osm_entity_modelled_price"),
    )


class PlanParameters:
    """Mutable search parameters, adjusted between revisions by the validator."""

    def __init__(self, brief: TravelBrief) -> None:
        style = brief.style if isinstance(brief.style, str) else brief.style.value
        pace = brief.pace if isinstance(brief.pace, str) else brief.pace.value

        lodging_types, min_stars = _STYLE_TO_LODGING.get(
            style, (["hotel", "guest_house", "apartment"], 3)
        )
        self.lodging_types = list(lodging_types)
        self.min_stars = min_stars
        self.max_nightly_eur: float | None = None
        self.hotel_radius_km = 4.0
        self.hotel_rank = 0            # Which ranked property to take.
        self.style = style
        self.flight_rank = 0           # Which ranked itinerary to take.
        self.prefer_nonstop = brief.constraints.max_stops == 0
        self.min_layover_minutes = 60
        self.activities_per_day = _PACE_ACTIVITIES.get(pace, 3)
        self.cabin = "business" if style == "luxury" else "economy"
        # When true, lodging is ranked on price alone. Set only by the
        # validator's `cheaper_hotel` fix -- the default is value-ranked.
        self.cost_first = style == "budget"
        self.notes: list[str] = []

    def apply_fixes(self, fixes: list[str], revision: int) -> None:
        """Translate the validator's fix hints into concrete parameter changes."""
        for fix in fixes:
            if fix == "cheaper_hotel":
                self.hotel_rank = 0
                self.cost_first = True   # Rank on price alone this round.
                self.min_stars = max(1, (self.min_stars or 3) - 1)
                if "hostel" not in self.lodging_types:
                    self.lodging_types.append("hostel")
                self.notes.append("Searched cheaper accommodation.")
            elif fix == "closer_hotel":
                self.hotel_radius_km = max(1.2, self.hotel_radius_km - 1.5)
                self.notes.append("Restricted the hotel search closer to the centre.")
            elif fix == "accessible_hotel":
                self.notes.append("Filtered for step-free accommodation.")
            elif fix == "cheaper_flight":
                # The flights server returns options cheapest-first.
                self.flight_rank = 0
                self.cabin = "economy"
                self.prefer_nonstop = False
                self.notes.append("Widened the flight search to cheaper options.")
            elif fix == "fewer_stops":
                self.prefer_nonstop = True
                self.notes.append("Restricted to non-stop flights.")
            elif fix == "longer_layover":
                self.min_layover_minutes = 90
                self.notes.append("Required a longer connection window.")
            elif fix == "fewer_activities":
                self.activities_per_day = max(1, self.activities_per_day - 1)
                self.notes.append("Reduced the daily activity load.")
        # Each revision widens the hotel search a little, so a repeatedly
        # rejected plan does not keep proposing the same property.
        self.hotel_rank = min(6, self.hotel_rank + revision)


async def _plan_flights(
    brief: TravelBrief,
    destination: DestinationCandidate,
    params: PlanParameters,
    tool_trace: list,
) -> tuple[FlightOption | None, FlightOption | None, list[FlightOption]]:
    """Search flights and pick the option matching the current parameters."""
    if not brief.origin or not brief.dates.start or not brief.dates.end:
        return None, None, []

    raw = await mcp_tools.search_flights(
        origin=brief.origin,
        destination=f"{destination.city}, {destination.country or ''}".strip(", "),
        depart_date=brief.dates.start,
        return_date=brief.dates.end,
        passengers=max(1, brief.party.adults + brief.party.children),
        cabin=params.cabin,
        trace=tool_trace,
    )
    if not raw:
        return None, None, []

    def choose(options: list[dict[str, Any]], direction: str) -> FlightOption | None:
        if not options:
            return None
        mapped = [_to_flight_option(o, direction) for o in options]

        pool = mapped
        if params.prefer_nonstop:
            nonstop = [f for f in mapped if f.stops == 0]
            if nonstop:
                pool = nonstop
        if params.min_layover_minutes > 60:
            comfortable = [
                f for f in pool
                if all(lay.minutes >= params.min_layover_minutes for lay in f.layovers)
            ]
            if comfortable:
                pool = comfortable
        if brief.constraints.max_flight_hours:
            limit = brief.constraints.max_flight_hours * 60
            within = [
                f for f in pool
                if (f.total_duration_minutes or 0) <= limit
            ]
            if within:
                pool = within

        index = min(params.flight_rank, len(pool) - 1)
        return pool[index]

    outbound = choose(raw.get("outbound") or [], "outbound")
    inbound = choose(raw.get("inbound") or [], "inbound")

    alternatives = [
        _to_flight_option(o, "outbound") for o in (raw.get("outbound") or [])[:4]
    ]
    return outbound, inbound, alternatives


def _rank_properties(
    properties: list[HotelOption],
    params: PlanParameters,
    nights: int,
    lodging_budget_eur: float | None,
) -> list[HotelOption]:
    """Order candidate properties by value, not by raw price.

    The hotels server already returns properties cheapest-first. Taking its
    first entry unconditionally was a real bug: a traveller with an 1800 EUR
    budget for six nights was booked into a 15 EUR/night 2-star, because
    "cheapest" and "best" are only the same thing for a budget traveller.

    So unless the traveller asked for budget travel (or the validator has just
    told us to cut cost), properties are scored on value:

    * **Quality** -- stars, mapped amenities, and how walkable the surroundings
      are, since a well-placed 3-star beats an isolated 4-star.
    * **Price fit** -- distance from the nightly rate the budget actually
      supports. Both far above (unaffordable) and far below (needlessly grim)
      are penalised, which is what "value" means in practice.
    * **Location** -- a mild penalty per km from the centre.
    """
    if params.cost_first or lodging_budget_eur is None:
        return sorted(
            properties,
            key=lambda p: (p.estimated_nightly_eur or 1e9)
            * (1 + 0.10 * (p.distance_from_centre_km or 0)),
        )

    target = max(25.0, lodging_budget_eur / max(1, nights))

    def score(hotel: HotelOption) -> float:
        nightly = hotel.estimated_nightly_eur or target
        quality = (
            (hotel.stars or 3) * 12.0
            + min(len(hotel.amenities), 6) * 3.0
            + (hotel.walkability_score or 50) * 0.12
        )
        ratio = nightly / target
        if ratio > 1.0:
            # Over the affordable rate: penalise hard and without limit.
            fit = -55.0 * (ratio - 1.0)
        else:
            # Under it: mild penalty, so a bargain still wins on quality but a
            # suspiciously cheap room does not automatically beat a decent one.
            fit = -14.0 * (1.0 - ratio)
        location = -3.0 * (hotel.distance_from_centre_km or 0.0)
        return quality + fit + location

    return sorted(properties, key=score, reverse=True)


async def _plan_hotel(
    brief: TravelBrief,
    destination: DestinationCandidate,
    params: PlanParameters,
    tool_trace: list,
    lodging_budget_eur: float | None = None,
) -> tuple[HotelOption | None, list[HotelOption]]:
    """Search lodging and pick the property matching the current parameters."""
    if not brief.dates.start or not brief.dates.end:
        return None, []

    guests = max(1, brief.party.adults + brief.party.children)
    # Two guests per room is the standard assumption; children share.
    rooms = max(1, math.ceil(brief.party.adults / 2))

    raw = await mcp_tools.search_accommodation(
        latitude=destination.latitude,
        longitude=destination.longitude,
        check_in=brief.dates.start,
        check_out=brief.dates.end,
        guests=guests,
        rooms=rooms,
        radius_km=params.hotel_radius_km,
        lodging_types=params.lodging_types,
        max_nightly_eur=params.max_nightly_eur,
        min_stars=params.min_stars,
        city=destination.city,
        region=destination.region,
        limit=14,
        trace=tool_trace,
    )
    if not raw or not raw.get("properties"):
        # Retry once with every filter relaxed. A plan with a hotel the
        # traveller can reject beats a plan with no hotel at all.
        raw = await mcp_tools.search_accommodation(
            latitude=destination.latitude,
            longitude=destination.longitude,
            check_in=brief.dates.start,
            check_out=brief.dates.end,
            guests=guests,
            rooms=rooms,
            radius_km=8.0,
            lodging_types=["hotel", "guest_house", "apartment", "hostel"],
            city=destination.city,
            region=destination.region,
            limit=14,
            trace=tool_trace,
        )
    if not raw or not raw.get("properties"):
        return None, []

    properties = [_to_hotel_option(p) for p in raw["properties"]]

    if brief.constraints.accessibility_required:
        accessible = [p for p in properties if p.wheelchair_accessible]
        if accessible:
            properties = accessible
        else:
            params.notes.append(
                "No property in OpenStreetMap is tagged step-free here; "
                "confirm accessibility directly with the hotel."
            )

    nights = (brief.dates.end - brief.dates.start).days or 1
    properties = _rank_properties(properties, params, nights, lodging_budget_eur)

    index = min(params.hotel_rank, len(properties) - 1)
    chosen = properties[index]

    # Walkability is what the validator checks to catch a stranded hotel.
    if chosen.latitude is not None and chosen.longitude is not None:
        context = await mcp_tools.neighbourhood_context(
            chosen.latitude, chosen.longitude, 700, tool_trace
        )
        if context:
            chosen.walkability_score = context.get("walkability_score")
            nearest = context.get("nearest_transit") or {}
            chosen.nearest_transit_m = nearest.get("distance_m")

    alternatives = [p for i, p in enumerate(properties[:5]) if i != index]
    return chosen, alternatives


def _daily_weather_index(forecast: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Index the forecast by ISO date for fast per-day lookup."""
    if not forecast:
        return {}
    return {day["date"]: day for day in forecast.get("days") or [] if day.get("date")}


def _is_wet(day: dict[str, Any] | None) -> bool:
    """Whether a day is wet enough to prefer indoor activities."""
    if not day:
        return False
    precipitation = day.get("precipitation_mm") or 0
    probability = day.get("precipitation_probability_pct") or 0
    severity = (day.get("condition") or {}).get("severity", 0)
    return precipitation >= 3.0 or probability >= 60 or severity >= 2


def _build_days(
    brief: TravelBrief,
    destination: DestinationCandidate,
    hotel: HotelOption | None,
    pois_by_category: dict[str, list[dict[str, Any]]],
    forecast: dict[str, Any] | None,
    params: PlanParameters,
    outbound: FlightOption | None,
    inbound: FlightOption | None,
) -> list[DayPlan]:
    """Lay activities out across the trip.

    Three constraints shape the layout:

    * **Geography.** Each day is built greedily nearest-neighbour from the
      previous stop, so a day's activities cluster instead of criss-crossing
      the city.
    * **Weather.** Wet days draw from indoor categories first. This is the
      single highest-value scheduling decision the planner makes.
    * **Arrival and departure reality.** A flight landing at 22:15 does not
      leave room for a museum; the first and last days are trimmed based on
      the actual flight times.
    """
    if not brief.dates.start or not brief.dates.end:
        return []

    weather_index = _daily_weather_index(forecast)
    nights = (brief.dates.end - brief.dates.start).days or 1

    # Flatten POIs, tracking their category so indoor/outdoor is known.
    pool: list[tuple[str, dict[str, Any]]] = []
    for category, places in pois_by_category.items():
        for place in places:
            pool.append((category, place))

    used: set[str] = set()

    # How many stops each category has already contributed. Distance alone is
    # not enough to build a good day: a purely nearest-first walk through a
    # medina produced six consecutive hammams in testing, because spas cluster.
    # Repeating a category is therefore penalised as if it were extra walking,
    # which keeps days geographically tight but thematically varied.
    category_used: dict[str, int] = {}
    REPEAT_PENALTY_KM = 1.6

    def take_nearest(
        origin_lat: float,
        origin_lon: float,
        indoor_only: bool,
        limit_km: float = 12.0,
        exclude_categories: set[str] | None = None,
    ) -> tuple[str, dict[str, Any]] | None:
        """Pop the best next POI: near, unvisited, and preferably a new theme."""
        best: tuple[float, float, str, dict[str, Any]] | None = None
        for category, place in pool:
            key = place.get("osm_id") or place.get("name", "")
            if key in used:
                continue
            if indoor_only and category not in _INDOOR_CATEGORIES:
                continue
            if exclude_categories and category in exclude_categories:
                continue
            lat, lon = place.get("latitude"), place.get("longitude")
            if lat is None or lon is None:
                continue
            distance = _haversine_km(origin_lat, origin_lon, lat, lon)
            if distance > limit_km:
                continue
            cost = distance + REPEAT_PENALTY_KM * category_used.get(category, 0)
            if best is None or cost < best[0]:
                best = (cost, distance, category, place)
        if best is None:
            return None
        _, _, category, place = best
        used.add(place.get("osm_id") or place.get("name", ""))
        category_used[category] = category_used.get(category, 0) + 1
        return category, place

    base_lat = hotel.latitude if hotel and hotel.latitude else destination.latitude
    base_lon = hotel.longitude if hotel and hotel.longitude else destination.longitude

    # Arrival and departure hours from the actual flights, when available.
    arrival_hour = 12
    if outbound and outbound.legs and outbound.legs[-1].arrive_local:
        try:
            arrival_hour = datetime.strptime(
                outbound.legs[-1].arrive_local, "%Y-%m-%dT%H:%M"
            ).hour
        except ValueError:
            pass
    departure_hour = 18
    if inbound and inbound.legs and inbound.legs[0].depart_local:
        try:
            departure_hour = datetime.strptime(
                inbound.legs[0].depart_local, "%Y-%m-%dT%H:%M"
            ).hour
        except ValueError:
            pass

    days: list[DayPlan] = []
    for offset in range(nights + 1):
        day_date = brief.dates.start + timedelta(days=offset)
        is_first, is_last = offset == 0, offset == nights
        weather = weather_index.get(day_date.isoformat())
        wet = _is_wet(weather)

        # How many activities fit today.
        slots = params.activities_per_day
        if is_first:
            # Landing at 21:00 leaves room for dinner, not a full day.
            slots = 1 if arrival_hour >= 17 else max(1, slots - 1)
        if is_last:
            slots = 0 if departure_hour <= 10 else 1

        notes: list[str] = []
        if is_first:
            notes.append(
                f"Arrival day -- landing around {arrival_hour:02d}:00."
                if outbound else "Arrival day."
            )
        if is_last:
            notes.append(
                f"Departure day -- flight out around {departure_hour:02d}:00."
                if inbound else "Departure day."
            )
        if wet:
            notes.append("Rain likely, so indoor stops are scheduled first.")
        if weather:
            high, low = weather.get("temp_max_c"), weather.get("temp_min_c")
            if high is not None:
                notes.append(
                    f"{weather.get('condition', {}).get('label', 'Mixed')}, "
                    f"{high:.0f}C / {low:.0f}C."
                    if low is not None
                    else f"{weather.get('condition', {}).get('label', 'Mixed')}, {high:.0f}C."
                )

        activities: list[Activity] = []
        cursor_lat, cursor_lon = base_lat, base_lon
        walking = 0.0
        start_hour = max(arrival_hour + 2, 10) if is_first else 9

        day_categories: set[str] = set()
        for slot in range(slots):
            # The first stop of a day may be anywhere in the city (you travel
            # to it deliberately). Every stop after it must be genuinely
            # walkable from the last one, or the "walking day" is a fiction --
            # an early version happily scheduled 6 km between two stops.
            reach_km = 12.0 if slot == 0 else 2.5

            # First try to add a theme this day does not already have.
            picked = take_nearest(
                cursor_lat, cursor_lon, indoor_only=wet,
                limit_km=reach_km, exclude_categories=day_categories,
            )
            if picked is None and wet:
                picked = take_nearest(
                    cursor_lat, cursor_lon, indoor_only=False,
                    limit_km=reach_km, exclude_categories=day_categories,
                )
            if picked is None:
                # Nothing new within walking range -- allow a repeated theme
                # rather than leaving the slot empty.
                picked = take_nearest(
                    cursor_lat, cursor_lon, indoor_only=False, limit_km=reach_km
                )
            if picked is None:
                break
            category, place = picked
            day_categories.add(category)

            distance = _haversine_km(
                cursor_lat, cursor_lon, place["latitude"], place["longitude"]
            )
            walking += distance
            hour = min(20, start_hour + slot * 3)

            activities.append(
                Activity(
                    name=place.get("name", "Unnamed"),
                    kind=place.get("kind") or category,
                    latitude=place.get("latitude"),
                    longitude=place.get("longitude"),
                    start_time=f"{hour:02d}:00",
                    duration_minutes=120 if category in _INDOOR_CATEGORIES else 90,
                    opening_hours=place.get("opening_hours"),
                    website=place.get("website"),
                    wheelchair=place.get("wheelchair"),
                    walk_from_previous_km=round(distance, 2),
                    osm_id=place.get("osm_id"),
                    notes=(
                        "Indoor option chosen for the forecast rain."
                        if wet and category in _INDOOR_CATEGORIES else None
                    ),
                )
            )
            cursor_lat, cursor_lon = place["latitude"], place["longitude"]

        if activities:
            title = activities[0]["name"] if isinstance(activities[0], dict) else activities[0].name
        elif is_first:
            title = "Arrival and settling in"
        elif is_last:
            title = "Departure"
        else:
            title = "Free day"

        days.append(
            DayPlan(
                day_number=offset + 1,
                day_date=day_date,
                title=title,
                activities=activities,
                total_walking_km=round(walking, 2),
                notes=notes,
            )
        )

    return days


def _compute_costs(
    brief: TravelBrief,
    outbound: FlightOption | None,
    inbound: FlightOption | None,
    hotel: HotelOption | None,
    stay: dict[str, Any] | None,
    budget_eur: float | None,
) -> CostBreakdown:
    """Total the plan. Every figure is EUR and every figure is modelled."""
    people = max(1, brief.party.total)

    flights = 0.0
    for option in (outbound, inbound):
        if option and option.price_total_eur:
            flights += option.price_total_eur

    lodging = (hotel.estimated_total_eur if hotel and hotel.estimated_total_eur else 0.0)
    living = (stay or {}).get("living_total_eur", 0.0) or 0.0

    # Activities: a flat allowance per scheduled stop rather than real ticket
    # prices, which OSM does not carry. Deliberately conservative.
    nights = brief.dates.duration_nights or 6
    activities = round(14.0 * people * nights * 0.6, 2)

    total = round(flights + lodging + living + activities, 2)

    return CostBreakdown(
        flights_eur=round(flights, 2),
        lodging_eur=round(lodging, 2),
        living_eur=round(living, 2),
        activities_eur=activities,
        total_eur=total,
        per_person_eur=round(total / people, 2),
        budget_eur=budget_eur,
        over_under_eur=round(total - budget_eur, 2) if budget_eur else None,
    )


async def itinerary_planner_node(state: AgentState) -> AgentState:
    """Build (or rebuild) the full trip plan."""
    with time_agent("itinerary_planner"):
        revision = state.get("revision", 0)
        STAGE_ENTERED.labels(
            stage="revising" if revision else "planning"
        ).inc()

        brief = state.get("brief")
        destination = state.get("selected_destination")
        tool_trace: list = []

        if brief is None or destination is None:
            return AgentState(stage="error", errors=["Nothing selected to plan."])

        params = PlanParameters(brief)
        fixes = list(state.get("fix_actions") or [])
        if fixes:
            params.apply_fixes(fixes, revision)
            logger.info("Replanning revision %d with fixes: %s", revision, fixes)

        with span(
            "agent.itinerary_planner",
            **{
                "plan.destination": destination.city,
                "plan.revision": revision,
                "plan.fixes": ",".join(fixes),
            },
        ) as current:
            # ---- Flights and lodging ----
            outbound, inbound, flight_alternatives = await _plan_flights(
                brief, destination, params, tool_trace
            )

            # How much of the budget is actually left for rooms, once the
            # flights just priced and a living-cost allowance are taken out.
            # This is what lets the planner buy a sensible room rather than
            # the cheapest one on the map.
            budget_eur = (state.get("scratch") or {}).get("budget_eur_total")
            lodging_budget = None
            if budget_eur:
                flight_cost = sum(
                    f.price_total_eur or 0.0 for f in (outbound, inbound) if f
                )
                nights = brief.dates.duration_nights or 6
                living_allowance = 55.0 * max(1, brief.party.total) * (nights + 1)
                lodging_budget = max(0.0, budget_eur - flight_cost - living_allowance)

            hotel, hotel_alternatives = await _plan_hotel(
                brief, destination, params, tool_trace, lodging_budget
            )

            # ---- Weather for the trip ----
            forecast = None
            if brief.dates.start and brief.dates.end:
                forecast = await mcp_tools.get_forecast(
                    destination.latitude, destination.longitude,
                    brief.dates.start, brief.dates.end, tool_trace,
                )

            # ---- Points of interest matched to interests ----
            categories = [
                _INTEREST_TO_POI[i] for i in brief.interests if i in _INTEREST_TO_POI
            ]
            if not categories:
                categories = ["attractions", "culture", "food"]
            # Always include somewhere to eat and a wet-weather option.
            for essential in ("food", "museums"):
                if essential not in categories:
                    categories.append(essential)

            pois = await mcp_tools.find_pois_multi(
                destination.latitude, destination.longitude,
                categories[:5], radius_km=6.0, per_category=10, trace=tool_trace,
            )

            days = _build_days(
                brief, destination, hotel, pois, forecast, params, outbound, inbound
            )

            # ---- Costs ----
            stay = None
            if hotel and hotel.estimated_nightly_eur and brief.dates.start and brief.dates.end:
                style = brief.style if isinstance(brief.style, str) else brief.style.value
                stay = await mcp_tools.estimate_stay_cost(
                    nightly_eur=hotel.estimated_nightly_eur,
                    check_in=brief.dates.start,
                    check_out=brief.dates.end,
                    rooms=hotel.rooms,
                    city=destination.city,
                    region=destination.region,
                    travellers=max(1, brief.party.total),
                    style=style,
                    trace=tool_trace,
                )

            costs = _compute_costs(brief, outbound, inbound, hotel, stay, budget_eur)

            # ---- Country briefing ----
            briefing = CountryBriefing(country=destination.country)
            if destination.country:
                info = await mcp_tools.get_country_info(destination.country, tool_trace)
                if info:
                    briefing = CountryBriefing(
                        country=info.get("name") or destination.country,
                        currency_code=info.get("currency_code"),
                        currency_name=info.get("currency_name"),
                        languages=list(info.get("languages") or []),
                        timezone=(info.get("timezones") or [None])[0],
                        drives_on=info.get("drives_on"),
                        calling_code=info.get("calling_code"),
                    )

            # Show the total in local currency too, using real ECB rates.
            if briefing.currency_code and briefing.currency_code != "EUR":
                converted = await mcp_tools.convert_currency(
                    costs.total_eur, "EUR", briefing.currency_code, tool_trace
                )
                if converted and converted.get("converted"):
                    costs.local_currency = briefing.currency_code
                    costs.total_local = converted["converted"]
                    costs.currency_note = (
                        f"About {converted['converted']:,.0f} "
                        f"{briefing.currency_code} at today's ECB rate."
                    )

            plan = TripPlan(
                destination=destination.city,
                country=destination.country,
                latitude=destination.latitude,
                longitude=destination.longitude,
                start_date=brief.dates.start,
                end_date=brief.dates.end,
                nights=brief.dates.duration_nights or 0,
                travellers=max(1, brief.party.total),
                revision=revision,
                outbound_flight=outbound,
                inbound_flight=inbound,
                flight_alternatives=flight_alternatives,
                hotel=hotel,
                hotel_alternatives=hotel_alternatives,
                days=days,
                costs=costs,
                weather=forecast or {},
                briefing=briefing,
                warnings=list(params.notes),
            )

            current.set_attribute("plan.total_eur", costs.total_eur)
            current.set_attribute("plan.days", len(days))
            current.set_attribute("plan.has_flights", outbound is not None)
            current.set_attribute("plan.has_hotel", hotel is not None)

        langfuse_hook.log_span(
            state.get("langfuse_trace"),
            name="itinerary_planner",
            input_data={"destination": destination.city, "revision": revision,
                        "fixes": fixes},
            output_data={"total_eur": costs.total_eur, "days": len(days)},
        )

        logger.info(
            "Plan built for %s: %d days, EUR %.0f (revision %d)",
            destination.city, len(days), costs.total_eur, revision,
        )

        return AgentState(
            plan=plan,
            stage="validating",
            tool_trace=tool_trace,
            plan_history=[
                {
                    "revision": revision,
                    "destination": destination.city,
                    "total_eur": costs.total_eur,
                    "fixes_applied": fixes,
                }
            ],
        )


def trip_month(brief: TravelBrief) -> int | None:
    return brief.dates.start.month if brief.dates.start else None


def days_until(target: date | None) -> int | None:
    return (target - date.today()).days if target else None
