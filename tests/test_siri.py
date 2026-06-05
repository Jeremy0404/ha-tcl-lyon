"""Offline tests for the pure SIRI parsers, driven by tests/fixtures/ snapshots."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from custom_components.tcl_lyon.siri import (
    parse_departures,
    parse_ref,
    parse_situations,
    parse_time,
)

from .conftest import load_fixture

UTC = UTC


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        ("ActIV:Line::T2:SYTRAL", "T2"),
        ("ActIV:Line::C3:SYTRAL", "C3"),
        ("ActIV:StopArea:SP:32166:SYTRAL", "32166"),
        ("ActIV:StopArea:SP:S5484:SYTRAL", "S5484"),
        ("ActIV:Vehicle:Bus:1614:LOC", "1614"),
        ("ActIV:Operator::KBLMB:SYTRAL", "KBLMB"),
        ("weird-unmapped-ref", "weird-unmapped-ref"),
        ("", ""),
        (None, None),
    ],
)
def test_parse_ref(ref, expected):
    assert parse_ref(ref) == expected


def test_parse_time_handles_z_suffix():
    assert parse_time("2026-06-04T20:15:20Z") == datetime(2026, 6, 4, 20, 15, 20, tzinfo=UTC)


def test_parse_time_truncates_overlong_fraction():
    # 9 sub-second digits in the feed; datetime accepts at most 6.
    assert parse_time("2026-06-04T20:17:05.625066426Z") == datetime(
        2026, 6, 4, 20, 17, 5, 625066, tzinfo=UTC
    )


def test_parse_time_assumes_utc_when_naive():
    assert parse_time("2026-06-04T20:15:20").tzinfo == UTC


@pytest.mark.parametrize(
    "value",
    ["0001-01-01T00:00:00Z", "0001-01-01T00:00:00.000Z", None, "", "not-a-time"],
)
def test_parse_time_returns_none_for_sentinel_and_garbage(value):
    assert parse_time(value) is None


def test_parse_departures_flattens_and_sorts():
    departures = parse_departures(load_fixture("estimated_timetables.json"))

    # Two journeys, three calls between them = three departures total.
    assert len(departures) == 3
    # Sorted soonest-first by best-available time.
    assert [d.time.isoformat() for d in departures] == [
        "2026-06-04T20:15:20+00:00",  # realtime estimate
        "2026-06-04T20:21:10+00:00",  # aimed-only fallback
        "2026-06-04T20:25:00+00:00",  # cancelled service
    ]


def test_parse_departures_realtime_vs_scheduled():
    departures = parse_departures(load_fixture("estimated_timetables.json"))
    soonest = departures[0]

    assert soonest.is_realtime is True
    assert soonest.time == datetime(2026, 6, 4, 20, 15, 20, tzinfo=UTC)
    assert soonest.line_id == "T2"
    assert soonest.stop_id == "32166"
    assert soonest.direction == "outbound"
    assert soonest.destination_id == "33219"
    assert soonest.order == 18

    # Second call has no Expected* time → falls back to Aimed, not realtime.
    fallback = departures[1]
    assert fallback.is_realtime is False
    assert fallback.time == datetime(2026, 6, 4, 20, 21, 10, tzinfo=UTC)


def test_parse_departures_flags_cancellation():
    departures = parse_departures(load_fixture("estimated_timetables.json"))
    cancelled = departures[-1]
    assert cancelled.cancelled is True


def test_parse_departures_filters_by_stop():
    payload = load_fixture("estimated_timetables.json")

    at_32166 = parse_departures(payload, stop_ids={"32166"})
    assert {d.stop_id for d in at_32166} == {"32166"}
    assert len(at_32166) == 2  # one per journey

    at_32168 = parse_departures(payload, stop_ids={"32168"})
    assert len(at_32168) == 1
    assert at_32168[0].stop_id == "32168"

    assert parse_departures(payload, stop_ids={"does-not-exist"}) == []


def test_parse_situations_all():
    disruptions = parse_situations(load_fixture("situation_exchange.json"))
    assert len(disruptions) == 2

    first = disruptions[0]
    assert first.situation_number == "ACTIV_111_2"
    assert first.description == "Déviée dir. Villeurbanne Centre - 26/05 au 30/06"
    assert first.keywords == ("Perturbation",)
    assert first.affected_line_refs == ("ActIV:Line::27:SYTRAL",)
    assert first.affected_line_ids == ("27",)
    start, end = first.validity_periods[0]
    assert start == datetime(2026, 5, 26, 7, 42, 11, 671823, tzinfo=UTC)
    assert end == datetime(2026, 6, 30, 23, 30, 0, tzinfo=UTC)


def test_parse_situations_filters_by_line():
    payload = load_fixture("situation_exchange.json")

    t2 = parse_situations(payload, line_refs={"ActIV:Line::T2:SYTRAL"})
    assert len(t2) == 1
    assert t2[0].situation_number == "ACTIV_222_1"
    assert t2[0].affected_line_ids == ("T2", "T4")

    assert parse_situations(payload, line_refs={"ActIV:Line::99:SYTRAL"}) == []


def test_parse_handles_empty_payload():
    assert parse_departures({}) == []
    assert parse_situations({}) == []
