"""Tests for DeparturesCoordinator — the poll-per-line, filter-by-stop logic.

Needs Home Assistant, so the whole module is skipped where it isn't installed
(the pure parser suites still collect without it).
"""

from __future__ import annotations

import pytest

# homeassistant.runner imports fcntl → skips on native Windows, runs on Linux/CI.
pytest.importorskip("homeassistant.runner")

from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tcl_lyon.api import TclLyonAuthError, TclLyonConnectionError
from custom_components.tcl_lyon.const import DOMAIN
from custom_components.tcl_lyon.coordinator import (
    DeparturesCoordinator,
    DisruptionsCoordinator,
)

from .conftest import load_fixture

LINE_REF = "ActIV:Line::T2:SYTRAL"


class FakeClient:
    """Stand-in for TclLyonClient that records calls and returns a fixed payload."""

    def __init__(self, *, payload=None, error=None):
        self._payload = payload
        self._error = error
        self.line_refs: list[str] = []
        self.situation_calls = 0

    async def async_fetch_estimated_timetables(self, line_ref):
        self.line_refs.append(line_ref)
        if self._error is not None:
            raise self._error
        return self._payload

    async def async_fetch_situation_exchange(self):
        self.situation_calls += 1
        if self._error is not None:
            raise self._error
        return self._payload


def _make_coordinator(hass, client, *, line_refs=(LINE_REF,), stop_ids=("32166",)):
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    return DeparturesCoordinator(hass, entry, client, line_refs, stop_ids)


def _make_disruptions_coordinator(hass, client, *, line_refs=(LINE_REF,)):
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    return DisruptionsCoordinator(hass, entry, client, line_refs)


async def test_polls_each_line_and_filters_to_wanted_stop(hass):
    client = FakeClient(payload=load_fixture("estimated_timetables.json"))
    coordinator = _make_coordinator(hass, client)

    data = await coordinator._async_update_data()

    assert client.line_refs == [LINE_REF]
    rows = data[LINE_REF]
    assert rows  # fixture has two calls at 32166
    assert {d.stop_id for d in rows} == {"32166"}


async def test_other_stop_yields_its_own_calls(hass):
    client = FakeClient(payload=load_fixture("estimated_timetables.json"))
    coordinator = _make_coordinator(hass, client, stop_ids=("32168",))

    data = await coordinator._async_update_data()

    rows = data[LINE_REF]
    assert [d.stop_id for d in rows] == ["32168"]


async def test_connection_error_becomes_update_failed(hass):
    client = FakeClient(error=TclLyonConnectionError("down"))
    coordinator = _make_coordinator(hass, client)

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


async def test_auth_error_becomes_config_entry_auth_failed(hass):
    client = FakeClient(error=TclLyonAuthError("401"))
    coordinator = _make_coordinator(hass, client)

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()


async def test_disruptions_polls_once_and_keys_per_line(hass):
    client = FakeClient(payload=load_fixture("situation_exchange.json"))
    # T4 shares the second situation with T2; line 99 is followed but undisrupted.
    coordinator = _make_disruptions_coordinator(
        hass,
        client,
        line_refs=(LINE_REF, "ActIV:Line::T4:SYTRAL", "ActIV:Line::99:SYTRAL"),
    )

    data = await coordinator._async_update_data()

    assert client.situation_calls == 1  # bulk: one request regardless of line count
    assert {d.situation_number for d in data[LINE_REF]} == {"ACTIV_222_1"}
    assert {d.situation_number for d in data["ActIV:Line::T4:SYTRAL"]} == {"ACTIV_222_1"}
    assert data["ActIV:Line::99:SYTRAL"] == []  # followed but no active situation


async def test_disruptions_connection_error_becomes_update_failed(hass):
    client = FakeClient(error=TclLyonConnectionError("down"))
    coordinator = _make_disruptions_coordinator(hass, client)

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


async def test_disruptions_auth_error_becomes_config_entry_auth_failed(hass):
    client = FakeClient(error=TclLyonAuthError("401"))
    coordinator = _make_disruptions_coordinator(hass, client)

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()
