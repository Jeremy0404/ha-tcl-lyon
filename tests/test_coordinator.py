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
from custom_components.tcl_lyon.const import (
    AUTH_FAILURE_GRACE_PERIOD,
    AUTH_FAILURE_STREAK_TIMEOUT,
    DOMAIN,
)
from custom_components.tcl_lyon.coordinator import (
    AuthFailureTracker,
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


def _make_coordinator(hass, client, *, line_refs=(LINE_REF,), stop_ids=("32166",), tracker=None):
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    return DeparturesCoordinator(
        hass, entry, client, line_refs, stop_ids, tracker or AuthFailureTracker()
    )


def _make_disruptions_coordinator(hass, client, *, line_refs=(LINE_REF,), tracker=None):
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    return DisruptionsCoordinator(hass, entry, client, line_refs, tracker or AuthFailureTracker())


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


async def test_auth_error_rides_out_the_grace_period(hass, freezer):
    client = FakeClient(error=TclLyonAuthError("401"))
    coordinator = _make_coordinator(hass, client)

    # Inside the grace period a 401 degrades like any outage — no reauth prompt.
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()
    freezer.tick(AUTH_FAILURE_GRACE_PERIOD / 2)
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()

    # Once auth has been failing for the whole grace period, escalate to reauth.
    freezer.tick(AUTH_FAILURE_GRACE_PERIOD / 2)
    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()


async def test_successful_poll_clears_the_streak(hass, freezer):
    client = FakeClient(payload=load_fixture("estimated_timetables.json"))
    coordinator = _make_coordinator(hass, client)

    client._error = TclLyonAuthError("401")
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()
    client._error = None
    await coordinator._async_update_data()

    # The good poll reset the clock, so the old streak can't carry the new 401
    # past the grace period.
    freezer.tick(AUTH_FAILURE_GRACE_PERIOD)
    client._error = TclLyonAuthError("401")
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


async def test_connection_errors_do_not_clear_the_streak(hass, freezer):
    """A wrong password still escalates when the flaky feed interleaves outages."""
    client = FakeClient(error=TclLyonAuthError("401"))
    coordinator = _make_coordinator(hass, client)

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()
    # An outage proves nothing about the credential, so it neither clears the
    # streak (only a successful poll does) nor counts towards it.
    freezer.tick(AUTH_FAILURE_GRACE_PERIOD / 2)
    client._error = TclLyonConnectionError("down")
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()

    freezer.tick(AUTH_FAILURE_GRACE_PERIOD / 2)
    client._error = TclLyonAuthError("401")
    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()


async def test_long_outage_starts_a_new_streak(hass, freezer):
    """Two 401s this far apart are unrelated blips, not one long auth failure."""
    client = FakeClient(error=TclLyonAuthError("401"))
    coordinator = _make_coordinator(hass, client)

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()

    client._error = TclLyonConnectionError("down")
    for _ in range(3):
        freezer.tick(AUTH_FAILURE_STREAK_TIMEOUT / 2)
        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

    # Nothing succeeded in between, but the gap is too wide to treat as one
    # streak — without this the entry would prompt for a password it never lost.
    client._error = TclLyonAuthError("401")
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


async def test_tracker_is_shared_between_coordinators(hass, freezer):
    """One credential, one streak: neither coordinator prompts on its own evidence."""
    tracker = AuthFailureTracker()
    departures_client = FakeClient(payload=load_fixture("estimated_timetables.json"))
    departures = _make_coordinator(hass, departures_client, tracker=tracker)
    disruptions = _make_disruptions_coordinator(
        hass, FakeClient(error=TclLyonAuthError("401")), tracker=tracker
    )

    # Disruptions 401s across the whole grace period while departures polls fine,
    # so the credential is demonstrably good and no reauth fires.
    with pytest.raises(UpdateFailed):
        await disruptions._async_update_data()
    freezer.tick(AUTH_FAILURE_GRACE_PERIOD)
    await departures._async_update_data()
    with pytest.raises(UpdateFailed):
        await disruptions._async_update_data()

    # Once departures 401s too, the shared streak escalates for both.
    departures_client._error = TclLyonAuthError("401")
    with pytest.raises(UpdateFailed):
        await departures._async_update_data()
    freezer.tick(AUTH_FAILURE_GRACE_PERIOD)
    with pytest.raises(ConfigEntryAuthFailed):
        await disruptions._async_update_data()


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


async def test_disruptions_auth_error_rides_out_the_grace_period(hass, freezer):
    client = FakeClient(error=TclLyonAuthError("401"))
    coordinator = _make_disruptions_coordinator(hass, client)

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()
    freezer.tick(AUTH_FAILURE_GRACE_PERIOD)
    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()
