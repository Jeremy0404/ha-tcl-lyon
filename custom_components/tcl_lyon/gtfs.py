"""Loader and search index for the static GTFS feed (stops + routes only).

The feed ships as a ~20 MB zip whose ``stop_times.txt`` alone is ~157 MB once
uncompressed, so this reads only ``stops.txt`` and ``routes.txt`` — the two files
the config flow needs for its stop/line pickers — and never touches the rest.

Like siri.py, this is HA/aiohttp-free so it can be tested offline.
See docs/02-data-sources.md for column meanings.
"""

from __future__ import annotations

import csv
import io
import unicodedata
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from .const import GTFS_ENCODINGS

STOPS_FILE = "stops.txt"
ROUTES_FILE = "routes.txt"

# GTFS location_type: 1 = parent station (what a user means by "Bellecour"),
# 0 = a physical quay/stop point (what SIRI StopPointRef refers to).
LOCATION_TYPE_STATION = 1


class GtfsError(Exception):
    """Raised when the GTFS archive is missing an expected file."""


@dataclass(frozen=True, slots=True)
class Stop:
    """A GTFS stop: either a parent station or a child quay."""

    stop_id: str
    name: str
    location_type: int
    parent_station: str | None
    lat: float | None
    lon: float | None

    @property
    def is_station(self) -> bool:
        return self.location_type == LOCATION_TYPE_STATION


@dataclass(frozen=True, slots=True)
class Route:
    """A GTFS route (commercial line)."""

    route_id: str
    short_name: str
    long_name: str
    route_type: int | None
    color: str | None
    text_color: str | None


class GtfsIndex:
    """In-memory index over the GTFS stops and routes, with accent-insensitive search."""

    def __init__(self, stops: dict[str, Stop], routes: dict[str, Route]) -> None:
        self.stops = stops
        self.routes = routes

    @classmethod
    def from_zip(cls, zip_path: str | Path) -> GtfsIndex:
        """Build the index from a GTFS zip, reading only stops.txt and routes.txt."""
        with zipfile.ZipFile(Path(zip_path)) as archive:
            stops = _parse_stops(_read_member(archive, STOPS_FILE))
            routes = _parse_routes(_read_member(archive, ROUTES_FILE))
        return cls(stops, routes)

    @classmethod
    def from_dir(cls, directory: str | Path) -> GtfsIndex:
        """Build the index from an already-extracted GTFS directory."""
        base = Path(directory)
        return cls(
            _parse_stops(_read_file(base / STOPS_FILE)),
            _parse_routes(_read_file(base / ROUTES_FILE)),
        )

    def search_stops(
        self, query: str, *, stations_only: bool = True, limit: int = 50
    ) -> list[Stop]:
        """Substring search over stop names, accent- and case-insensitive.

        ``stations_only`` (default) restricts to parent stations — the level a user
        picks from. Results favour earlier matches, then alphabetical order.
        """
        needle = _normalize(query)
        if not needle:
            return []
        matches: list[tuple[int, str, Stop]] = []
        for stop in self.stops.values():
            if stations_only and not stop.is_station:
                continue
            haystack = _normalize(stop.name)
            position = haystack.find(needle)
            if position != -1:
                matches.append((position, haystack, stop))
        matches.sort(key=lambda match: (match[0], match[1]))
        return [match[2] for match in matches[:limit]]

    def search_routes(self, query: str, *, limit: int = 50) -> list[Route]:
        """Search routes by short name (preferred) then long name, accent-insensitive."""
        needle = _normalize(query)
        if not needle:
            return []
        matches: list[tuple[int, str, Route]] = []
        for route in self.routes.values():
            short = _normalize(route.short_name)
            long = _normalize(route.long_name)
            if needle in short:
                matches.append((0, short, route))
            elif needle in long:
                matches.append((1, long, route))
        matches.sort(key=lambda match: (match[0], match[1]))
        return [match[2] for match in matches[:limit]]


# --- internal helpers -------------------------------------------------------


def _read_member(archive: zipfile.ZipFile, name: str) -> bytes:
    try:
        return archive.read(name)
    except KeyError as err:
        raise GtfsError(f"{name} missing from GTFS archive") from err


def _read_file(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except FileNotFoundError as err:
        raise GtfsError(f"{path.name} missing from GTFS directory") from err


def _decode(raw: bytes) -> str:
    """Decode GTFS bytes, trying each known encoding before a lossy last resort."""
    for encoding in GTFS_ENCODINGS:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    # Never raise over one stray byte in a 1 MB file — replace and carry on.
    return raw.decode(GTFS_ENCODINGS[0], errors="replace")


def _rows(raw: bytes) -> Iterator[dict[str, str]]:
    # A UTF-8 BOM would otherwise be folded into the first column header.
    text = _decode(raw).lstrip("\ufeff")
    yield from csv.DictReader(io.StringIO(text))


def _parse_stops(raw: bytes) -> dict[str, Stop]:
    stops: dict[str, Stop] = {}
    for row in _rows(raw):
        stop_id = _clean(row.get("stop_id"))
        if not stop_id:
            continue
        stops[stop_id] = Stop(
            stop_id=stop_id,
            name=_clean(row.get("stop_name")),
            location_type=_int(row.get("location_type"), default=0),
            parent_station=_clean(row.get("parent_station")) or None,
            lat=_float(row.get("stop_lat")),
            lon=_float(row.get("stop_lon")),
        )
    return stops


def _parse_routes(raw: bytes) -> dict[str, Route]:
    routes: dict[str, Route] = {}
    for row in _rows(raw):
        route_id = _clean(row.get("route_id"))
        if not route_id:
            continue
        routes[route_id] = Route(
            route_id=route_id,
            short_name=_clean(row.get("route_short_name")),
            long_name=_clean(row.get("route_long_name")),
            route_type=_int(row.get("route_type"), default=None),
            color=_clean(row.get("route_color")) or None,
            text_color=_clean(row.get("route_text_color")) or None,
        )
    return routes


def _normalize(text: str | None) -> str:
    """Casefold and strip diacritics so 'hotel' matches 'Hôtel'."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return stripped.casefold().strip()


def _clean(value: str | None) -> str:
    return value.strip() if value else ""


def _int(value: str | None, *, default: int | None) -> int | None:
    try:
        return int(_clean(value))
    except (TypeError, ValueError):
        return default


def _float(value: str | None) -> float | None:
    try:
        return float(_clean(value))
    except (TypeError, ValueError):
        return None
