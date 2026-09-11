#!/usr/bin/env python
"""Wayfarer Geo MCP server.

Place intelligence built on keyless open data:

* **OSM Nominatim** -- forward and reverse geocoding.
* **Overpass API** -- points of interest by category, from raw OSM tags.
* **Open-Meteo Geocoding** -- fast city lookup with population, used as the
  first-choice geocoder because it is far less rate-limited than Nominatim.
* **Bundled country dataset** -- currency, languages, driving side,
  calling code (see ``mcp_servers/common/countries.py``).
* **Built-in destination catalogue** -- deterministic candidate generation
  (see :mod:`mcp_servers.common.destinations`).

Tools
-----
``geocode_place``            Name -> coordinates, with population and country.
``reverse_geocode``          Coordinates -> address.
``suggest_destinations``     Interest/season/budget -> ranked candidate cities.
``find_points_of_interest``  Category POI search around a coordinate.
``get_country_info``         Practical country facts for the plan's briefing.
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp_servers.common.countries import lookup_country
from mcp_servers.common.destinations import (
    INTEREST_TAGS,
    budget_tier_for,
    filter_destinations,
    find_destination,
)
from mcp_servers.common.geo_math import bbox_around, haversine_km
from mcp_servers.common.http import HttpError, get_json, post_form_json
from mcp_servers.common.server_compat import create_server

NOMINATIM_URL = os.getenv("NOMINATIM_URL", "https://nominatim.openstreetmap.org")
OVERPASS_URL = os.getenv("OVERPASS_URL", "https://overpass-api.de/api/interpreter")
GEOCODE_URL = os.getenv(
    "OPEN_METEO_GEOCODE_URL", "https://geocoding-api.open-meteo.com/v1/search"
)

mcp = create_server("wayfarer-geo")

# Maps a friendly interest category onto the OSM tag filters that express it.
# Overpass has no notion of "culture" -- this table is where that translation
# lives, and keeping it here means the agents never write raw Overpass QL.
POI_CATEGORIES: dict[str, list[str]] = {
    "museums": ['["tourism"="museum"]', '["tourism"="gallery"]'],
    "art": ['["tourism"="gallery"]', '["tourism"="artwork"]'],
    "history": ['["historic"~"castle|ruins|monument|archaeological_site|fort|memorial"]'],
    "architecture": ['["tourism"="attraction"]', '["building"="cathedral"]',
                     '["historic"="building"]'],
    "culture": ['["tourism"="museum"]', '["amenity"="theatre"]',
                '["amenity"="arts_centre"]', '["historic"~"monument|memorial"]'],
    "food": ['["amenity"="restaurant"]', '["amenity"="cafe"]',
             '["amenity"="marketplace"]'],
    "nightlife": ['["amenity"~"bar|pub|nightclub"]'],
    "nature": ['["leisure"="park"]', '["leisure"="nature_reserve"]',
               '["natural"~"peak|waterfall|beach|cave_entrance"]'],
    "beach": ['["natural"="beach"]', '["leisure"="beach_resort"]'],
    "hiking": ['["natural"="peak"]', '["tourism"="viewpoint"]',
               '["route"="hiking"]'],
    "mountains": ['["natural"="peak"]', '["aerialway"~"cable_car|gondola"]'],
    "shopping": ['["shop"="mall"]', '["amenity"="marketplace"]',
                 '["shop"="department_store"]'],
    "family": ['["tourism"="zoo"]', '["tourism"="theme_park"]',
               '["tourism"="aquarium"]', '["leisure"="park"]'],
    "wellness": ['["leisure"="spa"]', '["amenity"="public_bath"]',
                 '["leisure"="sauna"]'],
    "viewpoints": ['["tourism"="viewpoint"]'],
    "attractions": ['["tourism"="attraction"]', '["tourism"="viewpoint"]',
                    '["tourism"="museum"]'],
    "transport": ['["railway"="station"]', '["amenity"="bus_station"]'],
    "wine": ['["craft"="winery"]', '["shop"="wine"]'],
    "diving": ['["shop"="scuba_diving"]', '["sport"="scuba_diving"]'],
    "skiing": ['["piste:type"]', '["aerialway"~"chair_lift|gondola"]'],
}


# --------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------


@mcp.tool()
async def geocode_place(query: str, limit: int = 5) -> dict[str, Any]:
    """Resolve a place name to coordinates and country.

    Tries the Open-Meteo geocoder first (fast, generous limits, returns
    population), then falls back to OSM Nominatim for anything it cannot find
    -- neighbourhoods, landmarks, and non-city queries.

    Args:
        query: Free-text place name, e.g. "Kyoto" or "Sagrada Familia".
        limit: Maximum results to return (1-10).
    """
    query = (query or "").strip()
    if not query:
        return {"query": query, "results": [], "error": "Empty query."}
    limit = max(1, min(10, int(limit)))

    results: list[dict[str, Any]] = []

    try:
        payload = await get_json(
            GEOCODE_URL,
            {"name": query, "count": limit, "format": "json"},
            cache_ttl=86400,
        )
        for hit in payload.get("results") or []:
            results.append(
                {
                    "name": hit.get("name"),
                    "latitude": hit.get("latitude"),
                    "longitude": hit.get("longitude"),
                    "country": hit.get("country"),
                    "country_code": hit.get("country_code"),
                    "admin1": hit.get("admin1"),
                    "population": hit.get("population"),
                    "timezone": hit.get("timezone"),
                    "elevation_m": hit.get("elevation"),
                    "source": "open-meteo-geocoding",
                }
            )
    except HttpError:
        pass  # Fall through to Nominatim.

    if not results:
        try:
            payload = await get_json(
                f"{NOMINATIM_URL}/search",
                {
                    "q": query,
                    "format": "jsonv2",
                    "limit": limit,
                    "addressdetails": 1,
                    "accept-language": "en",
                },
                cache_ttl=86400,
            )
            for hit in payload or []:
                address = hit.get("address") or {}
                results.append(
                    {
                        "name": hit.get("name") or hit.get("display_name"),
                        "display_name": hit.get("display_name"),
                        "latitude": float(hit["lat"]),
                        "longitude": float(hit["lon"]),
                        "country": address.get("country"),
                        "country_code": (address.get("country_code") or "").upper() or None,
                        "admin1": address.get("state") or address.get("region"),
                        "osm_type": hit.get("osm_type"),
                        "category": hit.get("category"),
                        "source": "osm-nominatim",
                    }
                )
        except HttpError as exc:
            return {"query": query, "results": [], "error": str(exc)}

    return {
        "query": query,
        "results": results,
        "attribution": "Data (c) OpenStreetMap contributors (ODbL) / Open-Meteo",
    }


@mcp.tool()
async def reverse_geocode(latitude: float, longitude: float) -> dict[str, Any]:
    """Resolve coordinates to a human-readable address via OSM Nominatim."""
    try:
        payload = await get_json(
            f"{NOMINATIM_URL}/reverse",
            {
                "lat": round(latitude, 6),
                "lon": round(longitude, 6),
                "format": "jsonv2",
                "addressdetails": 1,
                "accept-language": "en",
            },
            cache_ttl=86400,
        )
    except HttpError as exc:
        return {"error": str(exc)}

    address = payload.get("address") or {}
    return {
        "display_name": payload.get("display_name"),
        "city": address.get("city") or address.get("town") or address.get("village"),
        "suburb": address.get("suburb") or address.get("neighbourhood"),
        "postcode": address.get("postcode"),
        "country": address.get("country"),
        "country_code": (address.get("country_code") or "").upper() or None,
        "attribution": "Data (c) OpenStreetMap contributors (ODbL)",
    }


@mcp.tool()
async def suggest_destinations(
    interests: list[str] | None = None,
    travel_month: int | None = None,
    budget_per_day_eur: float | None = None,
    regions: list[str] | None = None,
    exclude_countries: list[str] | None = None,
    exclude_cities: list[str] | None = None,
    limit: int = 6,
) -> dict[str, Any]:
    """Rank candidate destinations against a traveller's stated preferences.

    Returns catalogue entries with coordinates already attached, so the Weather
    Analyst can score each candidate without a geocoding round-trip. Every
    result includes a ``match`` block explaining *why* it ranked where it did.

    Args:
        interests: Interest tags; see ``vocabulary`` in the response.
        travel_month: Month of travel, 1-12. Strongly affects ranking.
        budget_per_day_eur: Per-person daily budget, used to cap price tier.
        regions: Restrict to UN sub-regions, e.g. ["Southern Europe"].
        exclude_countries: Country names to rule out.
        exclude_cities: City names the traveller has already dismissed.
        limit: Number of candidates to return (1-12).
    """
    tier = budget_tier_for(budget_per_day_eur)
    candidates = filter_destinations(
        interests=interests,
        month=travel_month,
        max_budget_tier=tier,
        regions=regions,
        exclude_countries=exclude_countries,
        exclude_cities=exclude_cities,
        limit=max(1, min(12, int(limit))),
    )

    # If the budget filter emptied the list, relax it rather than returning
    # nothing -- the validator downstream will flag the cost honestly.
    relaxed = False
    if not candidates and tier is not None:
        relaxed = True
        candidates = filter_destinations(
            interests=interests,
            month=travel_month,
            max_budget_tier=None,
            regions=regions,
            exclude_countries=exclude_countries,
            exclude_cities=exclude_cities,
            limit=max(1, min(12, int(limit))),
        )

    return {
        "candidates": candidates,
        "vocabulary": sorted(set(INTEREST_TAGS)),
        "budget_tier_applied": None if relaxed else tier,
        "budget_filter_relaxed": relaxed,
        "note": (
            "Candidates come from a curated open catalogue. Coordinates are "
            "city-centre points suitable for weather scoring."
        ),
    }


@mcp.tool()
async def lookup_destination(name: str) -> dict[str, Any]:
    """Fetch full catalogue detail for a destination the user named directly.

    Falls back to geocoding when the city is not in the curated catalogue, so
    an off-catalogue request ("take me to Tallinn") still produces a usable
    coordinate, country and timezone for planning.
    """
    entry = find_destination(name)
    if entry:
        return {"found": True, "source": "catalogue", "destination": entry}

    geo = await geocode_place(name, limit=1)
    hits = geo.get("results") or []
    if not hits:
        return {"found": False, "source": "geocoder", "destination": None}

    hit = hits[0]
    return {
        "found": True,
        "source": "geocoder",
        "destination": {
            "city": hit.get("name"),
            "country": hit.get("country"),
            "iso2": hit.get("country_code"),
            "region": None,
            "lat": hit.get("latitude"),
            "lon": hit.get("longitude"),
            "budget_tier": None,
            "best_months": [],
            "tags": [],
            "blurb": None,
            "timezone": hit.get("timezone"),
        },
    }


@mcp.tool()
async def find_points_of_interest(
    latitude: float,
    longitude: float,
    category: str = "attractions",
    radius_km: float = 5.0,
    limit: int = 12,
) -> dict[str, Any]:
    """Find real OSM points of interest near a coordinate.

    Powers the day-by-day itinerary: the planner asks for the categories that
    match the traveller's interests and lays them out geographically so each
    day's stops are walkable from one another.

    Args:
        latitude: Centre latitude.
        longitude: Centre longitude.
        category: One of the supported categories (see ``categories`` in the
            response); unknown values fall back to "attractions".
        radius_km: Search radius, 0.5-30 km.
        limit: Maximum POIs to return (1-40).
    """
    radius_km = max(0.5, min(30.0, float(radius_km)))
    limit = max(1, min(40, int(limit)))
    filters = POI_CATEGORIES.get(category.strip().lower(), POI_CATEGORIES["attractions"])

    radius_m = int(radius_km * 1000)
    clauses = "".join(
        f'node{f}(around:{radius_m},{latitude:.5f},{longitude:.5f});'
        f'way{f}(around:{radius_m},{latitude:.5f},{longitude:.5f});'
        for f in filters
    )
    # `out center` gives ways a representative coordinate; `qt` sorts by
    # proximity, which keeps the truncation at `limit` sensible.
    query = f"[out:json][timeout:30];({clauses});out center {limit * 3} qt;"

    try:
        payload = await post_form_json(OVERPASS_URL, f"data={query}", cache_ttl=86400)
    except HttpError as exc:
        return {"category": category, "places": [], "error": str(exc)}

    seen: set[str] = set()
    places: list[dict[str, Any]] = []
    for element in payload.get("elements") or []:
        tags = element.get("tags") or {}
        name = tags.get("name") or tags.get("name:en")
        if not name or name.lower() in seen:
            continue

        lat = element.get("lat") or (element.get("center") or {}).get("lat")
        lon = element.get("lon") or (element.get("center") or {}).get("lon")
        if lat is None or lon is None:
            continue

        seen.add(name.lower())
        places.append(
            {
                "name": name,
                "latitude": lat,
                "longitude": lon,
                "distance_km": round(haversine_km(latitude, longitude, lat, lon), 2),
                "kind": (
                    tags.get("tourism") or tags.get("historic") or tags.get("amenity")
                    or tags.get("leisure") or tags.get("natural") or tags.get("shop")
                ),
                "opening_hours": tags.get("opening_hours"),
                "website": tags.get("website") or tags.get("contact:website"),
                "wheelchair": tags.get("wheelchair"),
                "fee": tags.get("fee"),
                "cuisine": tags.get("cuisine"),
                "osm_id": f"{element.get('type')}/{element.get('id')}",
            }
        )

    places.sort(key=lambda p: p["distance_km"])
    return {
        "category": category,
        "categories": sorted(POI_CATEGORIES),
        "centre": {"latitude": latitude, "longitude": longitude},
        "radius_km": radius_km,
        "places": places[:limit],
        "attribution": "Data (c) OpenStreetMap contributors (ODbL)",
    }


@mcp.tool()
async def get_country_info(country: str) -> dict[str, Any]:
    """Practical country facts: currency, languages, driving side, calling code.

    Feeds the "know before you go" section of the plan and tells the currency
    server which local currency to convert the trip total into.

    DATA SOURCE NOTE: this used to call restcountries.com, which moved its open
    API behind an authorization key during development and so no longer meets
    this project's keyless constraint. The data now ships bundled in
    ``mcp_servers/common/countries.py`` -- see that module for why bundling is
    the better answer for facts that change on a scale of years.

    Args:
        country: Country name, common alias, or ISO 3166-1 alpha-2/alpha-3 code.
    """
    country = (country or "").strip()
    if not country:
        return {"found": False, "error": "Empty country."}

    record = lookup_country(country)
    if record is None:
        return {
            "found": False,
            "country": country,
            "reason": (
                "Not in the bundled country dataset. Currency and language "
                "details will be omitted from the briefing."
            ),
        }

    return {
        "found": True,
        "name": record["name"],
        "official_name": record["name"],
        "iso2": record["iso2"],
        "iso3": record["iso3"],
        "capital": record["capital"],
        "region": record["region"],
        "subregion": record["subregion"],
        "currency_code": record["currency_code"],
        "currency_name": record["currency_name"],
        "currency_symbol": record["currency_symbol"],
        "languages": record["languages"],
        "timezones": [record["timezone"]],
        "drives_on": record["drives_on"],
        "calling_code": record["calling_code"],
        "attribution": (
            "Bundled dataset compiled from ISO 3166-1, ISO 4217, ITU-T E.164 "
            "and the IANA tz database."
        ),
        "note": (
            "Visa and entry requirements are NOT included -- no free keyless "
            "source is authoritative. Always check the official embassy site."
        ),
    }


@mcp.tool()
async def bounding_box(latitude: float, longitude: float, radius_km: float) -> dict[str, Any]:
    """Compute a lat/lon bounding box around a point (helper for map views)."""
    south, west, north, east = bbox_around(latitude, longitude, radius_km)
    return {"south": south, "west": west, "north": north, "east": east}


def main() -> None:
    mcp.run(transport=os.getenv("MCP_TRANSPORT", "stdio"))


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
