#!/usr/bin/env python
"""Wayfarer Hotels MCP server.

DATA HONESTY STATEMENT
----------------------
* **Properties are real.** Every hotel, hostel, guest house and apartment
  returned here is an actual OpenStreetMap object, with its real name, tags,
  coordinates, star rating (where mapped), website and address.
* **Rates and availability are modelled.** No keyless API publishes live room
  rates. Nightly prices come from the documented model in
  :mod:`mcp_servers.common.pricing`, driven by accommodation class, stars,
  city price level, seasonality, party size and distance from the centre.

Responses are stamped ``"pricing": "estimated"`` so the agents and UI can
label them. Availability is presented as a modelled likelihood, never as a
confirmed booking.

Tools
-----
``search_accommodation``   Real OSM lodging near a coordinate, with estimated
                          nightly rates for the requested stay.
``estimate_stay_cost``     Total lodging cost for a date range and party.
``get_neighbourhood_context``  What is walkable around a candidate hotel --
                          the signal the validator uses to check that a hotel
                          is not stranded away from the itinerary.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import date, datetime, timedelta
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp_servers.common.geo_math import haversine_km
from mcp_servers.common.http import HttpError, post_form_json
from mcp_servers.common.pricing import (
    city_cost_index,
    estimate_daily_living_cost_eur,
    estimate_nightly_rate_eur,
    stable_unit,
)
from mcp_servers.common.server_compat import create_server

OVERPASS_URL = os.getenv("OVERPASS_URL", "https://overpass-api.de/api/interpreter")

mcp = create_server("wayfarer-hotels")

# OSM tourism tags that represent somewhere you can sleep, mapped to the
# accommodation classes the pricing model understands.
LODGING_TAGS: dict[str, str] = {
    "hotel": "hotel",
    "hostel": "hostel",
    "guest_house": "guest_house",
    "apartment": "apartment",
    "motel": "motel",
    "resort": "resort",
    "chalet": "chalet",
}


def _parse_date(value: str | None, fallback: date) -> date:
    if not value:
        return fallback
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return fallback


def _infer_stars(tags: dict[str, str], lodging_type: str, seed: str) -> int:
    """Use the mapped OSM ``stars`` tag when present, else infer a plausible one.

    Most OSM lodging objects have no stars tag, so we infer from the class and
    from amenity hints. The inference is deterministic and flagged in the
    response via ``stars_source`` so nothing looks more authoritative than it is.
    """
    raw = tags.get("stars")
    if raw:
        try:
            value = int(float(str(raw).split(",")[0].strip()))
            if 1 <= value <= 5:
                return value
        except ValueError:
            pass

    if lodging_type == "hostel":
        return 2
    if lodging_type == "resort":
        return 5 if tags.get("swimming_pool") or tags.get("spa") else 4
    if lodging_type in ("guest_house", "motel"):
        return 3

    # Amenity hints nudge hotels up a notch.
    bonus = sum(
        1 for key in ("spa", "swimming_pool", "restaurant", "fitness_centre")
        if tags.get(key) in ("yes", "1", "true")
    )
    base = 3 + (1 if bonus >= 2 else 0)
    return min(5, base + (1 if stable_unit("stars", seed) > 0.82 else 0))


def _amenities(tags: dict[str, str]) -> list[str]:
    mapping = {
        "internet_access": "wifi",
        "wheelchair": "accessible",
        "swimming_pool": "pool",
        "spa": "spa",
        "restaurant": "restaurant",
        "bar": "bar",
        "parking": "parking",
        "air_conditioning": "air_conditioning",
        "breakfast": "breakfast",
        "laundry_service": "laundry",
        "fitness_centre": "gym",
        "pets": "pets_allowed",
    }
    found = []
    for key, label in mapping.items():
        value = tags.get(key)
        if value and value not in ("no", "false", "0"):
            found.append(label)
    return sorted(found)


# --------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------


@mcp.tool()
async def search_accommodation(
    latitude: float,
    longitude: float,
    check_in: str,
    check_out: str,
    guests: int = 2,
    rooms: int = 1,
    radius_km: float = 4.0,
    lodging_types: list[str] | None = None,
    max_nightly_eur: float | None = None,
    min_stars: int | None = None,
    city: str | None = None,
    region: str | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """Find real OSM lodging near a coordinate with estimated nightly rates.

    Args:
        latitude: Search centre latitude (usually the city centre).
        longitude: Search centre longitude.
        check_in: ISO date YYYY-MM-DD.
        check_out: ISO date YYYY-MM-DD.
        guests: Total guests (1-12).
        rooms: Rooms required (1-6).
        radius_km: Search radius, 0.5-15 km.
        lodging_types: Subset of hotel|hostel|guest_house|apartment|resort|
            motel|chalet. Defaults to hotel, guest_house and apartment.
        max_nightly_eur: Drop properties estimated above this nightly rate.
        min_stars: Minimum star rating (1-5).
        city: City name -- improves the price estimate materially.
        region: UN sub-region, used as a price fallback when city is unknown.
        limit: Maximum properties to return (1-25).
    """
    guests = max(1, min(12, int(guests)))
    rooms = max(1, min(6, int(rooms)))
    radius_km = max(0.5, min(15.0, float(radius_km)))
    limit = max(1, min(25, int(limit)))

    today = date.today()
    start = _parse_date(check_in, today + timedelta(days=30))
    end = _parse_date(check_out, start + timedelta(days=3))
    if end <= start:
        end = start + timedelta(days=1)
    nights = (end - start).days

    wanted = [t for t in (lodging_types or ["hotel", "guest_house", "apartment"])
              if t in LODGING_TAGS]
    if not wanted:
        wanted = ["hotel"]

    radius_m = int(radius_km * 1000)
    clauses = "".join(
        f'node["tourism"="{t}"](around:{radius_m},{latitude:.5f},{longitude:.5f});'
        f'way["tourism"="{t}"](around:{radius_m},{latitude:.5f},{longitude:.5f});'
        for t in wanted
    )
    query = f"[out:json][timeout:40];({clauses});out center 200 qt;"

    try:
        payload = await post_form_json(OVERPASS_URL, f"data={query}", cache_ttl=86400)
    except HttpError as exc:
        return {
            "properties": [],
            "error": f"Overpass lookup failed: {exc}",
            "nights": nights,
        }

    guests_per_room = max(1, -(-guests // rooms))  # ceiling division
    properties: list[dict[str, Any]] = []
    seen: set[str] = set()

    for element in payload.get("elements") or []:
        tags = element.get("tags") or {}
        name = tags.get("name") or tags.get("name:en")
        if not name:
            continue  # Unnamed lodging is useless to show a traveller.
        key = name.strip().lower()
        if key in seen:
            continue

        lat = element.get("lat") or (element.get("center") or {}).get("lat")
        lon = element.get("lon") or (element.get("center") or {}).get("lon")
        if lat is None or lon is None:
            continue

        lodging_type = LODGING_TAGS.get(tags.get("tourism", "hotel"), "hotel")
        osm_id = f"{element.get('type')}/{element.get('id')}"
        stars = _infer_stars(tags, lodging_type, osm_id)
        if min_stars and stars < int(min_stars):
            continue

        distance = round(haversine_km(latitude, longitude, lat, lon), 2)
        nightly = estimate_nightly_rate_eur(
            lodging_type, stars, start,
            city=city, region=region, guests=guests_per_room,
            central_distance_km=distance, seed_parts=(osm_id,),
        )
        if max_nightly_eur and nightly > float(max_nightly_eur):
            continue

        seen.add(key)
        address_parts = [
            tags.get("addr:street"), tags.get("addr:housenumber"),
            tags.get("addr:postcode"), tags.get("addr:city"),
        ]
        properties.append(
            {
                "id": osm_id,
                "name": name,
                "lodging_type": lodging_type,
                "stars": stars,
                "stars_source": "osm_tag" if tags.get("stars") else "inferred",
                "latitude": lat,
                "longitude": lon,
                "distance_from_centre_km": distance,
                "address": ", ".join(p for p in address_parts if p) or None,
                "website": tags.get("website") or tags.get("contact:website"),
                "phone": tags.get("phone") or tags.get("contact:phone"),
                "amenities": _amenities(tags),
                "wheelchair_accessible": tags.get("wheelchair") == "yes",
                "estimated_nightly_eur": nightly,
                "estimated_total_eur": round(nightly * nights * rooms, 2),
                "rooms": rooms,
                "guests_per_room": guests_per_room,
                # Modelled likelihood, deterministic per property and date.
                "availability_likelihood": round(
                    0.55 + 0.4 * stable_unit("avail", osm_id, start.isoformat()), 2
                ),
                "pricing": "estimated",
                "data_basis": "osm_entity_modelled_price",
            }
        )

    # Value ranking: cheap and central beats cheap and remote.
    properties.sort(
        key=lambda p: p["estimated_nightly_eur"] * (1 + 0.10 * p["distance_from_centre_km"])
    )

    return {
        "check_in": start.isoformat(),
        "check_out": end.isoformat(),
        "nights": nights,
        "guests": guests,
        "rooms": rooms,
        "currency": "EUR",
        "city_cost_index": city_cost_index(city, region),
        "properties": properties[:limit],
        "total_found": len(properties),
        "pricing": "estimated",
        "attribution": "Property data (c) OpenStreetMap contributors (ODbL)",
        "disclaimer": (
            "Properties are real OSM entities. Nightly rates and availability "
            "are MODELLED estimates -- no keyless API publishes live rates. "
            "Confirm directly with the property before relying on a price."
        ),
    }


@mcp.tool()
async def estimate_stay_cost(
    nightly_eur: float,
    check_in: str,
    check_out: str,
    rooms: int = 1,
    city: str | None = None,
    region: str | None = None,
    travellers: int = 2,
    style: str = "balanced",
) -> dict[str, Any]:
    """Total ground cost for a stay: lodging plus daily living expenses.

    Separating lodging from living cost matters because the validator checks
    them against different parts of the budget, and because food and transport
    scale with traveller count while a room does not.

    Args:
        nightly_eur: Chosen property's estimated nightly rate.
        check_in: ISO date.
        check_out: ISO date.
        rooms: Rooms booked.
        city: Destination city, for the local cost index.
        region: UN sub-region fallback.
        travellers: Number of travellers, for food/transport scaling.
        style: budget | balanced | comfort | luxury.
    """
    start = _parse_date(check_in, date.today())
    end = _parse_date(check_out, start + timedelta(days=3))
    nights = max(1, (end - start).days)

    lodging_total = round(float(nightly_eur) * nights * max(1, int(rooms)), 2)
    per_person_day = estimate_daily_living_cost_eur(city, region, style)
    living_total = round(per_person_day * max(1, int(travellers)) * (nights + 1), 2)

    return {
        "nights": nights,
        "lodging_total_eur": lodging_total,
        "daily_living_per_person_eur": per_person_day,
        "living_total_eur": living_total,
        "ground_total_eur": round(lodging_total + living_total, 2),
        "style": style,
        "note": (
            "Living cost covers food, local transport and incidentals for "
            f"{nights + 1} days (arrival and departure days both count)."
        ),
        "pricing": "estimated",
    }


@mcp.tool()
async def get_neighbourhood_context(
    latitude: float,
    longitude: float,
    radius_m: int = 700,
) -> dict[str, Any]:
    """What is within walking distance of a candidate property.

    The validator calls this to catch a specific failure mode: a cheap hotel
    that is technically "in" the city but has no transit, food or attractions
    nearby, which quietly wrecks an itinerary built around walkable days.

    Args:
        latitude: Property latitude.
        longitude: Property longitude.
        radius_m: Walking radius in metres (200-2000).
    """
    radius_m = max(200, min(2000, int(radius_m)))
    groups = {
        "transit": '["public_transport"="station"],["railway"~"station|subway_entrance"],'
                   '["highway"="bus_stop"]',
        "food": '["amenity"~"restaurant|cafe|bar|fast_food"]',
        "groceries": '["shop"~"supermarket|convenience"]',
        "attractions": '["tourism"~"attraction|museum|viewpoint|gallery"]',
        "pharmacy": '["amenity"="pharmacy"]',
    }

    clauses = []
    for filters in groups.values():
        for one in filters.split("],["):
            token = one.strip().strip("[]")
            if token:
                clauses.append(
                    f'node[{token}](around:{radius_m},{latitude:.5f},{longitude:.5f});'
                )
    query = f"[out:json][timeout:30];({''.join(clauses)});out tags center 400 qt;"

    try:
        payload = await post_form_json(OVERPASS_URL, f"data={query}", cache_ttl=86400)
    except HttpError as exc:
        return {"available": False, "error": str(exc)}

    counts = dict.fromkeys(groups, 0)
    nearest_transit: dict[str, Any] | None = None

    for element in payload.get("elements") or []:
        tags = element.get("tags") or {}
        lat = element.get("lat") or (element.get("center") or {}).get("lat")
        lon = element.get("lon") or (element.get("center") or {}).get("lon")

        if tags.get("public_transport") == "station" or tags.get("railway") in (
            "station", "subway_entrance"
        ) or tags.get("highway") == "bus_stop":
            counts["transit"] += 1
            if lat is not None and lon is not None:
                distance = haversine_km(latitude, longitude, lat, lon) * 1000
                if nearest_transit is None or distance < nearest_transit["distance_m"]:
                    nearest_transit = {
                        "name": tags.get("name") or "Unnamed stop",
                        "kind": tags.get("railway") or tags.get("public_transport")
                        or tags.get("highway"),
                        "distance_m": round(distance),
                    }
        if tags.get("amenity") in ("restaurant", "cafe", "bar", "fast_food"):
            counts["food"] += 1
        if tags.get("shop") in ("supermarket", "convenience"):
            counts["groceries"] += 1
        if tags.get("tourism") in ("attraction", "museum", "viewpoint", "gallery"):
            counts["attractions"] += 1
        if tags.get("amenity") == "pharmacy":
            counts["pharmacy"] += 1

    # A simple, explainable walkability heuristic capped at 100.
    score = min(
        100,
        counts["food"] * 4 + counts["transit"] * 6 + counts["groceries"] * 8
        + counts["attractions"] * 5 + counts["pharmacy"] * 4,
    )

    return {
        "available": True,
        "radius_m": radius_m,
        "counts": counts,
        "nearest_transit": nearest_transit,
        "walkability_score": score,
        "verdict": (
            "excellent" if score >= 75 else
            "good" if score >= 50 else
            "limited" if score >= 25 else
            "isolated"
        ),
        "attribution": "Data (c) OpenStreetMap contributors (ODbL)",
    }


def main() -> None:
    mcp.run(transport=os.getenv("MCP_TRANSPORT", "stdio"))


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
