from __future__ import annotations

from dataclasses import dataclass
from math import asin, cos, radians, sin, sqrt


@dataclass(frozen=True)
class GeoPoint:
    lat: float
    lon: float


def haversine_m(a: GeoPoint, b: GeoPoint) -> float:
    r = 6_371_000
    lat1 = radians(a.lat)
    lon1 = radians(a.lon)
    lat2 = radians(b.lat)
    lon2 = radians(b.lon)

    dlat = lat2 - lat1
    dlon = lon2 - lon1

    h = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 2 * r * asin(sqrt(h))
