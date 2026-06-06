"""The TCL Lyon integration."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval

from .api import TclLyonClient
from .const import (
    CONF_LINE_REF,
    CONF_LINES,
    CONF_QUAY_IDS,
    CONF_STOPS,
    DOMAIN,
    GTFS_REFRESH_INTERVAL,
    PLATFORMS,
)
from .coordinator import DeparturesCoordinator, DisruptionsCoordinator
from .store import async_load_available_index, async_refresh_index, index_is_stale

_LOGGER = logging.getLogger(__name__)

# Guards the weekly GTFS index refresh so multiple entries don't all download at once.
INDEX_REFRESH_LOCK = f"{DOMAIN}_index_refresh_lock"


@dataclass
class TclLyonData:
    """The two coordinators backing an entry's entities, stored in hass.data."""

    departures: DeparturesCoordinator
    disruptions: DisruptionsCoordinator


def configured_stops(entry: ConfigEntry) -> list[dict[str, Any]]:
    """The followed stop/line/direction targets.

    Prefers ``entry.options`` (written by the options flow, authoritative once set —
    including an empty list when the user removes everything) over ``entry.data``
    (written by the initial config flow).
    """
    if CONF_STOPS in entry.options:
        return entry.options[CONF_STOPS]
    return entry.data.get(CONF_STOPS, [])


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up TCL Lyon from a config entry."""
    entry.async_on_unload(entry.add_update_listener(_async_reload_on_update))

    client = TclLyonClient(
        async_get_clientsession(hass),
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
    )

    # One poll per distinct line; the union of quays is filtered client-side, then
    # each sensor narrows that line's calls to its own stop + direction (see sensor.py).
    stops = configured_stops(entry)
    line_refs = {line[CONF_LINE_REF] for stop in stops for line in stop[CONF_LINES]}
    quay_ids = {quay for stop in stops for quay in stop[CONF_QUAY_IDS]}

    departures = DeparturesCoordinator(
        hass,
        entry,
        client,
        line_refs=line_refs,
        stop_ids=quay_ids,
    )
    await departures.async_config_entry_first_refresh()

    # Disruptions are secondary; refresh best-effort so a situation-exchange outage
    # (the feed's ~58% uptime) doesn't block setup. Auth/readiness is gated above.
    disruptions = DisruptionsCoordinator(hass, entry, client, line_refs=line_refs)
    await disruptions.async_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = TclLyonData(departures, disruptions)

    _async_setup_index_refresh(hass, entry, client)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


def _async_setup_index_refresh(
    hass: HomeAssistant, entry: ConfigEntry, client: TclLyonClient
) -> None:
    """Keep the cached GTFS stop→lines index fresh in the background.

    The index only feeds the config/options pickers, so refreshing it never blocks
    setup or entities: a stale or missing cache triggers one catch-up refresh now,
    then a weekly timer takes over. The shared lock + freshness recheck mean a
    second entry won't re-download what another just fetched.
    """

    async def _refresh(_now: datetime | None = None) -> None:
        lock = hass.data.setdefault(INDEX_REFRESH_LOCK, asyncio.Lock())
        async with lock:
            # Weigh the shipped file's age too: a fresh prebuilt index needs no refresh.
            if not index_is_stale(await async_load_available_index(hass)):
                return
            await async_refresh_index(hass, client)

    entry.async_create_background_task(hass, _refresh(), "tcl_lyon_gtfs_index_refresh")
    entry.async_on_unload(async_track_time_interval(hass, _refresh, GTFS_REFRESH_INTERVAL))


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unload_ok


async def _async_reload_on_update(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Rebuild entities when the options flow edits the followed targets."""
    await hass.config_entries.async_reload(entry.entry_id)
