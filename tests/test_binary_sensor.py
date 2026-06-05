"""Tests for the line-disruption binary sensor.

Needs Home Assistant (CoordinatorEntity), so skipped where it isn't installed.
The entity is exercised against a coordinator whose data is set directly, keeping
the test about is_on/attribute logic rather than polling (covered in
test_coordinator).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

pytest.importorskip("homeassistant.runner")

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tcl_lyon.api import Disruption
from custom_components.tcl_lyon.binary_sensor import TclLineDisruptionSensor
from custom_components.tcl_lyon.const import (
    CONF_LINE_ID,
    CONF_LINE_NAME,
    CONF_LINE_REF,
    DOMAIN,
)
from custom_components.tcl_lyon.coordinator import DisruptionsCoordinator

LINE_REF = "ActIV:Line::T2:SYTRAL"
LINE = {CONF_LINE_REF: LINE_REF, CONF_LINE_ID: "T2", CONF_LINE_NAME: "T2"}

DISRUPTION = Disruption(
    situation_number="ACTIV_222_1",
    description="T2 interrompue entre Part-Dieu et Charpennes",
    report_type="incident",
    keywords=("Travaux",),
    affected_line_refs=(LINE_REF, "ActIV:Line::T4:SYTRAL"),
    affected_line_ids=("T2", "T4"),
    validity_periods=((datetime(2026, 6, 1, tzinfo=UTC), datetime(2026, 6, 10, tzinfo=UTC)),),
    creation_time=None,
)


def _make_sensor(hass, data):
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    coordinator = DisruptionsCoordinator(hass, entry, client=None, line_refs=(LINE_REF,))
    coordinator.data = data
    return TclLineDisruptionSensor(coordinator, LINE)


async def test_on_when_line_has_active_disruption(hass):
    sensor = _make_sensor(hass, {LINE_REF: [DISRUPTION]})

    assert sensor.is_on is True
    attrs = sensor.extra_state_attributes
    assert attrs["line_ref"] == LINE_REF
    assert attrs["disruption_count"] == 1
    assert attrs["summary"] == "Travaux — T2 interrompue entre Part-Dieu et Charpennes"
    assert len(attrs["disruptions"]) == 1
    served = attrs["disruptions"][0]
    assert served["situation_number"] == "ACTIV_222_1"
    assert served["keywords"] == ["Travaux"]
    assert served["validity_period"] == [
        {"start": "2026-06-01T00:00:00+00:00", "end": "2026-06-10T00:00:00+00:00"}
    ]


async def test_summary_joins_multiple_disruptions(hass):
    other = Disruption(
        situation_number="ACTIV_999_1",
        description="Arrêt non desservi",
        report_type="incident",
        keywords=("Perturbation",),
        affected_line_refs=(LINE_REF,),
        affected_line_ids=("T2",),
        validity_periods=(),
        creation_time=None,
    )
    sensor = _make_sensor(hass, {LINE_REF: [DISRUPTION, other]})

    attrs = sensor.extra_state_attributes
    assert attrs["disruption_count"] == 2
    assert attrs["summary"] == (
        "Travaux — T2 interrompue entre Part-Dieu et Charpennes\nPerturbation — Arrêt non desservi"
    )


async def test_off_when_line_clear(hass):
    sensor = _make_sensor(hass, {LINE_REF: []})

    assert sensor.is_on is False
    attrs = sensor.extra_state_attributes
    assert attrs["disruptions"] == []
    assert attrs["disruption_count"] == 0
    assert attrs["summary"] is None


async def test_off_when_coordinator_has_no_data(hass):
    sensor = _make_sensor(hass, None)

    assert sensor.is_on is False


async def test_unique_id_is_per_line(hass):
    sensor = _make_sensor(hass, {LINE_REF: []})

    assert sensor.unique_id.endswith("_line_T2_disrupted")
