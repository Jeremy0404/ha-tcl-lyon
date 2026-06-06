"""Loader and search index for the static GTFS feed.

The cheap path (:meth:`GtfsIndex.from_bytes` / :meth:`~GtfsIndex.from_zip`) reads
only ``stops.txt`` and ``routes.txt`` — the two small files the pickers need to
search — and never touches the rest, because ``stop_times.txt`` alone is ~157 MB
uncompressed.

The full path (:meth:`~GtfsIndex.from_bytes_full`) additionally streams
``trips.txt`` + ``stop_times.txt`` to build the stop→lines serving map, so the
line picker can be filtered to the lines that actually serve the chosen stop. It
is expensive, so it only runs to (re)build the cached index — never inline in a
flow. See store.py for how the result is shipped and cached.

Like siri.py, this is HA/aiohttp-free so it can be tested offline.
See docs/02-data-sources.md for column meanings.
"""

from __future__ import annotations

import csv
import io
import sys
import unicodedata
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .const import GTFS_ENCODINGS, GTFS_INDEX_SCHEMA_VERSION

STOPS_FILE = "stops.txt"
ROUTES_FILE = "routes.txt"
TRIPS_FILE = "trips.txt"
STOP_TIMES_FILE = "stop_times.txt"
FEED_INFO_FILE = "feed_info.txt"

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
    """In-memory index over the GTFS stops and routes, with accent-insensitive search.

    ``stop_routes`` maps a parent-station id → the route_ids that serve it. It is
    only populated by the full build (:meth:`from_bytes_full`); the cheap build
    leaves it empty, in which case :meth:`routes_serving` returns nothing and the
    line picker shows every line (the safe fallback).
    """

    def __init__(
        self,
        stops: dict[str, Stop],
        routes: dict[str, Route],
        stop_routes: dict[str, list[str]] | None = None,
        *,
        built_at: datetime | None = None,
        feed_version: str | None = None,
    ) -> None:
        self.stops = stops
        self.routes = routes
        self.stop_routes = stop_routes or {}
        self.built_at = built_at
        self.feed_version = feed_version

    @classmethod
    def from_zip(cls, zip_path: str | Path) -> GtfsIndex:
        """Build the index from a GTFS zip, reading only stops.txt and routes.txt."""
        with zipfile.ZipFile(Path(zip_path)) as archive:
            return cls._from_archive(archive)

    @classmethod
    def from_bytes(cls, data: bytes) -> GtfsIndex:
        """Build the index from a GTFS zip held in memory (config-flow download)."""
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            return cls._from_archive(archive)

    @classmethod
    def from_zip_full(cls, zip_path: str | Path) -> GtfsIndex:
        """Full build from a zip on disk, including the stop→lines serving map."""
        with zipfile.ZipFile(Path(zip_path)) as archive:
            return cls._from_archive(archive, with_serving=True)

    @classmethod
    def from_bytes_full(cls, data: bytes) -> GtfsIndex:
        """Full build from in-memory zip bytes, including the stop→lines serving map.

        Streams trips.txt + stop_times.txt, so it is the expensive path — run it in
        an executor and only to (re)build the cached index, never inline in a flow.
        """
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            return cls._from_archive(archive, with_serving=True)

    @classmethod
    def from_dir(cls, directory: str | Path) -> GtfsIndex:
        """Build the index from an already-extracted GTFS directory."""
        base = Path(directory)
        return cls(
            _parse_stops(_read_file(base / STOPS_FILE)),
            _parse_routes(_read_file(base / ROUTES_FILE)),
        )

    @classmethod
    def _from_archive(cls, archive: zipfile.ZipFile, *, with_serving: bool = False) -> GtfsIndex:
        stops = _parse_stops(_read_member(archive, STOPS_FILE))
        routes = _parse_routes(_read_member(archive, ROUTES_FILE))
        if not with_serving:
            return cls(stops, routes)
        return cls(
            stops,
            routes,
            _build_stop_routes(archive, stops),
            built_at=datetime.now(UTC),
            feed_version=_read_feed_version(archive),
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GtfsIndex:
        """Rebuild an index from its :meth:`to_dict` form (cache / shipped file)."""
        if data.get("schema_version") != GTFS_INDEX_SCHEMA_VERSION:
            raise ValueError("unsupported GTFS index schema version")
        stops = {row[0]: _stop_from_row(row) for row in data["stops"]}
        routes = {row[0]: _route_from_row(row) for row in data["routes"]}
        built = data.get("built_at")
        return cls(
            stops,
            routes,
            {station: list(ids) for station, ids in data.get("stop_routes", {}).items()},
            built_at=datetime.fromisoformat(built) if built else None,
            feed_version=data.get("feed_version"),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a compact JSON-friendly dict (cache / shipped file)."""
        return {
            "schema_version": GTFS_INDEX_SCHEMA_VERSION,
            "built_at": self.built_at.isoformat() if self.built_at else None,
            "feed_version": self.feed_version,
            "stops": [_stop_to_row(stop) for stop in self.stops.values()],
            "routes": [_route_to_row(route) for route in self.routes.values()],
            "stop_routes": self.stop_routes,
        }

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

    def search_routes(
        self, query: str, *, serving: set[str] | None = None, limit: int = 50
    ) -> list[Route]:
        """Search routes by short name (preferred) then long name, accent-insensitive.

        ``serving`` (a set of route_ids) restricts results to lines that serve the
        chosen stop — pass :meth:`routes_serving` for it. ``None`` means no filter.
        """
        needle = _normalize(query)
        if not needle:
            return []
        matches: list[tuple[int, str, Route]] = []
        for route in self.routes.values():
            if serving is not None and route.route_id not in serving:
                continue
            short = _normalize(route.short_name)
            long = _normalize(route.long_name)
            if needle in short:
                matches.append((0, short, route))
            elif needle in long:
                matches.append((1, long, route))
        matches.sort(key=lambda match: (match[0], match[1]))
        return [match[2] for match in matches[:limit]]

    def routes_serving(self, station_id: str) -> set[str]:
        """route_ids known to serve the given parent station.

        Empty when the serving map is absent (cheap build) or has no entry for the
        station — callers treat that as "don't filter", never as "no lines here".
        """
        return set(self.stop_routes.get(station_id, ()))

    def quay_ids(self, stop_id: str) -> list[str]:
        """SIRI StopPointRef ids to match for the stop the user picked.

        SIRI refers to physical quays (``location_type=0``), never to the parent
        station a user searches for, so a station expands to its child quays. A
        quay (or a childless/standalone stop) maps to just itself.
        """
        children = sorted(
            child.stop_id for child in self.stops.values() if child.parent_station == stop_id
        )
        return children or [stop_id]


# --- internal helpers -------------------------------------------------------


def _build_stop_routes(archive: zipfile.ZipFile, stops: dict[str, Stop]) -> dict[str, list[str]]:
    """Stream trips.txt + stop_times.txt into a parent-station → route_ids map.

    Only the (trip_id, route_id) and (trip_id, stop_id) columns are read, and
    stop_times.txt is streamed rather than loaded whole — it is ~157 MB. Quay-level
    stops are folded up to their parent station, the id the picker keys on.
    """
    trip_to_route: dict[str, str] = {}
    with _open_member(archive, TRIPS_FILE) as stream:
        reader = csv.reader(stream)
        trip_col, route_col = _columns(next(reader, []), TRIPS_FILE, "trip_id", "route_id")
        last = max(trip_col, route_col)
        for row in reader:
            if len(row) > last:
                # Intern route ids: ~800 distinct values shared across millions of trips.
                trip_to_route[row[trip_col]] = sys.intern(row[route_col])

    station_routes: dict[str, set[str]] = {}
    with _open_member(archive, STOP_TIMES_FILE) as stream:
        reader = csv.reader(stream)
        trip_col, stop_col = _columns(next(reader, []), STOP_TIMES_FILE, "trip_id", "stop_id")
        last = max(trip_col, stop_col)
        for row in reader:
            if len(row) <= last:
                continue
            route = trip_to_route.get(row[trip_col])
            if route is None:
                continue
            stop_id = row[stop_col]
            stop = stops.get(stop_id)
            station = stop.parent_station if stop and stop.parent_station else stop_id
            station_routes.setdefault(station, set()).add(route)

    return {station: sorted(routes) for station, routes in station_routes.items()}


def _read_feed_version(archive: zipfile.ZipFile) -> str | None:
    """The feed_info.txt version string, if the (optional) file is present."""
    try:
        raw = archive.read(FEED_INFO_FILE)
    except KeyError:
        return None
    for row in _rows(raw):
        return _clean(row.get("feed_version")) or None
    return None


def _columns(header: list[str], filename: str, *names: str) -> tuple[int, ...]:
    """Resolve column names to indices once, raising GtfsError on a missing column."""
    try:
        return tuple(header.index(name) for name in names)
    except ValueError as err:
        raise GtfsError(f"{filename} missing an expected column") from err


def _open_member(archive: zipfile.ZipFile, name: str) -> io.TextIOWrapper:
    """Open a zip member as a streamed text reader.

    Decodes as UTF-8 with replacement: only ASCII id/number columns are read from
    the streamed files, so a stray cp1252 byte in an unread column can't matter.
    """
    try:
        raw = archive.open(name)
    except KeyError as err:
        raise GtfsError(f"{name} missing from GTFS archive") from err
    return io.TextIOWrapper(raw, encoding="utf-8", errors="replace", newline="")


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


def _stop_to_row(stop: Stop) -> list[Any]:
    return [stop.stop_id, stop.name, stop.location_type, stop.parent_station, stop.lat, stop.lon]


def _stop_from_row(row: list[Any]) -> Stop:
    stop_id, name, location_type, parent_station, lat, lon = row
    return Stop(
        stop_id=stop_id,
        name=name,
        location_type=location_type,
        parent_station=parent_station,
        lat=lat,
        lon=lon,
    )


def _route_to_row(route: Route) -> list[Any]:
    return [
        route.route_id,
        route.short_name,
        route.long_name,
        route.route_type,
        route.color,
        route.text_color,
    ]


def _route_from_row(row: list[Any]) -> Route:
    route_id, short_name, long_name, route_type, color, text_color = row
    return Route(
        route_id=route_id,
        short_name=short_name,
        long_name=long_name,
        route_type=route_type,
        color=color,
        text_color=text_color,
    )


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
