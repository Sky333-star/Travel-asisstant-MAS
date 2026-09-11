"""Deterministic cost-estimation models for flights and lodging.

WHY THIS FILE EXISTS
--------------------
This project is constrained to data sources that need no API key. Real-time
airfare and hotel *rates* are not available under that constraint -- every
provider (Amadeus, Skyscanner, Booking, Kiwi) requires registered credentials.

Rather than silently invent numbers, Wayfarer splits the problem:

* **Entities are real.** Airports come from the OurAirports open dataset;
  hotels come from OpenStreetMap. Names, codes, coordinates and star ratings
  are genuine open data.
* **Prices are modelled.** The functions below produce *estimates* from
  published industry relationships (distance-based cost-per-km curves, the
  advance-purchase discount curve, seasonal multipliers, city cost indices).

Every price this module returns is tagged ``estimated`` in the MCP response so
that the agents, the API and the UI can all label it honestly. The models are
deterministic: the same query always yields the same number, which keeps the
plan stable across the validator's replan loop and makes tests reproducible.

Sources for the shape of the curves (not for live values):
* ICAO/IATA published average yield per revenue-passenger-kilometre.
* Widely reported advance-purchase pricing behaviour (cheapest 3-8 weeks out).
* Numbeo-style relative cost-of-living indices, bucketed coarsely by city.
"""

from __future__ import annotations

import hashlib
from datetime import date

# --------------------------------------------------------------------------
# Deterministic pseudo-randomness
# --------------------------------------------------------------------------


def stable_unit(*parts: object) -> float:
    """Return a deterministic float in [0, 1) derived from ``parts``.

    Used to give each synthesised flight or room a stable, plausible-looking
    variation without a random seed leaking between calls. Two identical
    queries always produce identical output, which the replan loop relies on.
    """
    raw = "|".join(str(p) for p in parts)
    digest = hashlib.sha256(raw.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / float(1 << 64)


def jitter(value: float, spread: float, *parts: object) -> float:
    """Scale ``value`` by +/- ``spread`` fraction, deterministically."""
    return value * (1.0 + spread * (2.0 * stable_unit(*parts) - 1.0))


# --------------------------------------------------------------------------
# Flight cost model
# --------------------------------------------------------------------------

# Cost per km falls sharply with distance: short hops are dominated by fixed
# costs (crew, landing fees, turnaround), long hauls amortise them.
_DISTANCE_BANDS: tuple[tuple[float, float, float], ...] = (
    # (upper_km, fixed_component_eur, per_km_eur)
    (400.0, 48.0, 0.150),
    (1_000.0, 42.0, 0.115),
    (2_500.0, 38.0, 0.082),
    (6_000.0, 55.0, 0.058),
    (10_000.0, 80.0, 0.045),
    (float("inf"), 120.0, 0.038),
)

# Northern-hemisphere demand seasonality by month index (1-12).
_MONTH_DEMAND: dict[int, float] = {
    1: 0.88, 2: 0.86, 3: 0.94, 4: 1.02, 5: 1.05, 6: 1.16,
    7: 1.28, 8: 1.26, 9: 1.04, 10: 0.97, 11: 0.90, 12: 1.15,
}

_CABIN_MULTIPLIER: dict[str, float] = {
    "economy": 1.0,
    "premium_economy": 1.65,
    "business": 3.1,
    "first": 5.2,
}


def advance_purchase_factor(days_ahead: int) -> float:
    """Fare multiplier as a function of booking lead time.

    Mirrors the well-documented U-shape: last-minute fares spike, the cheapest
    window sits roughly 3-8 weeks out, and very early bookings drift back up
    because carriers release only limited cheap inventory early.
    """
    if days_ahead <= 2:
        return 1.85
    if days_ahead <= 7:
        return 1.52
    if days_ahead <= 14:
        return 1.24
    if days_ahead <= 21:
        return 1.08
    if days_ahead <= 60:
        return 1.00
    if days_ahead <= 120:
        return 1.06
    return 1.14


def estimate_flight_price_eur(
    distance_km: float,
    depart_on: date,
    days_ahead: int,
    *,
    stops: int = 0,
    cabin: str = "economy",
    carrier_code: str = "XX",
    seed_parts: tuple[object, ...] = (),
) -> float:
    """Estimate a one-way fare in EUR.

    The result is a modelled estimate, never a bookable price. Connections are
    priced below non-stops because that is how carriers sell the inconvenience.
    """
    fixed, per_km = next(
        (f, k) for upper, f, k in _DISTANCE_BANDS if distance_km <= upper
    )
    base = fixed + per_km * distance_km

    base *= _MONTH_DEMAND.get(depart_on.month, 1.0)
    base *= advance_purchase_factor(days_ahead)
    base *= _CABIN_MULTIPLIER.get(cabin, 1.0)

    # Each connection trades ~11% off the fare for the added travel time.
    base *= (0.89 ** min(stops, 2))

    # Weekend departures carry a small premium on leisure routes.
    if depart_on.weekday() in (4, 5):  # Friday / Saturday
        base *= 1.06
    elif depart_on.weekday() in (1, 2):  # Tuesday / Wednesday
        base *= 0.95

    price = jitter(base, 0.16, carrier_code, depart_on.isoformat(), *seed_parts)
    return round(max(24.0, price), 2)


def estimate_flight_duration_minutes(distance_km: float, stops: int = 0) -> int:
    """Estimate gate-to-gate duration including taxi and connection time."""
    # ~820 km/h effective block speed, plus 35 min of taxi/climb/descent.
    airborne = (distance_km / 820.0) * 60.0
    total = airborne + 35.0
    # Each stop adds a realistic 75-110 minute connection.
    total += stops * 95.0
    return round(total)


# --------------------------------------------------------------------------
# Lodging cost model
# --------------------------------------------------------------------------

# Coarse nightly-rate anchors (EUR, mid-range double room) for cities whose
# price level differs sharply from their region's average. Everything else
# falls back to the regional default below.
_CITY_COST_INDEX: dict[str, float] = {
    "zurich": 2.05, "geneva": 1.95, "oslo": 1.85, "reykjavik": 1.90,
    "copenhagen": 1.70, "stockholm": 1.55, "london": 1.80, "paris": 1.65,
    "amsterdam": 1.62, "dublin": 1.58, "new york": 1.95, "san francisco": 1.98,
    "singapore": 1.60, "tokyo": 1.35, "sydney": 1.55, "dubai": 1.45,
    "venice": 1.60, "santorini": 1.62, "monaco": 2.10, "hong kong": 1.55,
    "rome": 1.30, "barcelona": 1.28, "madrid": 1.18, "vienna": 1.30,
    "berlin": 1.22, "prague": 0.92, "budapest": 0.82, "warsaw": 0.80,
    "lisbon": 1.05, "porto": 0.92, "athens": 0.95, "istanbul": 0.72,
    "bangkok": 0.62, "hanoi": 0.48, "ho chi minh city": 0.52, "bali": 0.58,
    "denpasar": 0.58, "kuala lumpur": 0.60, "delhi": 0.45, "mumbai": 0.58,
    "cairo": 0.48, "marrakesh": 0.62, "cape town": 0.68, "nairobi": 0.62,
    "mexico city": 0.72, "buenos aires": 0.62, "rio de janeiro": 0.78,
    "lima": 0.60, "bogota": 0.58, "medellin": 0.55, "tbilisi": 0.55,
    "sofia": 0.72, "bucharest": 0.78, "krakow": 0.78, "split": 1.00,
    "dubrovnik": 1.22, "valletta": 1.10, "palma": 1.15, "nice": 1.45,
}

_REGION_COST_INDEX: dict[str, float] = {
    "Northern Europe": 1.55, "Western Europe": 1.45, "Southern Europe": 1.10,
    "Central Europe": 1.05, "Eastern Europe": 0.80, "Northern America": 1.60,
    "Central America": 0.70, "South America": 0.65, "Caribbean": 1.15,
    "Eastern Asia": 1.05, "South-eastern Asia": 0.62, "Southern Asia": 0.48,
    "Western Asia": 0.85, "Central Asia": 0.55, "Australia and New Zealand": 1.50,
    "Melanesia": 0.95, "Polynesia": 1.20, "Micronesia": 1.05,
    "Northern Africa": 0.58, "Sub-Saharan Africa": 0.62, "Southern Africa": 0.68,
    "Europe": 1.20, "Asia": 0.75, "Africa": 0.60, "Americas": 1.00, "Oceania": 1.40,
}

# Base nightly rate in EUR at cost index 1.0, by accommodation class.
_LODGING_BASE: dict[str, float] = {
    "hostel": 34.0,
    "guest_house": 62.0,
    "apartment": 96.0,
    "hotel": 108.0,
    "resort": 168.0,
    "chalet": 140.0,
    "motel": 68.0,
}

_STAR_MULTIPLIER: dict[int, float] = {1: 0.55, 2: 0.72, 3: 1.00, 4: 1.42, 5: 2.35}


def city_cost_index(city: str | None, region: str | None = None) -> float:
    """Relative price level for a city, 1.0 being a mid-priced European city."""
    if city:
        hit = _CITY_COST_INDEX.get(city.strip().lower())
        if hit is not None:
            return hit
    if region:
        hit = _REGION_COST_INDEX.get(region.strip())
        if hit is not None:
            return hit
    return 1.0


def estimate_nightly_rate_eur(
    lodging_type: str,
    stars: int | None,
    check_in: date,
    *,
    city: str | None = None,
    region: str | None = None,
    guests: int = 2,
    central_distance_km: float = 1.5,
    seed_parts: tuple[object, ...] = (),
) -> float:
    """Estimate a nightly room rate in EUR.

    Combines accommodation class, star rating, city price level, seasonality,
    party size and distance from the centre. Returned as an estimate only.
    """
    base = _LODGING_BASE.get(lodging_type, _LODGING_BASE["hotel"])
    base *= _STAR_MULTIPLIER.get(int(stars or 3), 1.0)
    base *= city_cost_index(city, region)

    # Lodging seasonality is milder than airfare seasonality.
    base *= 1.0 + (_MONTH_DEMAND.get(check_in.month, 1.0) - 1.0) * 0.6

    # Central locations command a premium that decays with distance.
    base *= 1.0 + 0.28 * max(0.0, 1.0 - central_distance_km / 4.0)

    # Rooms beyond a double add capacity cost, not a linear multiple.
    if guests > 2:
        base *= 1.0 + 0.22 * (guests - 2)

    rate = jitter(base, 0.18, lodging_type, city or "", *seed_parts)
    return round(max(12.0, rate), 2)


def estimate_daily_living_cost_eur(
    city: str | None,
    region: str | None,
    style: str = "balanced",
) -> float:
    """Estimate food, local transport and incidental spend per person per day."""
    index = city_cost_index(city, region)
    base = {"budget": 32.0, "balanced": 58.0, "comfort": 95.0, "luxury": 165.0}.get(
        style, 58.0
    )
    return round(base * index, 2)
