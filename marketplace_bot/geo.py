"""Geocoding and distance checks, so results really are within the radius you asked for.

Facebook only shows a city name for each listing (e.g. "Round Rock, TX"), so
distances are measured city-center to city-center and are approximate.
Lookups use OpenStreetMap's free Nominatim service and are cached on disk.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable

from geopy.distance import geodesic
from geopy.exc import GeopyError
from geopy.extra.rate_limiter import RateLimiter
from geopy.geocoders import Nominatim

log = logging.getLogger(__name__)

KM_PER_MILE = 1.609344

# (query, country_code or None) -> (lat, lon, country_code) or None
GeocodeFn = Callable[[str, str | None], tuple[float, float, str] | None]


def miles_between(a: tuple[float, float], b: tuple[float, float]) -> float:
    return geodesic(a, b).miles


def nominatim_geocoder(user_agent: str) -> GeocodeFn:
    geolocator = Nominatim(user_agent=user_agent, timeout=10)
    # Nominatim's usage policy allows at most one request per second.
    geocode = RateLimiter(geolocator.geocode, min_delay_seconds=1.1, max_retries=2, swallow_exceptions=False)

    def lookup(query: str, country_code: str | None) -> tuple[float, float, str] | None:
        kwargs = {"addressdetails": True, "exactly_one": True}
        if country_code:
            kwargs["country_codes"] = country_code
        result = geocode(query, **kwargs)
        if result is None:
            return None
        cc = (result.raw.get("address") or {}).get("country_code", "") or ""
        return result.latitude, result.longitude, cc

    return lookup


class GeoLocator:
    def __init__(self, cache_path: Path, geocode: GeocodeFn):
        self._cache_path = cache_path
        self._geocode = geocode
        self._cache: dict[str, list | None] = {}
        if cache_path.exists():
            try:
                self._cache = json.loads(cache_path.read_text())
            except (OSError, ValueError):
                log.warning("Ignoring unreadable geocode cache at %s", cache_path)

    def lookup(self, place: str, country_code: str | None = None) -> tuple[float, float, str] | None:
        place = " ".join(place.split())
        if not place:
            return None
        key = f"{place.lower()}|{country_code or ''}"
        if key in self._cache:
            hit = self._cache[key]
            return tuple(hit) if hit else None
        try:
            result = self._geocode(place, country_code)
        except GeopyError as exc:
            log.warning("Could not geocode %r: %s", place, exc)
            return None  # not cached, so it is retried next run
        self._cache[key] = list(result) if result else None
        self._save()
        return result

    def _save(self) -> None:
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._cache_path.write_text(json.dumps(self._cache, indent=0, sort_keys=True))
        except OSError as exc:
            log.warning("Could not write geocode cache: %s", exc)
