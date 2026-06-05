"""The TCL Lyon integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import TclLyonClient
from .const import (
    CONF_LINE_REF,
    CONF_LINES,
    CONF_QUAY_IDS,
    CONF_STOPS,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import DeparturesCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up TCL Lyon from a config entry."""
    client = TclLyonClient(
        async_get_clientsession(hass),
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
    )

    # One poll per distinct line; the union of quays is filtered client-side, then
    # each sensor narrows that line's calls to its own stop (see sensor.py).
    stops = entry.data.get(CONF_STOPS, [])
    line_refs = {line[CONF_LINE_REF] for stop in stops for line in stop[CONF_LINES]}
    quay_ids = {quay for stop in stops for quay in stop[CONF_QUAY_IDS]}

    coordinator = DeparturesCoordinator(
        hass,
        entry,
        client,
        line_refs=line_refs,
        stop_ids=quay_ids,
    )
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unload_ok
