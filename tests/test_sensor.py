"""Tests for the departure sensor.

Needs Home Assistant (CoordinatorEntity), so skipped where it isn't installed.
The entity is exercised against a coordinator whose data is set directly, keeping
the test about state/attribute logic rather than polling (covered in
test_coordinator).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("homeassistant.runner")

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tcl_lyon.api import Departure, GtfsIndex, Route
from custom_components.tcl_lyon.const import (
    CONF_DIRECTION,
    CONF_DIRECTION_NAME,
    CONF_LINE_COLOR,
    CONF_LINE_ID,
    CONF_LINE_NAME,
    CONF_LINE_REF,
    CONF_LINE_TEXT_COLOR,
    CONF_QUAY_IDS,
    CONF_ROUTE_TYPE,
    CONF_STOP_ID,
    CONF_STOP_NAME,
    DOMAIN,
)
from custom_components.tcl_lyon.coordinator import DeparturesCoordinator
from custom_components.tcl_lyon.sensor import TclDepartureSensor

LINE_REF = "ActIV:Line::T2:SYTRAL"
QUAY = "32166"
NOW = datetime(2026, 6, 6, 12, 0, tzinfo=UTC)

STOP = {
    CONF_STOP_ID: "SP_42",
    CONF_STOP_NAME: "Bellecour",
    CONF_QUAY_IDS: [QUAY],
}
TARGET = {
    CONF_LINE_REF: LINE_REF,
    CONF_LINE_ID: "T2",
    CONF_LINE_NAME: "T2",
    CONF_DIRECTION: "outbound",
    CONF_DIRECTION_NAME: "Saint-Priest",
}


def _departure(
    minutes: float, *, cancelled: bool = False, direction: str = "outbound"
) -> Departure:
    when = NOW + timedelta(minutes=minutes)
    return Departure(
        line_ref=LINE_REF,
        line_id="T2",
        stop_id=QUAY,
        direction=direction,
        destination_id="42",
        order=1,
        aimed=when,
        expected=when,
        cancelled=cancelled,
    )


def _make_sensor(hass, departures, target=TARGET, index=None):
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    coordinator = DeparturesCoordinator(
        hass, entry, client=None, line_refs=(LINE_REF,), stop_ids=(QUAY,)
    )
    coordinator.data = {LINE_REF: departures}
    return TclDepartureSensor(coordinator, STOP, target, index)


async def test_next_departure_time_matches_state_passage(hass, freezer):
    freezer.move_to(NOW)
    sensor = _make_sensor(hass, [_departure(5), _departure(20)])

    assert sensor.native_value == 5
    attrs = sensor.extra_state_attributes
    assert attrs["next_departure_time"] == (NOW + timedelta(minutes=5)).isoformat()


async def test_next_departure_time_skips_past_and_cancelled(hass, freezer):
    freezer.move_to(NOW)
    # A passage already gone, a cancelled one, then the real next one.
    sensor = _make_sensor(hass, [_departure(-3), _departure(4, cancelled=True), _departure(9)])

    assert sensor.native_value == 9
    attrs = sensor.extra_state_attributes
    assert attrs["next_departure_time"] == (NOW + timedelta(minutes=9)).isoformat()


async def test_next_departure_time_none_when_no_passage(hass, freezer):
    freezer.move_to(NOW)
    sensor = _make_sensor(hass, [])

    assert sensor.native_value is None
    assert sensor.extra_state_attributes["next_departure_time"] is None


async def test_next_departure_time_none_when_all_in_past(hass, freezer):
    freezer.move_to(NOW)
    sensor = _make_sensor(hass, [_departure(-10), _departure(-2)])

    assert sensor.extra_state_attributes["next_departure_time"] is None


async def test_next_departure_time_respects_direction(hass, freezer):
    freezer.move_to(NOW)
    # The soonest passage is the wrong direction; it must be ignored.
    sensor = _make_sensor(
        hass, [_departure(3, direction="inbound"), _departure(8, direction="outbound")]
    )

    assert sensor.native_value == 8
    assert (
        sensor.extra_state_attributes["next_departure_time"]
        == (NOW + timedelta(minutes=8)).isoformat()
    )


def _route(route_type, color="2EB6AC", text_color="FFFFFF"):
    return Route(
        route_id="T2",
        short_name="T2",
        long_name="",
        route_type=route_type,
        color=color,
        text_color=text_color,
    )


async def test_icon_and_color_from_target_fields(hass):
    target = {
        **TARGET,
        CONF_ROUTE_TYPE: 0,  # tram
        CONF_LINE_COLOR: "2EB6AC",
        CONF_LINE_TEXT_COLOR: "FFFFFF",
    }
    sensor = _make_sensor(hass, [], target=target)

    assert sensor.icon == "mdi:tram"
    attrs = sensor.extra_state_attributes
    assert attrs["line_color"] == "#2EB6AC"
    assert attrs["line_text_color"] == "#FFFFFF"


async def test_icon_per_route_type(hass):
    cases = {0: "mdi:tram", 1: "mdi:subway-variant", 7: "mdi:cable-car", 11: "mdi:bus-electric"}
    for route_type, icon in cases.items():
        sensor = _make_sensor(hass, [], target={**TARGET, CONF_ROUTE_TYPE: route_type})
        assert sensor.icon == icon


async def test_icon_defaults_when_route_type_unknown(hass):
    sensor = _make_sensor(hass, [], target={**TARGET, CONF_ROUTE_TYPE: 999})

    assert sensor.icon == "mdi:bus"


async def test_backfill_icon_and_color_from_index(hass):
    # An "old" target (no colour fields) is filled in from the GTFS index.
    index = GtfsIndex(stops={}, routes={"T2": _route(0)})
    sensor = _make_sensor(hass, [], target=TARGET, index=index)

    assert sensor.icon == "mdi:tram"
    assert sensor.extra_state_attributes["line_color"] == "#2EB6AC"


async def test_neutral_default_without_fields_or_index(hass):
    sensor = _make_sensor(hass, [], target=TARGET)

    assert sensor.icon == "mdi:bus"
    attrs = sensor.extra_state_attributes
    assert attrs["line_color"] is None
    assert attrs["line_text_color"] is None
