"""Tests for the TCL Lyon config, reauth, and options flows.

Needs Home Assistant, so the whole module skips where it isn't installed. The
client is faked to serve a tiny in-memory GTFS zip (real GtfsIndex parsing), a
fixed estimated-timetables payload for direction discovery, and a controllable
auth probe; no network is touched.
"""

from __future__ import annotations

import io
import zipfile
from unittest.mock import patch

import pytest

# homeassistant.runner imports fcntl → skips on native Windows, runs on Linux/CI.
pytest.importorskip("homeassistant.runner")

from homeassistant.config_entries import SOURCE_REAUTH, SOURCE_USER
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tcl_lyon import config_flow
from custom_components.tcl_lyon.api import TclLyonAuthError, TclLyonConnectionError
from custom_components.tcl_lyon.const import (
    CONF_ADD_ANOTHER,
    CONF_DIRECTION,
    CONF_DIRECTION_NAME,
    CONF_DIRECTIONS,
    CONF_LINE_ID,
    CONF_LINE_NAME,
    CONF_LINE_REF,
    CONF_LINES,
    CONF_QUAY_IDS,
    CONF_QUERY,
    CONF_REMOVE,
    CONF_STOP_ID,
    CONF_STOP_NAME,
    CONF_STOPS,
    DOMAIN,
)

from .conftest import load_fixture

STOPS_CSV = (
    "stop_id,stop_code,stop_name,location_type,parent_station,stop_lat,stop_lon\n"
    "S5484,,Bron Hôtel de Ville,1,,45.7400,4.9100\n"
    "32166,32166,Bron Hôtel de Ville,0,S5484,45.7401,4.9102\n"
    "33219,33219,Saint-Priest Bel Air,0,S7000,45.7000,4.9400\n"
    "S6000,,Cuzin - Picasso,1,,45.7801,4.8801\n"
    "48253,48253,Cuzin - Picasso,0,S6000,45.7800,4.8800\n"
)
ROUTES_CSV = (
    "route_id,agency_id,route_short_name,route_long_name,route_type,route_color,route_text_color\n"
    "T2,1,T2,Montrochet - Saint Priest Bel Air,0,2EB6AC,FFFFFF\n"
    "C3,1,C3,Laurent Bonnevay - Vaulx La Grappinière,11,778186,\n"
)


def _gtfs_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("stops.txt", STOPS_CSV.encode("utf-8"))
        archive.writestr("routes.txt", ROUTES_CSV.encode("utf-8"))
    return buffer.getvalue()


GTFS_BYTES = _gtfs_bytes()

# T2 target as the flow stores it once "outbound" is picked at S5484.
T2_OUTBOUND = {
    CONF_LINE_REF: "ActIV:Line::T2:SYTRAL",
    CONF_LINE_ID: "T2",
    CONF_LINE_NAME: "T2",
    CONF_DIRECTION: "outbound",
    CONF_DIRECTION_NAME: "Saint-Priest Bel Air",
}


def _patch_client(monkeypatch, *, validate_exc=None, download_exc=None):
    class FakeClient:
        def __init__(self, session, username, password):
            self.username = username
            self.password = password

        async def async_validate_credentials(self):
            if validate_exc is not None:
                raise validate_exc

        async def async_download_gtfs_bytes(self):
            if download_exc is not None:
                raise download_exc
            return GTFS_BYTES

        async def async_fetch_estimated_timetables(self, line_ref):
            return load_fixture("estimated_timetables.json")

    monkeypatch.setattr(config_flow, "TclLyonClient", FakeClient)


@pytest.fixture(autouse=True)
def _isolate_flow():
    """Keep these tests on the flow only: skip real entry setup and the aiohttp
    session (the faked client ignores it, but creating one leaks a cleanup thread)."""
    with (
        patch("custom_components.tcl_lyon.async_setup_entry", return_value=True),
        patch("custom_components.tcl_lyon.config_flow.async_get_clientsession", return_value=None),
    ):
        yield


async def _init_user(hass):
    return await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})


async def _cfg(hass, result, user_input):
    return await hass.config_entries.flow.async_configure(result["flow_id"], user_input)


async def _opt(hass, result, user_input):
    return await hass.config_entries.options.async_configure(result["flow_id"], user_input)


async def _walk_to_direction(hass, result, *, stop_query, stop_id, line_query, lines):
    """Drive stop → pick_stop → line → pick_line, landing on the direction step."""
    result = await _cfg(hass, result, {CONF_QUERY: stop_query})
    result = await _cfg(hass, result, {CONF_STOP_ID: stop_id})
    result = await _cfg(hass, result, {CONF_QUERY: line_query})
    return await _cfg(hass, result, {CONF_LINES: lines})


async def test_full_flow_with_direction(hass, monkeypatch):
    _patch_client(monkeypatch)

    result = await _init_user(hass)
    assert result["step_id"] == "user"
    result = await _cfg(hass, result, {CONF_USERNAME: "me@example.com", CONF_PASSWORD: "pw"})
    assert result["step_id"] == "stop"

    result = await _walk_to_direction(
        hass, result, stop_query="hotel", stop_id="S5484", line_query="t2", lines=["T2"]
    )
    assert result["step_id"] == "direction"

    # "T2|outbound" is only a valid choice because discovery resolved it from the
    # live poll; the stored direction_name ("Saint-Priest Bel Air") proves the
    # terminus was looked up from GTFS.
    result = await _cfg(hass, result, {CONF_DIRECTIONS: ["T2|outbound"], CONF_ADD_ANOTHER: False})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_STOPS] == [
        {
            CONF_STOP_ID: "S5484",
            CONF_STOP_NAME: "Bron Hôtel de Ville",
            CONF_QUAY_IDS: ["32166"],
            CONF_LINES: [T2_OUTBOUND],
        }
    ]


async def test_add_another_stop_loops_and_falls_back_to_all(hass, monkeypatch):
    _patch_client(monkeypatch)

    result = await _init_user(hass)
    result = await _cfg(hass, result, {CONF_USERNAME: "me@example.com", CONF_PASSWORD: "pw"})
    result = await _walk_to_direction(
        hass, result, stop_query="hotel", stop_id="S5484", line_query="t2", lines=["T2"]
    )
    result = await _cfg(hass, result, {CONF_DIRECTIONS: ["T2|outbound"], CONF_ADD_ANOTHER: True})
    assert result["step_id"] == "stop"

    # C3 has no calls at S6000's quay in the fixture → only the "all directions" fallback.
    result = await _walk_to_direction(
        hass, result, stop_query="cuzin", stop_id="S6000", line_query="c3", lines=["C3"]
    )
    assert result["step_id"] == "direction"
    result = await _cfg(hass, result, {CONF_DIRECTIONS: ["C3|all"], CONF_ADD_ANOTHER: False})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    stops = result["data"][CONF_STOPS]
    assert [s[CONF_STOP_ID] for s in stops] == ["S5484", "S6000"]
    assert stops[1][CONF_LINES][0][CONF_DIRECTION] is None


async def test_invalid_auth_shows_error(hass, monkeypatch):
    _patch_client(monkeypatch, validate_exc=TclLyonAuthError("401"))

    result = await _init_user(hass)
    result = await _cfg(hass, result, {CONF_USERNAME: "me@example.com", CONF_PASSWORD: "bad"})

    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "invalid_auth"}


async def test_cannot_connect_shows_error(hass, monkeypatch):
    _patch_client(monkeypatch, validate_exc=TclLyonConnectionError("down"))

    result = await _init_user(hass)
    result = await _cfg(hass, result, {CONF_USERNAME: "me@example.com", CONF_PASSWORD: "pw"})

    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "cannot_connect"}


async def test_no_stop_match_shows_error(hass, monkeypatch):
    _patch_client(monkeypatch)

    result = await _init_user(hass)
    result = await _cfg(hass, result, {CONF_USERNAME: "me@example.com", CONF_PASSWORD: "pw"})
    result = await _cfg(hass, result, {CONF_QUERY: "nowhere"})

    assert result["step_id"] == "stop"
    assert result["errors"] == {"base": "no_results"}


async def test_already_configured_aborts(hass, monkeypatch):
    _patch_client(monkeypatch)
    MockConfigEntry(domain=DOMAIN, unique_id="me@example.com", data={}).add_to_hass(hass)

    result = await _init_user(hass)
    result = await _cfg(hass, result, {CONF_USERNAME: "me@example.com", CONF_PASSWORD: "pw"})

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_updates_password(hass, monkeypatch):
    _patch_client(monkeypatch)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="me@example.com",
        data={CONF_USERNAME: "me@example.com", CONF_PASSWORD: "old", CONF_STOPS: []},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id},
        data=entry.data,
    )
    assert result["step_id"] == "reauth_confirm"

    result = await _cfg(hass, result, {CONF_PASSWORD: "new"})
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == "new"


def _entry_with_targets(hass, lines):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="me@example.com",
        data={
            CONF_USERNAME: "me@example.com",
            CONF_PASSWORD: "pw",
            CONF_STOPS: [
                {
                    CONF_STOP_ID: "S5484",
                    CONF_STOP_NAME: "Bron Hôtel de Ville",
                    CONF_QUAY_IDS: ["32166"],
                    CONF_LINES: lines,
                }
            ],
        },
    )
    entry.add_to_hass(hass)
    return entry


async def test_options_add_target(hass, monkeypatch):
    _patch_client(monkeypatch)
    entry = _entry_with_targets(hass, lines=[])  # start with nothing followed

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU

    result = await _opt(hass, result, {"next_step_id": "add_stop"})
    assert result["step_id"] == "stop"
    result = await _opt(hass, result, {CONF_QUERY: "hotel"})
    result = await _opt(hass, result, {CONF_STOP_ID: "S5484"})
    result = await _opt(hass, result, {CONF_QUERY: "t2"})
    result = await _opt(hass, result, {CONF_LINES: ["T2"]})
    result = await _opt(hass, result, {CONF_DIRECTIONS: ["T2|outbound"], CONF_ADD_ANOTHER: False})
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    stops = entry.options[CONF_STOPS]
    assert len(stops) == 1
    assert stops[0][CONF_LINES] == [T2_OUTBOUND]


async def test_options_remove_target(hass, monkeypatch):
    _patch_client(monkeypatch)
    inbound = {**T2_OUTBOUND, CONF_DIRECTION: "inbound", CONF_DIRECTION_NAME: "Bron Hôtel de Ville"}
    entry = _entry_with_targets(hass, lines=[T2_OUTBOUND, inbound])

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await _opt(hass, result, {"next_step_id": "remove_target"})
    assert result["step_id"] == "remove_target"

    # "S5484|T2|inbound" is only accepted if it was offered as a current target.
    result = await _opt(hass, result, {CONF_REMOVE: ["S5484|T2|inbound"]})
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    stops = entry.options[CONF_STOPS]
    assert len(stops) == 1
    assert stops[0][CONF_LINES] == [T2_OUTBOUND]


async def test_options_remove_nothing_configured_aborts(hass, monkeypatch):
    _patch_client(monkeypatch)
    entry = _entry_with_targets(hass, lines=[])

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await _opt(hass, result, {"next_step_id": "remove_target"})

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "nothing_to_remove"
