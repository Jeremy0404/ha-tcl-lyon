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

from .api import GtfsIndex, TclLyonClient
from .const import (
    CONF_LINE_COLOR,
    CONF_LINE_ID,
    CONF_LINE_REF,
    CONF_LINE_TEXT_COLOR,
    CONF_LINES,
    CONF_QUAY_IDS,
    CONF_ROUTE_TYPE,
    CONF_STOPS,
    DEFAULT_LINE_ICON,
    DOMAIN,
    GTFS_REFRESH_INTERVAL,
    PLATFORMS,
    ROUTE_TYPE_ICONS,
)
from .coordinator import AuthFailureTracker, DeparturesCoordinator, DisruptionsCoordinator
from .store import async_load_available_index, async_refresh_index, index_is_stale

_LOGGER = logging.getLogger(__name__)

# Guards the weekly GTFS index refresh so multiple entries don't all download at once.
INDEX_REFRESH_LOCK = f"{DOMAIN}_index_refresh_lock"
# Auth-failure trackers, kept per entry outside the entry's own hass.data slot so
# they outlive a failed setup — see _auth_tracker.
AUTH_TRACKERS = f"{DOMAIN}_auth_trackers"


@dataclass
class TclLyonData:
    """The two coordinators backing an entry's entities, stored in hass.data.

    ``index`` is loaded only to backfill line icon/colour for entries configured
    before those fields were stored; it stays None when every target already has them.
    """

    departures: DeparturesCoordinator
    disruptions: DisruptionsCoordinator
    index: GtfsIndex | None = None


def configured_stops(entry: ConfigEntry) -> list[dict[str, Any]]:
    """The followed stop/line/direction targets.

    Prefers ``entry.options`` (written by the options flow, authoritative once set —
    including an empty list when the user removes everything) over ``entry.data``
    (written by the initial config flow).
    """
    if CONF_STOPS in entry.options:
        return entry.options[CONF_STOPS]
    return entry.data.get(CONF_STOPS, [])


def line_meta(
    target: dict[str, Any], index: GtfsIndex | None
) -> tuple[str, str | None, str | None]:
    """(icon, line_color, text_color) for a line target — hex colours '#'-prefixed.

    Prefers the values stored on the target at pick_line; falls back to the GTFS
    index for older entries that predate them, then to a neutral default.
    """
    route_type = target.get(CONF_ROUTE_TYPE)
    color = target.get(CONF_LINE_COLOR)
    text_color = target.get(CONF_LINE_TEXT_COLOR)
    if route_type is None and index is not None:
        route = index.routes.get(target[CONF_LINE_ID])
        if route is not None:
            route_type, color, text_color = route.route_type, route.color, route.text_color
    icon = ROUTE_TYPE_ICONS.get(route_type, DEFAULT_LINE_ICON)
    return icon, _hex(color), _hex(text_color)


def _hex(color: str | None) -> str | None:
    """GTFS route colours are bare hex ('E2231A'); prefix '#' for dashboards."""
    return f"#{color}" if color else None


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

    auth_tracker = _auth_tracker(hass, entry)

    departures = DeparturesCoordinator(
        hass,
        entry,
        client,
        line_refs=line_refs,
        stop_ids=quay_ids,
        auth_tracker=auth_tracker,
    )
    await departures.async_config_entry_first_refresh()

    # Disruptions are secondary; refresh best-effort so a situation-exchange outage
    # (the feed's ~58% uptime) doesn't block setup. Auth/readiness is gated above.
    disruptions = DisruptionsCoordinator(
        hass, entry, client, line_refs=line_refs, auth_tracker=auth_tracker
    )
    await disruptions.async_refresh()

    # Only entries predating the stored icon/colour fields need the index to backfill.
    index = None
    if any(CONF_ROUTE_TYPE not in line for stop in stops for line in stop[CONF_LINES]):
        index = await async_load_available_index(hass)

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = TclLyonData(departures, disruptions, index)

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


def _auth_tracker(hass: HomeAssistant, entry: ConfigEntry) -> AuthFailureTracker:
    """The entry's auth-failure tracker, shared by its two coordinators.

    One tracker per entry, not per coordinator: both poll with the same credential,
    so a success on either is evidence for both. It is kept across setup *retries*
    (a ``ConfigEntryNotReady`` never unloads the entry) — a tracker rebuilt on every
    retry could never reach the grace period, so a password that went wrong while
    HA was down would retry forever instead of prompting. A real unload does drop
    it, which is what reauth and the options flow want: the credential just changed.
    """
    trackers: dict[str, AuthFailureTracker] = hass.data.setdefault(AUTH_TRACKERS, {})
    return trackers.setdefault(entry.entry_id, AuthFailureTracker())


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
        hass.data.get(AUTH_TRACKERS, {}).pop(entry.entry_id, None)
    return unload_ok


async def _async_reload_on_update(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Rebuild entities when the options flow edits the followed targets."""
    await hass.config_entries.async_reload(entry.entry_id)
