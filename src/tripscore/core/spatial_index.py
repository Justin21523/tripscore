"""
Lightweight spatial indexing (grid bucket) for lat/lon points.

Used to avoid O(N) scans when the catalog grows to thousands of POIs.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Generic, TypeVar

from tripscore.core.geo import GeoPoint, haversine_m

T = TypeVar("T")


def _to_xy_m(lat: float, lon: float, *, lat0_deg: float) -> tuple[float, float]:
    # Equirectangular projection around a reference latitude (good enough for Taiwan-scale indexing).
    lat0 = math.radians(float(lat0_deg))
    x = float(lon) * 111_320.0 * math.cos(lat0)
    y = float(lat) * 110_540.0
    return x, y


@dataclass(frozen=True)
class _Entry(Generic[T]):
    item: T
    lat: float
    lon: float
    x_m: float
    y_m: float


class SpatialGridIndex(Generic[T]):
    def __init__(
        self,
        items: list[T],
        *,
        get_latlon: Callable[[T], tuple[float, float]],
        cell_size_m: float = 1200.0,
        lat0_deg: float = 23.7,
    ):
        if float(cell_size_m) <= 0:
            raise ValueError("cell_size_m must be > 0")
        self._cell_size_m = float(cell_size_m)
        self._lat0_deg = float(lat0_deg)
        self._cells: dict[tuple[int, int], list[_Entry[T]]] = {}
        self._entries: list[_Entry[T]] = []

        for it in items:
            try:
                lat, lon = get_latlon(it)
                lat_f = float(lat)
                lon_f = float(lon)
            except Exception:
                continue
            x_m, y_m = _to_xy_m(lat_f, lon_f, lat0_deg=self._lat0_deg)
            e = _Entry(item=it, lat=lat_f, lon=lon_f, x_m=x_m, y_m=y_m)
            self._entries.append(e)
            self._cells.setdefault(self._cell_key_xy(x_m, y_m), []).append(e)

    def _cell_key_xy(self, x_m: float, y_m: float) -> tuple[int, int]:
        return (int(math.floor(x_m / self._cell_size_m)), int(math.floor(y_m / self._cell_size_m)))

    def query_within(self, *, lat: float, lon: float, radius_m: float) -> list[T]:
        r = float(radius_m)
        if r <= 0:
            return []
        x0, y0 = _to_xy_m(float(lat), float(lon), lat0_deg=self._lat0_deg)
        cx, cy = self._cell_key_xy(x0, y0)
        steps = int(math.ceil(r / self._cell_size_m))

        origin = GeoPoint(lat=float(lat), lon=float(lon))
        out: list[T] = []
        for dx in range(-steps, steps + 1):
            for dy in range(-steps, steps + 1):
                cell = self._cells.get((cx + dx, cy + dy))
                if not cell:
                    continue
                for e in cell:
                    # Cheap bounding circle filter in projected space.
                    if (e.x_m - x0) ** 2 + (e.y_m - y0) ** 2 > (r * 1.15) ** 2:
                        continue
                    d = haversine_m(origin, GeoPoint(lat=e.lat, lon=e.lon))
                    if d <= r:
                        out.append(e.item)
        return out

    def nearest_distance_m(self, *, lat: float, lon: float, search_radius_m: float) -> float | None:
        x0, y0 = _to_xy_m(float(lat), float(lon), lat0_deg=self._lat0_deg)
        cx, cy = self._cell_key_xy(x0, y0)
        r = float(search_radius_m)
        if r <= 0:
            return None
        steps = int(math.ceil(r / self._cell_size_m))

        origin = GeoPoint(lat=float(lat), lon=float(lon))
        best: float | None = None
        found = False
        for dx in range(-steps, steps + 1):
            for dy in range(-steps, steps + 1):
                cell = self._cells.get((cx + dx, cy + dy))
                if not cell:
                    continue
                for e in cell:
                    found = True
                    if (e.x_m - x0) ** 2 + (e.y_m - y0) ** 2 > (r * 1.25) ** 2:
                        continue
                    d = haversine_m(origin, GeoPoint(lat=e.lat, lon=e.lon))
                    best = d if best is None else min(best, d)
        if not found:
            return None
        return best
