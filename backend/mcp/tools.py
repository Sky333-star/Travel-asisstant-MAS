"""Typed, agent-facing wrappers over the raw MCP tool calls.

Agents call these functions rather than :meth:`MCPManager.call` directly, for
three reasons:

1. **The failure contract lives here.** Each wrapper decides what a sensible
   degraded answer looks like -- an empty candidate list, ``None`` weather, a
   1.0 exchange rate -- so agent code contains planning logic, not error
   handling.
2. **Arguments are validated once.** Coordinates, dates and enums are coerced
   at this boundary, so a malformed LLM-suggested argument never reaches a
   subprocess.
3. **Call sites stay readable.** ``await score_weather(lat, lon, start, end)``
   documents itself; ``await mcp.call("weather", "score_destination_weather",
   {...})`` does not.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any

from ..schemas.travel import ToolInvocation
from .client import mcp_manager

logger = logging.getLogger(__name__)


def _iso(value: date | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value.isoformat()
    return str(value)[:10]


def _record(
    trace: list[ToolInvocation] | None,
    server: str,
    tool: str,
    arguments: dict[str, Any],
    ok: bool,
    summary: str | None = None,
) -> None:
    """Append a tool invocation to the conversation trace shown in the UI."""
    if trace is None:
        return
    trace.append(
        ToolInvocation(
            server=server, tool=tool, arguments=arguments, ok=ok, result_summary=summary
        )
    )


# --------------------------------------------------------------------------
# Weather
# --------------------------------------------------------------------------


async def score_weather(
    latitude: float,
    longitude: float,
    start: date | str,
    end: date | str,
    preference: str = "mild",
    trace: list[ToolInvocation] | None = None,
) -> dict[str, Any] | None:
    """Comfort score (0-100) for a destination over a trip window."""
    args = {
        "latitude": round(float(latitude), 4),
        "longitude": round(float(longitude), 4),
        "start_date": _iso(start),
        "end_date": _iso(end),
        "preference": preference if preference != "any" else "mild",
    }
    result = await mcp_manager.call("weather", "score_destination_weather", args)
    ok = isinstance(result, dict) and result.get("score") is not None
    _record(
        trace, "weather", "score_destination_weather", args, ok,
        f"score={result.get('score')} basis={result.get('basis')}" if ok else "no score",
    )
    return result if isinstance(result, dict) else None


async def get_forecast(
    latitude: float,
    longitude: float,
    start: date | str,
    end: date | str,
    trace: list[ToolInvocation] | None = None,
) -> dict[str, Any] | None:
    """Day-by-day forecast, used to annotate the itinerary."""
    args = {
        "latitude": round(float(latitude), 4),
        "longitude": round(float(longitude), 4),
        "start_date": _iso(start),
        "end_date": _iso(end),
    }
    result = await mcp_manager.call("weather", "get_forecast", args)
    ok = isinstance(result, dict) and bool(result.get("days"))
    _record(trace, "weather", "get_forecast", args, ok,
            f"{len(result.get('days', []))} days" if ok else "no data")
    return result if isinstance(result, dict) else None


async def get_air_quality(
    latitude: float,
    longitude: float,
    trace: list[ToolInvocation] | None = None,
) -> dict[str, Any] | None:
    """Air quality and pollen, requested only when health notes warrant it."""
    args = {"latitude": round(float(latitude), 3), "longitude": round(float(longitude), 3)}
    result = await mcp_manager.call("weather", "get_air_quality", args)
    ok = isinstance(result, dict) and result.get("available") is True
    _record(trace, "weather", "get_air_quality", args, ok,
            result.get("aqi_band") if ok else "unavailable")
    return result if isinstance(result, dict) else None


# --------------------------------------------------------------------------
# Geo
# --------------------------------------------------------------------------


async def suggest_destinations(
    interests: list[str] | None,
    travel_month: int | None,
    budget_per_day_eur: float | None = None,
    regions: list[str] | None = None,
    exclude_countries: list[str] | None = None,
    exclude_cities: list[str] | None = None,
    limit: int = 6,
    trace: list[ToolInvocation] | None = None,
) -> list[dict[str, Any]]:
    """Ranked destination candidates. Returns ``[]`` if the server is down."""
    args = {
        "interests": interests or [],
        "travel_month": travel_month,
        "budget_per_day_eur": budget_per_day_eur,
        "regions": regions or [],
        "exclude_countries": exclude_countries or [],
        "exclude_cities": exclude_cities or [],
        "limit": int(limit),
    }
    result = await mcp_manager.call("geo", "suggest_destinations", args, default={})
    candidates = (result or {}).get("candidates") or []
    _record(trace, "geo", "suggest_destinations", args, bool(candidates),
            f"{len(candidates)} candidates")
    return candidates


async def lookup_destination(
    name: str, trace: list[ToolInvocation] | None = None
) -> dict[str, Any] | None:
    """Resolve a user-named destination to a full record."""
    args = {"name": name}
    result = await mcp_manager.call("geo", "lookup_destination", args, default={})
    found = (result or {}).get("found") and (result or {}).get("destination")
    _record(trace, "geo", "lookup_destination", args, bool(found),
            (result or {}).get("source"))
    return (result or {}).get("destination") if found else None


async def geocode(
    query: str, trace: list[ToolInvocation] | None = None
) -> dict[str, Any] | None:
    """First geocoding hit for a free-text place name."""
    args = {"query": query, "limit": 1}
    result = await mcp_manager.call("geo", "geocode_place", args, default={})
    hits = (result or {}).get("results") or []
    _record(trace, "geo", "geocode_place", args, bool(hits),
            hits[0].get("name") if hits else "no match")
    return hits[0] if hits else None


async def find_pois(
    latitude: float,
    longitude: float,
    category: str,
    radius_km: float = 5.0,
    limit: int = 12,
    trace: list[ToolInvocation] | None = None,
) -> list[dict[str, Any]]:
    """Points of interest for one interest category."""
    args = {
        "latitude": round(float(latitude), 5),
        "longitude": round(float(longitude), 5),
        "category": category,
        "radius_km": float(radius_km),
        "limit": int(limit),
    }
    result = await mcp_manager.call("geo", "find_points_of_interest", args, default={})
    places = (result or {}).get("places") or []
    _record(trace, "geo", "find_points_of_interest", args, bool(places),
            f"{len(places)} places")
    return places


async def find_pois_multi(
    latitude: float,
    longitude: float,
    categories: list[str],
    radius_km: float = 5.0,
    per_category: int = 8,
    trace: list[ToolInvocation] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Fetch several POI categories.

    Overpass is rate-limited to ~0.5 req/s, so these are issued sequentially
    on purpose: firing them concurrently would just queue inside the limiter
    while risking a 429 that costs more time than it saves.
    """
    out: dict[str, list[dict[str, Any]]] = {}
    for category in categories[:5]:
        try:
            out[category] = await find_pois(
                latitude, longitude, category, radius_km, per_category, trace
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("POI fetch failed for %s: %s", category, exc)
            out[category] = []
    return out


async def get_country_info(
    country: str, trace: list[ToolInvocation] | None = None
) -> dict[str, Any] | None:
    """Practical country facts for the traveller briefing."""
    args = {"country": country}
    result = await mcp_manager.call("geo", "get_country_info", args, default={})
    ok = bool((result or {}).get("found"))
    _record(trace, "geo", "get_country_info", args, ok,
            (result or {}).get("currency_code") if ok else "not found")
    return result if ok else None


# --------------------------------------------------------------------------
# Flights
# --------------------------------------------------------------------------


async def search_flights(
    origin: str,
    destination: str,
    depart_date: date | str,
    return_date: date | str | None = None,
    passengers: int = 1,
    cabin: str = "economy",
    trace: list[ToolInvocation] | None = None,
) -> dict[str, Any] | None:
    """Modelled flight options. Prices are estimates, never live fares."""
    args = {
        "origin": origin,
        "destination": destination,
        "depart_date": _iso(depart_date),
        "return_date": _iso(return_date),
        "passengers": int(passengers),
        "cabin": cabin,
        "max_results": 4,
    }
    result = await mcp_manager.call("flights", "search_flights", args, default={})
    ok = isinstance(result, dict) and bool(result.get("outbound"))
    _record(
        trace, "flights", "search_flights", args, ok,
        f"{len(result.get('outbound', []))} outbound, "
        f"cheapest={result.get('cheapest_round_trip_eur')} EUR" if ok
        else (result or {}).get("error", "no options"),
    )
    return result if ok else None


async def resolve_airport(
    query: str, trace: list[ToolInvocation] | None = None
) -> dict[str, Any] | None:
    args = {"query": query}
    result = await mcp_manager.call("flights", "resolve_airport", args, default={})
    ok = bool((result or {}).get("found"))
    _record(trace, "flights", "resolve_airport", args, ok,
            ((result or {}).get("airport") or {}).get("iata") if ok else "not found")
    return (result or {}).get("airport") if ok else None


# --------------------------------------------------------------------------
# Hotels
# --------------------------------------------------------------------------


async def search_accommodation(
    latitude: float,
    longitude: float,
    check_in: date | str,
    check_out: date | str,
    guests: int = 2,
    rooms: int = 1,
    radius_km: float = 4.0,
    lodging_types: list[str] | None = None,
    max_nightly_eur: float | None = None,
    min_stars: int | None = None,
    city: str | None = None,
    region: str | None = None,
    limit: int = 10,
    trace: list[ToolInvocation] | None = None,
) -> dict[str, Any] | None:
    """Real OSM properties with modelled nightly rates."""
    args = {
        "latitude": round(float(latitude), 5),
        "longitude": round(float(longitude), 5),
        "check_in": _iso(check_in),
        "check_out": _iso(check_out),
        "guests": int(guests),
        "rooms": int(rooms),
        "radius_km": float(radius_km),
        "lodging_types": lodging_types or ["hotel", "guest_house", "apartment"],
        "max_nightly_eur": max_nightly_eur,
        "min_stars": min_stars,
        "city": city,
        "region": region,
        "limit": int(limit),
    }
    result = await mcp_manager.call("hotels", "search_accommodation", args, default={})
    properties = (result or {}).get("properties") or []
    _record(trace, "hotels", "search_accommodation", args, bool(properties),
            f"{len(properties)} properties")
    return result if properties else None


async def neighbourhood_context(
    latitude: float,
    longitude: float,
    radius_m: int = 700,
    trace: list[ToolInvocation] | None = None,
) -> dict[str, Any] | None:
    """Walkability around a candidate hotel."""
    args = {
        "latitude": round(float(latitude), 5),
        "longitude": round(float(longitude), 5),
        "radius_m": int(radius_m),
    }
    result = await mcp_manager.call("hotels", "get_neighbourhood_context", args, default={})
    ok = bool((result or {}).get("available"))
    _record(trace, "hotels", "get_neighbourhood_context", args, ok,
            (result or {}).get("verdict") if ok else "unavailable")
    return result if ok else None


async def estimate_stay_cost(
    nightly_eur: float,
    check_in: date | str,
    check_out: date | str,
    rooms: int = 1,
    city: str | None = None,
    region: str | None = None,
    travellers: int = 2,
    style: str = "balanced",
    trace: list[ToolInvocation] | None = None,
) -> dict[str, Any] | None:
    """Lodging plus daily living cost for the stay."""
    args = {
        "nightly_eur": float(nightly_eur),
        "check_in": _iso(check_in),
        "check_out": _iso(check_out),
        "rooms": int(rooms),
        "city": city,
        "region": region,
        "travellers": int(travellers),
        "style": style,
    }
    result = await mcp_manager.call("hotels", "estimate_stay_cost", args, default={})
    ok = isinstance(result, dict) and result.get("ground_total_eur") is not None
    _record(trace, "hotels", "estimate_stay_cost", args, ok,
            f"{result.get('ground_total_eur')} EUR" if ok else "unavailable")
    return result if ok else None


# --------------------------------------------------------------------------
# Currency
# --------------------------------------------------------------------------


async def convert_currency(
    amount: float,
    from_currency: str,
    to_currency: str,
    trace: list[ToolInvocation] | None = None,
) -> dict[str, Any] | None:
    """Convert an amount using real ECB reference rates."""
    if not amount or from_currency.upper() == to_currency.upper():
        return {
            "amount": amount, "from": from_currency.upper(),
            "to": to_currency.upper(), "rate": 1.0, "converted": amount,
        }
    args = {
        "amount": float(amount),
        "from_currency": from_currency,
        "to_currency": to_currency,
    }
    result = await mcp_manager.call("currency", "convert", args, default={})
    ok = isinstance(result, dict) and result.get("converted") is not None
    _record(trace, "currency", "convert", args, ok,
            f"{result.get('converted')} {result.get('to')}" if ok
            else (result or {}).get("error", "failed"))
    return result if ok else None


async def convert_budget_to_eur(
    amount: float, currency: str, trace: list[ToolInvocation] | None = None
) -> float | None:
    """Normalise a stated budget into EUR, the planner's working unit."""
    if currency.upper() == "EUR":
        return float(amount)
    result = await convert_currency(amount, currency, "EUR", trace)
    return float(result["converted"]) if result and result.get("converted") else None


async def warm_up() -> dict[str, Any]:
    """Prefetch the slow datasets so the first real request is not penalised.

    The OurAirports CSV is several megabytes and the FX rates are a single
    round trip; paying for both at startup turns a 6-second first search into
    a sub-second one.
    """
    async def airports() -> Any:
        return await mcp_manager.call(
            "flights", "find_airports",
            {"latitude": 48.8566, "longitude": 2.3522, "radius_km": 60, "limit": 1},
            default=None,
        )

    async def rates() -> Any:
        return await mcp_manager.call(
            "currency", "get_rates", {"base": "EUR"}, default=None
        )

    results = await asyncio.gather(airports(), rates(), return_exceptions=True)
    return {
        "airports_warm": not isinstance(results[0], Exception) and results[0] is not None,
        "currency_warm": not isinstance(results[1], Exception) and results[1] is not None,
    }
