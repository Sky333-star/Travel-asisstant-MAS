"""Declarative registry of the MCP servers this backend speaks to.

Keeping the server list as data (rather than scattered spawn calls) means the
set of capabilities is inspectable at runtime -- ``/health`` reports it, the
trace panel labels tool calls with it, and enabling or disabling a server is a
one-line ``.env`` change.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

from ..config import REPO_ROOT, settings


@dataclass(frozen=True)
class MCPServerSpec:
    """How to launch one MCP server and what it is for."""

    name: str
    script: str
    description: str
    tools: tuple[str, ...] = field(default_factory=tuple)
    critical: bool = False  # If true, startup failure degrades /health.

    @property
    def script_path(self) -> Path:
        return REPO_ROOT / "mcp_servers" / self.script

    def command(self) -> tuple[str, list[str]]:
        """Return ``(executable, args)`` for spawning this server over stdio.

        ``sys.executable`` guarantees the child runs in the same interpreter
        and virtualenv as the backend, which is the difference between "works
        on my machine" and "works".
        """
        return sys.executable, [str(self.script_path)]


SERVERS: dict[str, MCPServerSpec] = {
    "weather": MCPServerSpec(
        name="weather",
        script="weather_server.py",
        description="Open-Meteo forecasts, climate normals, air quality, comfort scoring.",
        tools=(
            "get_forecast",
            "get_climate_normals",
            "get_air_quality",
            "score_destination_weather",
        ),
        critical=True,
    ),
    "geo": MCPServerSpec(
        name="geo",
        script="geo_server.py",
        description="OSM Nominatim geocoding, Overpass POIs, destination catalogue, bundled country facts.",
        tools=(
            "geocode_place",
            "reverse_geocode",
            "suggest_destinations",
            "lookup_destination",
            "find_points_of_interest",
            "get_country_info",
            "bounding_box",
        ),
        critical=True,
    ),
    "flights": MCPServerSpec(
        name="flights",
        script="flights_server.py",
        description="OurAirports airport data with modelled routings and fares.",
        tools=("find_airports", "resolve_airport", "search_flights"),
        critical=False,
    ),
    "hotels": MCPServerSpec(
        name="hotels",
        script="hotels_server.py",
        description="Real OSM lodging with modelled nightly rates and neighbourhood context.",
        tools=(
            "search_accommodation",
            "estimate_stay_cost",
            "get_neighbourhood_context",
        ),
        critical=False,
    ),
    "currency": MCPServerSpec(
        name="currency",
        script="currency_server.py",
        description="Frankfurter/ECB foreign-exchange rates (real data).",
        tools=("convert", "get_rates", "convert_budget", "historical_rate"),
        critical=False,
    ),
}


def enabled_specs() -> list[MCPServerSpec]:
    """Servers enabled by ``MCP_ENABLED_SERVERS``, in configured order."""
    specs: list[MCPServerSpec] = []
    for name in settings.enabled_servers:
        spec = SERVERS.get(name)
        if spec is not None:
            specs.append(spec)
    return specs


def tool_owner(tool: str) -> str | None:
    """Which server exposes a given tool name."""
    for spec in SERVERS.values():
        if tool in spec.tools:
            return spec.name
    return None
