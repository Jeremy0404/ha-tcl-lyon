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

from custom_components.tcl_lyon.api import Departure
from custom_components.tcl_lyon.const import (
    CONF_DIRECTION,
    CONF_DIRECTION_NAME,
    CONF_LINE_ID,
    CONF_LINE_NAME,
    CONF_LINE_REF,
    CONF_QUAY_IDS,
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


def _make_sensor(hass, departures, target=TARGET):
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    coordinator = DeparturesCoordinator(
        hass, entry, client=None, line_refs=(LINE_REF,), stop_ids=(QUAY,)
    )
    coordinator.data = {LINE_REF: departures}
    return TclDepartureSensor(coordinator, STOP, target)


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
