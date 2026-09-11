"""Spherical geometry helpers.

Used for great-circle flight distances, airport proximity search and building
Overpass bounding boxes. Earth is modelled as a sphere with the mean radius,
which is accurate to ~0.3% -- far tighter than the precision this project needs.
"""

from __future__ import annotations

import math

EARTH_RADIUS_KM = 6371.0088


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two WGS84 points, in kilometres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    d_phi = p2 - p1
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(min(1.0, a)))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial compass bearing from point 1 to point 2, in degrees (0-360)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    d_lambda = math.radians(lon2 - lon1)
    y = math.sin(d_lambda) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(d_lambda)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def bbox_around(lat: float, lon: float, radius_km: float) -> tuple[float, float, float, float]:
    """Return ``(south, west, north, east)`` for a radius around a point.

    Longitude degrees shrink with latitude, so the east/west span is divided by
    ``cos(lat)``. The cosine is floored to avoid a blow-up near the poles.
    """
    d_lat = radius_km / 111.32
    d_lon = radius_km / (111.32 * max(0.15, math.cos(math.radians(lat))))
    return (
        max(-90.0, lat - d_lat),
        max(-180.0, lon - d_lon),
        min(90.0, lat + d_lat),
        min(180.0, lon + d_lon),
    )


def midpoint(lat1: float, lon1: float, lat2: float, lon2: float) -> tuple[float, float]:
    """Great-circle midpoint, used to pick plausible flight connection hubs."""
    p1, lon1_rad, p2 = math.radians(lat1), math.radians(lon1), math.radians(lat2)
    d_lambda = math.radians(lon2 - lon1)
    bx = math.cos(p2) * math.cos(d_lambda)
    by = math.cos(p2) * math.sin(d_lambda)
    lat3 = math.atan2(math.sin(p1) + math.sin(p2), math.sqrt((math.cos(p1) + bx) ** 2 + by**2))
    lon3 = lon1_rad + math.atan2(by, math.cos(p1) + bx)
    return math.degrees(lat3), (math.degrees(lon3) + 540) % 360 - 180
