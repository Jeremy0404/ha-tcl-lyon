"""Offline tests for the GTFS loader / search index.

Builds tiny GTFS zips on the fly (one UTF-8, one Windows-1252) so the encoding
fallback and the "only read stops + routes" behaviour are both exercised without
shipping a binary fixture.
"""

from __future__ import annotations

import zipfile
from datetime import datetime

import pytest

from custom_components.tcl_lyon.gtfs import GtfsError, GtfsIndex

STOPS_CSV = (
    "stop_id,stop_code,stop_name,location_type,parent_station,stop_lat,stop_lon\n"
    "S5484,,Bron Hôtel de Ville,1,,45.7400,4.9100\n"
    "32166,32166,Bron Hôtel de Ville,0,S5484,45.7401,4.9102\n"
    "48253,48253,Cuzin - Picasso,0,S6000,45.7800,4.8800\n"
    "S6000,,Cuzin - Picasso,1,,45.7801,4.8801\n"
)

ROUTES_CSV = (
    "route_id,agency_id,route_short_name,route_long_name,route_type,route_color,route_text_color\n"
    "T2,1,T2,Montrochet - Saint Priest Bel Air,0,2EB6AC,FFFFFF\n"
    "C3,1,C3,Laurent Bonnevay - Vaulx La Grappinière,11,778186,\n"
)

# Junk that would blow up a CSV parse — proves stop_times.txt is never read.
JUNK = b"\x00\x01\x02 this is not csv and must never be parsed \xff\xfe"


def _build_zip(path, *, encoding: str) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("stops.txt", STOPS_CSV.encode(encoding))
        archive.writestr("routes.txt", ROUTES_CSV.encode(encoding))
        archive.writestr("stop_times.txt", JUNK)
        archive.writestr("trips.txt", JUNK)


@pytest.fixture(params=["utf-8", "cp1252"])
def gtfs_index(request, tmp_path) -> GtfsIndex:
    zip_path = tmp_path / f"gtfs_{request.param}.zip"
    _build_zip(zip_path, encoding=request.param)
    return GtfsIndex.from_zip(zip_path)


def test_loads_stops_and_routes(gtfs_index):
    assert len(gtfs_index.stops) == 4
    assert len(gtfs_index.routes) == 2


def test_decodes_accents_regardless_of_encoding(gtfs_index):
    # The same accented name must come back correct from both UTF-8 and cp1252 zips.
    assert gtfs_index.stops["S5484"].name == "Bron Hôtel de Ville"


def test_stop_fields(gtfs_index):
    station = gtfs_index.stops["S5484"]
    assert station.is_station is True
    assert station.location_type == 1
    assert station.parent_station is None
    assert station.lat == pytest.approx(45.74)

    child = gtfs_index.stops["32166"]
    assert child.is_station is False
    assert child.parent_station == "S5484"


def test_route_fields(gtfs_index):
    tram = gtfs_index.routes["T2"]
    assert tram.short_name == "T2"
    assert tram.long_name == "Montrochet - Saint Priest Bel Air"
    assert tram.route_type == 0
    assert tram.color == "2EB6AC"

    # Empty trailing field → None, not "".
    assert gtfs_index.routes["C3"].text_color is None


def test_search_stops_is_accent_and_case_insensitive(gtfs_index):
    results = gtfs_index.search_stops("hotel")
    assert [s.stop_id for s in results] == ["S5484"]  # station only, child excluded


def test_search_stops_can_include_children(gtfs_index):
    results = gtfs_index.search_stops("hotel", stations_only=False)
    assert {s.stop_id for s in results} == {"S5484", "32166"}


def test_search_stops_empty_query_returns_nothing(gtfs_index):
    assert gtfs_index.search_stops("") == []


def test_search_routes(gtfs_index):
    assert [r.route_id for r in gtfs_index.search_routes("t2")] == ["T2"]
    # Matches on the long name too.
    assert [r.route_id for r in gtfs_index.search_routes("bonnevay")] == ["C3"]


def test_missing_member_raises_gtfs_error(tmp_path):
    incomplete = tmp_path / "incomplete.zip"
    with zipfile.ZipFile(incomplete, "w") as archive:
        archive.writestr("routes.txt", ROUTES_CSV.encode("utf-8"))
    with pytest.raises(GtfsError):
        GtfsIndex.from_zip(incomplete)


def test_from_bytes_matches_from_zip(tmp_path):
    zip_path = tmp_path / "gtfs.zip"
    _build_zip(zip_path, encoding="utf-8")
    index = GtfsIndex.from_bytes(zip_path.read_bytes())
    assert set(index.stops) == {"S5484", "32166", "48253", "S6000"}
    assert set(index.routes) == {"T2", "C3"}


def test_quay_ids_expands_station_to_children(gtfs_index):
    # The user picks the parent S5484; SIRI only ever names the child quay 32166.
    assert gtfs_index.quay_ids("S5484") == ["32166"]


def test_quay_ids_falls_back_to_self_for_childless_stop(gtfs_index):
    # 32166 is a quay with no children of its own → matches just itself.
    assert gtfs_index.quay_ids("32166") == ["32166"]


# --- full build: the stop→lines serving map ---------------------------------

FULL_STOPS_CSV = (
    "stop_id,stop_code,stop_name,location_type,parent_station,stop_lat,stop_lon\n"
    "S5484,,Bron Hôtel de Ville,1,,45.7400,4.9100\n"
    "32166,32166,Bron Hôtel de Ville,0,S5484,45.7401,4.9102\n"
    "S6000,,Cuzin - Picasso,1,,45.7801,4.8801\n"
    "48253,48253,Cuzin - Picasso,0,S6000,45.7800,4.8800\n"
    "S7000,,Saint-Priest Bel Air,1,,45.7000,4.9400\n"
    "33219,33219,Saint-Priest Bel Air,0,S7000,45.7001,4.9402\n"
    "90001,90001,Standalone Halt,0,,45.0,4.0\n"
)
TRIPS_CSV = "route_id,service_id,trip_id\nT2,W,t1\nT2,W,t2\nC3,W,t3\n"
# t1/t2 (T2) call at 32166 (→S5484) and 33219 (→S7000); t3 (C3) at 48253 (→S6000)
# and the parentless quay 90001 (keyed by itself).
STOP_TIMES_CSV = (
    "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
    "t1,08:00:00,08:00:00,32166,1\n"
    "t1,08:10:00,08:10:00,33219,2\n"
    "t2,09:00:00,09:00:00,32166,1\n"
    "t3,07:00:00,07:00:00,48253,1\n"
    "t3,07:30:00,07:30:00,90001,2\n"
)
FEED_INFO_CSV = "feed_publisher_name,feed_version\nTCL,2026-06-06\n"


def _build_full_zip(path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("stops.txt", FULL_STOPS_CSV.encode("utf-8"))
        archive.writestr("routes.txt", ROUTES_CSV.encode("utf-8"))
        archive.writestr("trips.txt", TRIPS_CSV.encode("utf-8"))
        archive.writestr("stop_times.txt", STOP_TIMES_CSV.encode("utf-8"))
        archive.writestr("feed_info.txt", FEED_INFO_CSV.encode("utf-8"))


@pytest.fixture
def full_index(tmp_path) -> GtfsIndex:
    zip_path = tmp_path / "gtfs_full.zip"
    _build_full_zip(zip_path)
    return GtfsIndex.from_zip_full(zip_path)


def test_full_build_maps_lines_to_parent_stations(full_index):
    assert full_index.routes_serving("S5484") == {"T2"}
    assert full_index.routes_serving("S7000") == {"T2"}
    assert full_index.routes_serving("S6000") == {"C3"}


def test_full_build_keys_parentless_quay_by_itself(full_index):
    assert full_index.routes_serving("90001") == {"C3"}


def test_routes_serving_unknown_stop_is_empty(full_index):
    assert full_index.routes_serving("does-not-exist") == set()


def test_full_build_records_metadata(full_index):
    assert isinstance(full_index.built_at, datetime)
    assert full_index.feed_version == "2026-06-06"


def test_search_routes_serving_filter(full_index):
    assert [r.route_id for r in full_index.search_routes("t2", serving={"T2"})] == ["T2"]
    # T2 exists but is filtered out when restricted to a set it isn't in.
    assert full_index.search_routes("t2", serving={"C3"}) == []
    # None means no filter — the default behaviour.
    assert [r.route_id for r in full_index.search_routes("t2")] == ["T2"]


def test_cheap_build_leaves_serving_map_empty(tmp_path):
    # from_bytes must not read trips/stop_times, so no serving map results.
    zip_path = tmp_path / "gtfs_full.zip"
    _build_full_zip(zip_path)
    index = GtfsIndex.from_bytes(zip_path.read_bytes())
    assert index.stop_routes == {}
    assert index.routes_serving("S5484") == set()


def test_to_dict_from_dict_round_trip(full_index):
    restored = GtfsIndex.from_dict(full_index.to_dict())
    assert set(restored.stops) == set(full_index.stops)
    assert set(restored.routes) == set(full_index.routes)
    assert restored.stop_routes == full_index.stop_routes
    assert restored.built_at == full_index.built_at
    assert restored.feed_version == full_index.feed_version
    # A restored stop/route keeps its fields, not just its id.
    assert restored.stops["S5484"].name == "Bron Hôtel de Ville"
    assert restored.routes["T2"].color == "2EB6AC"


def test_from_dict_rejects_unknown_schema(full_index):
    data = full_index.to_dict()
    data["schema_version"] = 999
    with pytest.raises(ValueError, match="schema"):
        GtfsIndex.from_dict(data)


def test_full_build_missing_column_raises(tmp_path):
    zip_path = tmp_path / "bad.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("stops.txt", FULL_STOPS_CSV.encode("utf-8"))
        archive.writestr("routes.txt", ROUTES_CSV.encode("utf-8"))
        archive.writestr("trips.txt", "service_id,trip_id\nW,t1\n")  # no route_id
        archive.writestr("stop_times.txt", STOP_TIMES_CSV.encode("utf-8"))
    with pytest.raises(GtfsError):
        GtfsIndex.from_zip_full(zip_path)
