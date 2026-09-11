#!/usr/bin/env python
"""Wayfarer Weather MCP server.

Exposes weather intelligence over the Model Context Protocol, backed entirely
by **Open-Meteo** -- a free, keyless, open-data service (CC-BY 4.0).

Tools
-----
``get_forecast``          16-day daily + hourly forecast for a coordinate.
``get_climate_normals``   Historical month averages from the ERA5 reanalysis
                          archive, for trips beyond the forecast horizon.
``get_air_quality``       European AQI, PM2.5/PM10 and pollen.
``score_destination_weather``  Turns raw weather into a 0-100 comfort score
                          plus human-readable reasons -- this is the tool the
                          Weather Analyst agent actually reasons over.

Transport: stdio (default) so the backend can spawn it as a subprocess with
zero configuration.
"""

from __future__ import annotations

import asyncio
import os
import statistics
import sys
from datetime import date, datetime, timedelta
from typing import Any

# Allow `python mcp_servers/weather_server.py` from the repository root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp_servers.common.http import HttpError, get_json
from mcp_servers.common.server_compat import create_server

FORECAST_URL = os.getenv("OPEN_METEO_FORECAST_URL", "https://api.open-meteo.com/v1/forecast")
ARCHIVE_URL = os.getenv(
    "OPEN_METEO_ARCHIVE_URL", "https://archive-api.open-meteo.com/v1/archive"
)
AIR_URL = os.getenv(
    "OPEN_METEO_AIR_URL", "https://air-quality-api.open-meteo.com/v1/air-quality"
)

mcp = create_server("wayfarer-weather")

# Open-Meteo publishes forecasts up to 16 days out. Beyond that we fall back
# to climate normals, and we say so in the response.
FORECAST_HORIZON_DAYS = 16

# WMO weather interpretation codes -> (label, severity 0-3).
WMO_CODES: dict[int, tuple[str, int]] = {
    0: ("Clear sky", 0), 1: ("Mainly clear", 0), 2: ("Partly cloudy", 0),
    3: ("Overcast", 1), 45: ("Fog", 1), 48: ("Depositing rime fog", 1),
    51: ("Light drizzle", 1), 53: ("Moderate drizzle", 1), 55: ("Dense drizzle", 2),
    56: ("Freezing drizzle", 2), 57: ("Dense freezing drizzle", 3),
    61: ("Slight rain", 1), 63: ("Moderate rain", 2), 65: ("Heavy rain", 3),
    66: ("Freezing rain", 3), 67: ("Heavy freezing rain", 3),
    71: ("Slight snow", 1), 73: ("Moderate snow", 2), 75: ("Heavy snow", 3),
    77: ("Snow grains", 1), 80: ("Slight rain showers", 1),
    81: ("Moderate rain showers", 2), 82: ("Violent rain showers", 3),
    85: ("Slight snow showers", 1), 86: ("Heavy snow showers", 3),
    95: ("Thunderstorm", 3), 96: ("Thunderstorm with hail", 3),
    99: ("Thunderstorm with heavy hail", 3),
}


def _describe(code: int | None) -> dict[str, Any]:
    label, severity = WMO_CODES.get(int(code or 0), ("Unknown", 1))
    return {"code": code, "label": label, "severity": severity}


def _parse_date(value: str | None, fallback: date) -> date:
    if not value:
        return fallback
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    except ValueError:
        return fallback


# --------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------


@mcp.tool()
async def get_forecast(
    latitude: float,
    longitude: float,
    start_date: str | None = None,
    end_date: str | None = None,
    timezone: str = "auto",
) -> dict[str, Any]:
    """Daily weather forecast for a coordinate (Open-Meteo, no key required).

    Args:
        latitude: WGS84 latitude, -90 to 90.
        longitude: WGS84 longitude, -180 to 180.
        start_date: ISO date (YYYY-MM-DD). Defaults to today.
        end_date: ISO date. Defaults to 7 days after ``start_date``.
        timezone: IANA timezone or "auto" to infer from the coordinate.

    Returns a dict with ``days`` (per-day conditions), ``summary`` aggregates
    and ``coverage`` describing how much of the requested range the forecast
    horizon actually covers.
    """
    today = date.today()
    start = _parse_date(start_date, today)
    end = _parse_date(end_date, start + timedelta(days=7))
    if end < start:
        start, end = end, start

    horizon = today + timedelta(days=FORECAST_HORIZON_DAYS)
    clipped_start = max(start, today)
    clipped_end = min(end, horizon)

    if clipped_start > clipped_end:
        return {
            "source": "open-meteo",
            "covered": False,
            "reason": (
                f"Requested window starts {(start - today).days} days out, beyond the "
                f"{FORECAST_HORIZON_DAYS}-day forecast horizon. Use get_climate_normals."
            ),
            "days": [],
        }

    params = {
        "latitude": round(latitude, 4),
        "longitude": round(longitude, 4),
        "daily": ",".join(
            [
                "weather_code",
                "temperature_2m_max",
                "temperature_2m_min",
                "apparent_temperature_max",
                "apparent_temperature_min",
                "precipitation_sum",
                "precipitation_probability_max",
                "wind_speed_10m_max",
                "uv_index_max",
                "sunshine_duration",
                "daylight_duration",
            ]
        ),
        "timezone": timezone,
        "start_date": clipped_start.isoformat(),
        "end_date": clipped_end.isoformat(),
    }

    try:
        payload = await get_json(FORECAST_URL, params, cache_ttl=1800)
    except HttpError as exc:
        return {"source": "open-meteo", "covered": False, "error": str(exc), "days": []}

    daily = payload.get("daily") or {}
    times: list[str] = daily.get("time") or []

    def col(name: str) -> list[Any]:
        values = daily.get(name) or []
        return list(values) + [None] * (len(times) - len(values))

    days = []
    for i, day in enumerate(times):
        days.append(
            {
                "date": day,
                "condition": _describe(col("weather_code")[i]),
                "temp_max_c": col("temperature_2m_max")[i],
                "temp_min_c": col("temperature_2m_min")[i],
                "feels_like_max_c": col("apparent_temperature_max")[i],
                "feels_like_min_c": col("apparent_temperature_min")[i],
                "precipitation_mm": col("precipitation_sum")[i],
                "precipitation_probability_pct": col("precipitation_probability_max")[i],
                "wind_max_kmh": col("wind_speed_10m_max")[i],
                "uv_index_max": col("uv_index_max")[i],
                "sunshine_hours": round((col("sunshine_duration")[i] or 0) / 3600.0, 1),
                "daylight_hours": round((col("daylight_duration")[i] or 0) / 3600.0, 1),
            }
        )

    highs = [d["temp_max_c"] for d in days if d["temp_max_c"] is not None]
    lows = [d["temp_min_c"] for d in days if d["temp_min_c"] is not None]
    rain = [d["precipitation_mm"] or 0 for d in days]

    return {
        "source": "open-meteo",
        "license": "CC-BY 4.0 Open-Meteo",
        "covered": True,
        "coverage": {
            "requested": {"start": start.isoformat(), "end": end.isoformat()},
            "returned": {"start": clipped_start.isoformat(), "end": clipped_end.isoformat()},
            "full_range_available": clipped_start == start and clipped_end == end,
        },
        "timezone": payload.get("timezone"),
        "elevation_m": payload.get("elevation"),
        "days": days,
        "summary": {
            "avg_high_c": round(statistics.fmean(highs), 1) if highs else None,
            "avg_low_c": round(statistics.fmean(lows), 1) if lows else None,
            "total_precipitation_mm": round(sum(rain), 1),
            "rainy_days": sum(1 for v in rain if (v or 0) >= 1.0),
            "worst_condition": max(
                (d["condition"] for d in days),
                key=lambda c: c["severity"],
                default=_describe(0),
            ),
        },
    }


@mcp.tool()
async def get_climate_normals(
    latitude: float,
    longitude: float,
    month: int,
    years_back: int = 5,
) -> dict[str, Any]:
    """Historical climate averages for a month, from the ERA5 reanalysis archive.

    Used when a trip is further out than the 16-day forecast horizon, so the
    system can still reason about "is October good for Lisbon?" without ever
    pretending to have a real forecast.

    Args:
        latitude: WGS84 latitude.
        longitude: WGS84 longitude.
        month: Calendar month, 1-12.
        years_back: How many past years to average (1-10).
    """
    month = max(1, min(12, int(month)))
    years_back = max(1, min(10, int(years_back)))

    # The ERA5 archive lags real time by ~5 days; step back a year to be safe.
    latest_year = date.today().year - 1
    highs: list[float] = []
    lows: list[float] = []
    precip: list[float] = []
    wet_days: list[int] = []

    for year in range(latest_year - years_back + 1, latest_year + 1):
        start = date(year, month, 1)
        end = date(year + (month == 12), (month % 12) + 1, 1) - timedelta(days=1)
        params = {
            "latitude": round(latitude, 3),
            "longitude": round(longitude, 3),
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum",
            "timezone": "auto",
        }
        try:
            # Historical data never changes -> cache it for a week.
            payload = await get_json(ARCHIVE_URL, params, cache_ttl=604800)
        except HttpError:
            continue

        daily = payload.get("daily") or {}
        year_highs = [v for v in (daily.get("temperature_2m_max") or []) if v is not None]
        year_lows = [v for v in (daily.get("temperature_2m_min") or []) if v is not None]
        year_precip = [v or 0.0 for v in (daily.get("precipitation_sum") or [])]
        if year_highs:
            highs.append(statistics.fmean(year_highs))
        if year_lows:
            lows.append(statistics.fmean(year_lows))
        if year_precip:
            precip.append(sum(year_precip))
            wet_days.append(sum(1 for v in year_precip if v >= 1.0))

    if not highs:
        return {
            "source": "open-meteo-archive",
            "available": False,
            "reason": "No archive data returned for this location.",
        }

    return {
        "source": "open-meteo-archive",
        "license": "CC-BY 4.0 Open-Meteo / ERA5",
        "available": True,
        "basis": f"{len(highs)}-year average ({latest_year - len(highs) + 1}-{latest_year})",
        "month": month,
        "avg_high_c": round(statistics.fmean(highs), 1),
        "avg_low_c": round(statistics.fmean(lows), 1) if lows else None,
        "avg_monthly_precipitation_mm": round(statistics.fmean(precip), 1) if precip else None,
        "avg_wet_days": round(statistics.fmean(wet_days), 1) if wet_days else None,
    }


@mcp.tool()
async def get_air_quality(latitude: float, longitude: float) -> dict[str, Any]:
    """Current air quality and pollen for a coordinate.

    Relevant for travellers with asthma or allergies, which the Query Analyst
    picks up from phrases like "I have asthma" and passes into planning.
    """
    params = {
        "latitude": round(latitude, 3),
        "longitude": round(longitude, 3),
        "current": "european_aqi,pm2_5,pm10,ozone,alder_pollen,birch_pollen,grass_pollen",
        "timezone": "auto",
    }
    try:
        payload = await get_json(AIR_URL, params, cache_ttl=3600)
    except HttpError as exc:
        return {"source": "open-meteo-air-quality", "available": False, "error": str(exc)}

    current = payload.get("current") or {}
    aqi = current.get("european_aqi")

    def band(value: float | None) -> str:
        if value is None:
            return "unknown"
        for threshold, name in ((20, "good"), (40, "fair"), (60, "moderate"),
                                (80, "poor"), (100, "very poor")):
            if value <= threshold:
                return name
        return "extremely poor"

    return {
        "source": "open-meteo-air-quality",
        "available": True,
        "european_aqi": aqi,
        "aqi_band": band(aqi),
        "pm2_5": current.get("pm2_5"),
        "pm10": current.get("pm10"),
        "ozone": current.get("ozone"),
        "pollen": {
            "alder": current.get("alder_pollen"),
            "birch": current.get("birch_pollen"),
            "grass": current.get("grass_pollen"),
        },
    }


@mcp.tool()
async def score_destination_weather(
    latitude: float,
    longitude: float,
    start_date: str,
    end_date: str,
    preference: str = "mild",
) -> dict[str, Any]:
    """Score how well a destination's weather fits a trip window (0-100).

    This is the primary tool the Weather Analyst agent calls. It automatically
    uses the live forecast when the window is within the 16-day horizon and
    falls back to climate normals otherwise, always reporting which basis was
    used so the agent never overstates its confidence.

    Args:
        latitude: WGS84 latitude.
        longitude: WGS84 longitude.
        start_date: Trip start, ISO date.
        end_date: Trip end, ISO date.
        preference: One of ``warm``, ``mild``, ``cool``, ``snow``, ``dry``.
    """
    today = date.today()
    start = _parse_date(start_date, today)
    end = _parse_date(end_date, start + timedelta(days=5))

    forecast = await get_forecast(latitude, longitude, start.isoformat(), end.isoformat())
    basis = "forecast"
    days = forecast.get("days") or []

    if not days:
        normals = await get_climate_normals(latitude, longitude, start.month)
        if not normals.get("available"):
            return {
                "score": None,
                "basis": "unavailable",
                "reasons": ["No weather data available for this location."],
            }
        basis = "climate_normals"
        avg_high = normals["avg_high_c"]
        avg_low = normals.get("avg_low_c") or avg_high - 8
        wet_days = normals.get("avg_wet_days") or 0
        span_days = max(1, (end - start).days + 1)
        # Project monthly normals onto the trip length.
        rainy_days = wet_days * span_days / 30.0
        precip_total = (normals.get("avg_monthly_precipitation_mm") or 0) * span_days / 30.0
        uv_avg = None
        worst_severity = 1 if wet_days > 12 else 0
    else:
        highs = [d["temp_max_c"] for d in days if d["temp_max_c"] is not None]
        lows = [d["temp_min_c"] for d in days if d["temp_min_c"] is not None]
        avg_high = statistics.fmean(highs) if highs else 18.0
        avg_low = statistics.fmean(lows) if lows else avg_high - 8
        rainy_days = forecast["summary"]["rainy_days"]
        precip_total = forecast["summary"]["total_precipitation_mm"]
        uvs = [d["uv_index_max"] for d in days if d["uv_index_max"] is not None]
        uv_avg = statistics.fmean(uvs) if uvs else None
        worst_severity = forecast["summary"]["worst_condition"]["severity"]
        span_days = len(days)

    # ---- Comfort scoring -------------------------------------------------
    # Each preference defines an ideal daytime-high band. Score decays
    # quadratically outside it, which punishes a 40C heatwave far harder than
    # a 2C miss.
    ideal_bands = {
        "warm": (24.0, 31.0),
        "mild": (17.0, 26.0),
        "cool": (8.0, 18.0),
        "snow": (-8.0, 2.0),
        "dry": (15.0, 30.0),
    }
    low_ideal, high_ideal = ideal_bands.get(preference, ideal_bands["mild"])

    if low_ideal <= avg_high <= high_ideal:
        temp_score = 100.0
    else:
        miss = low_ideal - avg_high if avg_high < low_ideal else avg_high - high_ideal
        temp_score = max(0.0, 100.0 - 3.2 * miss * miss ** 0.5)

    rain_fraction = rainy_days / max(1, span_days)
    rain_score = max(0.0, 100.0 - rain_fraction * 130.0 - min(40.0, precip_total * 0.55))
    if preference == "dry":
        rain_score = max(0.0, rain_score - 10.0 * rain_fraction * 5)

    severity_penalty = {0: 0.0, 1: 6.0, 2: 18.0, 3: 34.0}.get(worst_severity, 10.0)

    score = 0.58 * temp_score + 0.42 * rain_score - severity_penalty
    if basis == "climate_normals":
        # Historical averages are a weaker signal; pull scores toward neutral.
        score = 50.0 + (score - 50.0) * 0.85
    score = round(max(0.0, min(100.0, score)), 1)

    # ---- Human-readable reasoning ---------------------------------------
    reasons: list[str] = []
    reasons.append(
        f"Average daytime high of {avg_high:.0f}C"
        + (
            f", inside the {low_ideal:.0f}-{high_ideal:.0f}C band you asked for."
            if low_ideal <= avg_high <= high_ideal
            else f", outside the {low_ideal:.0f}-{high_ideal:.0f}C band you asked for."
        )
    )
    if rainy_days >= 1:
        reasons.append(
            f"{rainy_days:.0f} of {span_days} days show measurable rain "
            f"({precip_total:.0f} mm total)."
        )
    else:
        reasons.append("Essentially no rain expected in the window.")
    if uv_avg and uv_avg >= 8:
        reasons.append(f"High UV index ({uv_avg:.0f}) - sun protection needed.")
    if avg_low is not None and avg_low <= 3:
        reasons.append(f"Nights drop to about {avg_low:.0f}C - pack warm layers.")
    if worst_severity >= 3:
        reasons.append("At least one day carries a severe-weather code.")
    if basis == "climate_normals":
        reasons.append(
            "Trip is beyond the 16-day forecast horizon; scored from multi-year "
            "historical averages rather than a live forecast."
        )

    return {
        "score": score,
        "basis": basis,
        "preference": preference,
        "avg_high_c": round(avg_high, 1),
        "avg_low_c": round(avg_low, 1) if avg_low is not None else None,
        "rainy_days": round(rainy_days, 1),
        "precipitation_mm": round(precip_total, 1),
        "components": {
            "temperature_score": round(temp_score, 1),
            "rain_score": round(rain_score, 1),
            "severity_penalty": severity_penalty,
        },
        "reasons": reasons,
    }


def main() -> None:
    mcp.run(transport=os.getenv("MCP_TRANSPORT", "stdio"))


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
