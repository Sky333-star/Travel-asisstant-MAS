#!/usr/bin/env python
"""Wayfarer Flights MCP server.

DATA HONESTY STATEMENT
----------------------
Real-time airfare requires a credentialed API (Amadeus, Kiwi, Duffel) and is
therefore out of scope for this keyless project. This server is explicit about
the split:

* **Airports are real.** Codes, names, cities, coordinates, elevation and
  scheduled-service flags come from the **OurAirports** open dataset (public
  domain), fetched live and cached.
* **Routings are plausible, not published.** Non-stop feasibility is derived
  from great-circle distance and airport class; connections are routed through
  genuine hub airports that actually lie near the path.
* **Prices and times are modelled.** See :mod:`mcp_servers.common.pricing`.

Every itinerary is stamped ``"pricing": "estimated"`` and
``"data_basis": "modelled"``. The backend propagates those flags to the API,
and the UI renders an "estimate" badge, so a user is never shown a modelled
number that looks like a booked fare.

Tools
-----
``find_airports``    Nearest scheduled-service airports to a coordinate.
``resolve_airport``  City name or IATA code -> airport record.
``search_flights``   Modelled itinerary options for a city pair and date.
"""

from __future__ import annotations

import asyncio
import csv
import io
import os
import sys
from datetime import date, datetime, timedelta
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp_servers.common.geo_math import haversine_km, midpoint
from mcp_servers.common.http import HttpError, get_json, get_text
from mcp_servers.common.pricing import (
    estimate_flight_duration_minutes,
    estimate_flight_price_eur,
    stable_unit,
)
from mcp_servers.common.server_compat import create_server

OURAIRPORTS_URL = os.getenv(
    "OURAIRPORTS_URL",
    "https://davidmegginson.github.io/ourairports-data/airports.csv",
)
GEOCODE_URL = os.getenv(
    "OPEN_METEO_GEOCODE_URL", "https://geocoding-api.open-meteo.com/v1/search"
)

mcp = create_server("wayfarer-flights")

# Genuine global connecting hubs, used to route one-stop itineraries through
# airports that actually handle transfer traffic. (IATA, name, lat, lon, region)
HUBS: tuple[tuple[str, str, float, float, str], ...] = (
    ("LHR", "London Heathrow", 51.4700, -0.4543, "EU"),
    ("CDG", "Paris Charles de Gaulle", 49.0097, 2.5479, "EU"),
    ("AMS", "Amsterdam Schiphol", 52.3105, 4.7683, "EU"),
    ("FRA", "Frankfurt", 50.0379, 8.5622, "EU"),
    ("MUC", "Munich", 48.3538, 11.7861, "EU"),
    ("MAD", "Madrid Barajas", 40.4936, -3.5668, "EU"),
    ("IST", "Istanbul", 41.2753, 28.7519, "EU"),
    ("FCO", "Rome Fiumicino", 41.8003, 12.2389, "EU"),
    ("ZRH", "Zurich", 47.4647, 8.5492, "EU"),
    ("VIE", "Vienna", 48.1103, 16.5697, "EU"),
    ("CPH", "Copenhagen", 55.6180, 12.6560, "EU"),
    ("DXB", "Dubai", 25.2532, 55.3657, "ME"),
    ("DOH", "Doha Hamad", 25.2731, 51.6081, "ME"),
    ("JFK", "New York JFK", 40.6413, -73.7781, "NA"),
    ("ORD", "Chicago O'Hare", 41.9742, -87.9073, "NA"),
    ("DFW", "Dallas Fort Worth", 32.8998, -97.0403, "NA"),
    ("LAX", "Los Angeles", 33.9416, -118.4085, "NA"),
    ("ATL", "Atlanta", 33.6407, -84.4277, "NA"),
    ("YYZ", "Toronto Pearson", 43.6777, -79.6248, "NA"),
    ("MEX", "Mexico City", 19.4363, -99.0721, "NA"),
    ("PTY", "Panama City Tocumen", 9.0714, -79.3835, "SA"),
    ("GRU", "Sao Paulo Guarulhos", -23.4356, -46.4731, "SA"),
    ("BOG", "Bogota El Dorado", 4.7016, -74.1469, "SA"),
    ("LIM", "Lima Jorge Chavez", -12.0219, -77.1143, "SA"),
    ("SIN", "Singapore Changi", 1.3644, 103.9915, "AS"),
    ("HKG", "Hong Kong", 22.3080, 113.9185, "AS"),
    ("NRT", "Tokyo Narita", 35.7720, 140.3929, "AS"),
    ("ICN", "Seoul Incheon", 37.4602, 126.4407, "AS"),
    ("BKK", "Bangkok Suvarnabhumi", 13.6900, 100.7501, "AS"),
    ("KUL", "Kuala Lumpur", 2.7456, 101.7099, "AS"),
    ("DEL", "Delhi Indira Gandhi", 28.5562, 77.1000, "AS"),
    ("PEK", "Beijing Capital", 40.0799, 116.6031, "AS"),
    ("ADD", "Addis Ababa Bole", 8.9779, 38.7993, "AF"),
    ("CAI", "Cairo", 30.1219, 31.4056, "AF"),
    ("JNB", "Johannesburg O.R. Tambo", -26.1392, 28.2460, "AF"),
    ("NBO", "Nairobi Jomo Kenyatta", -1.3192, 36.9278, "AF"),
    ("SYD", "Sydney Kingsford Smith", -33.9399, 151.1753, "OC"),
    ("AKL", "Auckland", -37.0082, 174.7850, "OC"),
)

# Plausible carrier pools by hub region, so a Bangkok connection is not
# operated by a Scandinavian regional airline.
CARRIERS: dict[str, tuple[tuple[str, str], ...]] = {
    "EU": (("LH", "Lufthansa"), ("KL", "KLM"), ("AF", "Air France"), ("BA", "British Airways"),
           ("IB", "Iberia"), ("AZ", "ITA Airways"), ("LX", "SWISS"), ("OS", "Austrian"),
           ("SK", "SAS"), ("TP", "TAP Air Portugal"), ("FR", "Ryanair"), ("U2", "easyJet"),
           ("TK", "Turkish Airlines"), ("W6", "Wizz Air")),
    "ME": (("EK", "Emirates"), ("QR", "Qatar Airways"), ("EY", "Etihad"), ("GF", "Gulf Air")),
    "NA": (("AA", "American Airlines"), ("DL", "Delta"), ("UA", "United"),
           ("AC", "Air Canada"), ("B6", "JetBlue"), ("AM", "Aeromexico")),
    "SA": (("LA", "LATAM"), ("AV", "Avianca"), ("CM", "Copa Airlines"), ("AR", "Aerolineas")),
    "AS": (("SQ", "Singapore Airlines"), ("CX", "Cathay Pacific"), ("NH", "ANA"),
           ("JL", "Japan Airlines"), ("KE", "Korean Air"), ("TG", "Thai Airways"),
           ("MH", "Malaysia Airlines"), ("AI", "Air India"), ("VN", "Vietnam Airlines")),
    "AF": (("ET", "Ethiopian Airlines"), ("MS", "EgyptAir"), ("SA", "South African"),
           ("KQ", "Kenya Airways"), ("AT", "Royal Air Maroc")),
    "OC": (("QF", "Qantas"), ("NZ", "Air New Zealand"), ("VA", "Virgin Australia")),
}

_airports_cache: dict[str, dict[str, Any]] | None = None
_airports_lock = asyncio.Lock()


# --------------------------------------------------------------------------
# OurAirports dataset
# --------------------------------------------------------------------------


async def _load_airports() -> dict[str, dict[str, Any]]:
    """Download and parse the OurAirports dataset, keyed by IATA code.

    The raw CSV is ~80k rows; we keep only large/medium airports that have an
    IATA code and scheduled service, which reduces it to roughly 3,000 usable
    entries. The parsed result is memoised for the process lifetime and the raw
    CSV is disk-cached for a week (the dataset changes slowly).
    """
    global _airports_cache
    if _airports_cache is not None:
        return _airports_cache

    async with _airports_lock:
        if _airports_cache is not None:
            return _airports_cache

        raw = await get_text(OURAIRPORTS_URL, cache_ttl=604800)
        airports: dict[str, dict[str, Any]] = {}

        reader = csv.DictReader(io.StringIO(raw))
        for row in reader:
            iata = (row.get("iata_code") or "").strip().upper()
            kind = (row.get("type") or "").strip()
            if len(iata) != 3 or kind not in ("large_airport", "medium_airport"):
                continue
            if (row.get("scheduled_service") or "").strip() != "yes":
                continue
            try:
                lat = float(row["latitude_deg"])
                lon = float(row["longitude_deg"])
            except (KeyError, TypeError, ValueError):
                continue

            airports[iata] = {
                "iata": iata,
                "icao": (row.get("icao_code") or row.get("ident") or "").strip().upper() or None,
                "name": (row.get("name") or "").strip(),
                "municipality": (row.get("municipality") or "").strip() or None,
                "country_code": (row.get("iso_country") or "").strip().upper() or None,
                "region_code": (row.get("iso_region") or "").strip() or None,
                "type": kind,
                "latitude": lat,
                "longitude": lon,
                "elevation_ft": (
                    int(float(row["elevation_ft"])) if row.get("elevation_ft") else None
                ),
            }

        _airports_cache = airports
        return airports


def _carrier_for(region: str, seed: str) -> tuple[str, str]:
    pool = CARRIERS.get(region) or CARRIERS["EU"]
    return pool[int(stable_unit("carrier", region, seed) * len(pool)) % len(pool)]


def _region_of(country_code: str | None) -> str:
    """Very coarse continent bucket, used only to pick a plausible carrier."""
    cc = (country_code or "").upper()
    if cc in {"US", "CA", "MX", "PR", "BS", "CU", "DO", "JM", "CR", "PA", "GT"}:
        return "NA"
    if cc in {"BR", "AR", "CL", "PE", "CO", "EC", "BO", "UY", "PY", "VE"}:
        return "SA"
    if cc in {"AE", "QA", "SA", "OM", "KW", "BH", "JO", "IL", "LB", "IQ", "IR"}:
        return "ME"
    if cc in {"CN", "JP", "KR", "TH", "VN", "SG", "MY", "ID", "PH", "IN", "LK",
              "NP", "BD", "PK", "KH", "LA", "MM", "TW", "HK", "MO", "MN", "KZ",
              "UZ", "GE", "AM", "AZ"}:
        return "AS"
    if cc in {"ZA", "KE", "TZ", "EG", "MA", "TN", "ET", "NG", "GH", "SN", "UG",
              "RW", "ZW", "ZM", "BW", "NA", "MU", "SC", "DZ"}:
        return "AF"
    if cc in {"AU", "NZ", "FJ", "PG", "NC", "PF", "VU", "WS", "TO"}:
        return "OC"
    return "EU"


# --------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------


@mcp.tool()
async def find_airports(
    latitude: float,
    longitude: float,
    radius_km: float = 150.0,
    limit: int = 5,
    large_only: bool = False,
) -> dict[str, Any]:
    """Nearest airports with scheduled service to a coordinate.

    Args:
        latitude: Centre latitude.
        longitude: Centre longitude.
        radius_km: Search radius in km (10-800).
        limit: Maximum airports to return (1-15).
        large_only: Restrict to major (large_airport) facilities.
    """
    radius_km = max(10.0, min(800.0, float(radius_km)))
    limit = max(1, min(15, int(limit)))

    try:
        airports = await _load_airports()
    except HttpError as exc:
        return {"airports": [], "error": f"Could not load airport dataset: {exc}"}

    found: list[dict[str, Any]] = []
    for airport in airports.values():
        if large_only and airport["type"] != "large_airport":
            continue
        distance = haversine_km(
            latitude, longitude, airport["latitude"], airport["longitude"]
        )
        if distance <= radius_km:
            record = dict(airport)
            record["distance_km"] = round(distance, 1)
            found.append(record)

    # Prefer larger airports at similar distance: a major hub 90 km away is
    # usually a better departure point than a regional field 40 km away.
    found.sort(
        key=lambda a: a["distance_km"] * (0.72 if a["type"] == "large_airport" else 1.0)
    )
    return {
        "airports": found[:limit],
        "searched_radius_km": radius_km,
        "attribution": "OurAirports open data (public domain)",
    }


@mcp.tool()
async def resolve_airport(query: str) -> dict[str, Any]:
    """Resolve a city name or IATA code to a specific airport.

    Accepts "LIS", "Lisbon" or "Lisbon, Portugal". City names are geocoded
    first and then matched to the nearest major airport, which is how a user
    saying "fly me from Berlin" becomes BER.
    """
    query = (query or "").strip()
    if not query:
        return {"found": False, "error": "Empty query."}

    try:
        airports = await _load_airports()
    except HttpError as exc:
        return {"found": False, "error": f"Could not load airport dataset: {exc}"}

    # 1. Direct IATA hit.
    upper = query.upper()
    if len(upper) == 3 and upper in airports:
        return {"found": True, "match_type": "iata", "airport": airports[upper]}

    # 2. Exact municipality match, preferring the largest airport there.
    needle = query.split(",")[0].strip().lower()
    municipal = [
        a for a in airports.values() if (a["municipality"] or "").lower() == needle
    ]
    if municipal:
        municipal.sort(key=lambda a: 0 if a["type"] == "large_airport" else 1)
        return {
            "found": True,
            "match_type": "municipality",
            "airport": municipal[0],
            "alternatives": municipal[1:4],
        }

    # 3. Geocode the query, then take the nearest sizeable airport.
    try:
        geo = await get_json(
            GEOCODE_URL, {"name": needle, "count": 1, "format": "json"}, cache_ttl=86400
        )
    except HttpError as exc:
        return {"found": False, "error": str(exc)}

    hits = geo.get("results") or []
    if not hits:
        return {"found": False, "query": query}

    nearby = await find_airports(
        hits[0]["latitude"], hits[0]["longitude"], radius_km=250, limit=4
    )
    options = nearby.get("airports") or []
    if not options:
        return {"found": False, "query": query, "reason": "No airport within 250 km."}

    return {
        "found": True,
        "match_type": "geocoded",
        "resolved_place": {
            "name": hits[0].get("name"),
            "country": hits[0].get("country"),
            "latitude": hits[0]["latitude"],
            "longitude": hits[0]["longitude"],
        },
        "airport": options[0],
        "alternatives": options[1:],
    }


def _pick_hub(
    o_lat: float, o_lon: float, d_lat: float, d_lon: float, direct_km: float, seed: str
) -> tuple[str, str, float, float, str] | None:
    """Choose a connecting hub that genuinely lies near the great-circle path.

    A hub qualifies when routing through it adds less than 35% to the direct
    distance and it is not implausibly close to either endpoint. Among the
    qualifying hubs we pick deterministically so the same query always yields
    the same connection.
    """
    mid_lat, mid_lon = midpoint(o_lat, o_lon, d_lat, d_lon)
    scored: list[tuple[float, tuple[str, str, float, float, str]]] = []

    for hub in HUBS:
        _, _, h_lat, h_lon, _ = hub
        leg1 = haversine_km(o_lat, o_lon, h_lat, h_lon)
        leg2 = haversine_km(h_lat, h_lon, d_lat, d_lon)
        if leg1 < 120 or leg2 < 120:
            continue  # Hub is effectively the origin or destination.
        detour = (leg1 + leg2) / max(1.0, direct_km)
        if detour > 1.35:
            continue
        # Prefer hubs near the midpoint, which is what real routings do.
        offset = haversine_km(mid_lat, mid_lon, h_lat, h_lon)
        scored.append((detour * 1000 + offset, hub))

    if not scored:
        return None
    scored.sort(key=lambda pair: pair[0])
    shortlist = [hub for _, hub in scored[:4]]
    return shortlist[int(stable_unit("hub", seed) * len(shortlist)) % len(shortlist)]


def _leg(
    frm: dict[str, Any],
    to: dict[str, Any],
    depart: datetime,
    carrier: tuple[str, str],
    flight_seed: str,
) -> dict[str, Any]:
    distance = haversine_km(
        frm["latitude"], frm["longitude"], to["latitude"], to["longitude"]
    )
    minutes = estimate_flight_duration_minutes(distance, stops=0)
    arrive = depart + timedelta(minutes=minutes)
    number = 100 + int(stable_unit("num", flight_seed) * 8800)
    return {
        "from": {"iata": frm["iata"], "name": frm["name"], "city": frm["municipality"]},
        "to": {"iata": to["iata"], "name": to["name"], "city": to["municipality"]},
        "carrier_code": carrier[0],
        "carrier_name": carrier[1],
        "flight_number": f"{carrier[0]}{number}",
        "depart_local": depart.strftime("%Y-%m-%dT%H:%M"),
        "arrive_local": arrive.strftime("%Y-%m-%dT%H:%M"),
        "duration_minutes": minutes,
        "distance_km": round(distance, 1),
    }


async def _build_itineraries(
    origin: dict[str, Any],
    destination: dict[str, Any],
    depart_on: date,
    passengers: int,
    cabin: str,
) -> list[dict[str, Any]]:
    direct_km = haversine_km(
        origin["latitude"], origin["longitude"],
        destination["latitude"], destination["longitude"],
    )
    days_ahead = max(0, (depart_on - date.today()).days)
    seed_base = f"{origin['iata']}-{destination['iata']}-{depart_on.isoformat()}"

    # Non-stop feasibility: short routes are almost always flown non-stop;
    # very long routes need two large airports; beyond ~14,000 km nothing
    # flies non-stop commercially.
    nonstop_possible = (
        direct_km < 2_200
        or (direct_km < 14_000
            and origin["type"] == "large_airport"
            and destination["type"] == "large_airport")
    )

    itineraries: list[dict[str, Any]] = []

    if nonstop_possible:
        for slot, hour in enumerate((7, 13, 19)):
            seed = f"{seed_base}-ns{slot}"
            carrier = _carrier_for(_region_of(origin["country_code"]), seed)
            depart_dt = datetime.combine(depart_on, datetime.min.time()).replace(
                hour=hour, minute=int(stable_unit("min", seed) * 12) * 5
            )
            leg = _leg(origin, destination, depart_dt, carrier, seed)
            price = estimate_flight_price_eur(
                direct_km, depart_on, days_ahead,
                stops=0, cabin=cabin, carrier_code=carrier[0],
                seed_parts=(seed,),
            )
            itineraries.append(
                {
                    "id": f"ITIN-{origin['iata']}{destination['iata']}-NS{slot + 1}",
                    "stops": 0,
                    "legs": [leg],
                    "total_duration_minutes": leg["duration_minutes"],
                    "price_per_person_eur": price,
                    "price_total_eur": round(price * passengers, 2),
                    "cabin": cabin,
                    "passengers": passengers,
                    "pricing": "estimated",
                    "data_basis": "modelled",
                }
            )

    # Always offer at least one connection: it is often the cheaper option and
    # it is the only option on routes with no non-stop service.
    try:
        airports = await _load_airports()
    except HttpError:
        airports = {}

    hub = _pick_hub(
        origin["latitude"], origin["longitude"],
        destination["latitude"], destination["longitude"],
        direct_km, seed_base,
    )
    if hub and hub[0] in airports and hub[0] not in (origin["iata"], destination["iata"]):
        hub_airport = airports[hub[0]]
        seed = f"{seed_base}-1s"
        carrier = _carrier_for(hub[4], seed)
        depart_dt = datetime.combine(depart_on, datetime.min.time()).replace(
            hour=6 + int(stable_unit("h", seed) * 8), minute=int(stable_unit("m", seed) * 12) * 5
        )
        leg1 = _leg(origin, hub_airport, depart_dt, carrier, seed + "a")
        # A realistic connection window: 75-140 minutes on the ground.
        layover = 75 + int(stable_unit("lay", seed) * 65)
        leg2_depart = datetime.strptime(leg1["arrive_local"], "%Y-%m-%dT%H:%M") + timedelta(
            minutes=layover
        )
        leg2 = _leg(hub_airport, destination, leg2_depart, carrier, seed + "b")
        total_km = leg1["distance_km"] + leg2["distance_km"]
        price = estimate_flight_price_eur(
            total_km, depart_on, days_ahead,
            stops=1, cabin=cabin, carrier_code=carrier[0], seed_parts=(seed,),
        )
        itineraries.append(
            {
                "id": f"ITIN-{origin['iata']}{destination['iata']}-1S",
                "stops": 1,
                "legs": [leg1, leg2],
                "layovers": [
                    {
                        "airport": hub_airport["iata"],
                        "city": hub_airport["municipality"],
                        "minutes": layover,
                    }
                ],
                "total_duration_minutes": (
                    leg1["duration_minutes"] + layover + leg2["duration_minutes"]
                ),
                "price_per_person_eur": price,
                "price_total_eur": round(price * passengers, 2),
                "cabin": cabin,
                "passengers": passengers,
                "pricing": "estimated",
                "data_basis": "modelled",
            }
        )

    itineraries.sort(key=lambda i: i["price_per_person_eur"])
    return itineraries


@mcp.tool()
async def search_flights(
    origin: str,
    destination: str,
    depart_date: str,
    return_date: str | None = None,
    passengers: int = 1,
    cabin: str = "economy",
    max_results: int = 4,
) -> dict[str, Any]:
    """Modelled flight options between two places.

    IMPORTANT: prices and schedules are ESTIMATES produced by a documented
    cost model, not live inventory. Airports and routings are grounded in the
    OurAirports open dataset. Every itinerary carries ``pricing: "estimated"``.

    Args:
        origin: City name or IATA code to depart from.
        destination: City name or IATA code to fly to.
        depart_date: Outbound date, ISO YYYY-MM-DD.
        return_date: Optional inbound date for a round trip.
        passengers: Number of travellers (1-9).
        cabin: economy | premium_economy | business | first.
        max_results: Max itineraries per direction (1-6).
    """
    passengers = max(1, min(9, int(passengers)))
    max_results = max(1, min(6, int(max_results)))
    cabin = cabin if cabin in ("economy", "premium_economy", "business", "first") else "economy"

    try:
        out_date = datetime.strptime(depart_date[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return {"error": f"Invalid depart_date: {depart_date!r}. Use YYYY-MM-DD."}

    back_date: date | None = None
    if return_date:
        try:
            back_date = datetime.strptime(return_date[:10], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return {"error": f"Invalid return_date: {return_date!r}. Use YYYY-MM-DD."}
        if back_date < out_date:
            return {"error": "return_date must not precede depart_date."}

    origin_res = await resolve_airport(origin)
    dest_res = await resolve_airport(destination)
    if not origin_res.get("found"):
        return {"error": f"Could not resolve origin {origin!r}."}
    if not dest_res.get("found"):
        return {"error": f"Could not resolve destination {destination!r}."}

    origin_ap = origin_res["airport"]
    dest_ap = dest_res["airport"]

    if origin_ap["iata"] == dest_ap["iata"]:
        return {
            "error": (
                f"Origin and destination resolve to the same airport "
                f"({origin_ap['iata']}). A flight is not needed."
            )
        }

    outbound = await _build_itineraries(origin_ap, dest_ap, out_date, passengers, cabin)
    inbound: list[dict[str, Any]] = []
    if back_date:
        inbound = await _build_itineraries(dest_ap, origin_ap, back_date, passengers, cabin)

    cheapest_out = outbound[0]["price_total_eur"] if outbound else None
    cheapest_in = inbound[0]["price_total_eur"] if inbound else None

    return {
        "origin_airport": origin_ap,
        "destination_airport": dest_ap,
        "direct_distance_km": round(
            haversine_km(
                origin_ap["latitude"], origin_ap["longitude"],
                dest_ap["latitude"], dest_ap["longitude"],
            ),
            1,
        ),
        "depart_date": out_date.isoformat(),
        "return_date": back_date.isoformat() if back_date else None,
        "passengers": passengers,
        "cabin": cabin,
        "currency": "EUR",
        "outbound": outbound[:max_results],
        "inbound": inbound[:max_results],
        "cheapest_round_trip_eur": (
            round(cheapest_out + cheapest_in, 2)
            if cheapest_out is not None and cheapest_in is not None
            else cheapest_out
        ),
        "pricing": "estimated",
        "data_basis": "modelled",
        "disclaimer": (
            "Airports and routings use the OurAirports open dataset. Fares and "
            "schedules are MODELLED estimates -- no keyless real-time airfare "
            "API exists. Verify on an airline or OTA site before booking."
        ),
    }


def main() -> None:
    mcp.run(transport=os.getenv("MCP_TRANSPORT", "stdio"))


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
