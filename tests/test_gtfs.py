"""Offline tests for the GTFS loader / search index.

Builds tiny GTFS zips on the fly (one UTF-8, one Windows-1252) so the encoding
fallback and the "only read stops + routes" behaviour are both exercised without
shipping a binary fixture.
"""

from __future__ import annotations

import zipfile

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
