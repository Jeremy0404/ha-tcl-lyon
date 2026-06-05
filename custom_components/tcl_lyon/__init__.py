"""The TCL Lyon integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import TclLyonClient
from .const import DOMAIN, PLATFORMS, V03_DEFAULT_LINE_REF, V03_DEFAULT_STOP_ID
from .coordinator import DeparturesCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up TCL Lyon from a config entry."""
    client = TclLyonClient(
        async_get_clientsession(hass),
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
    )

    # v0.3: a single hardcoded stop/line. v0.4 builds these from the config flow.
    coordinator = DeparturesCoordinator(
        hass,
        entry,
        client,
        line_refs=[V03_DEFAULT_LINE_REF],
        stop_ids=[V03_DEFAULT_STOP_ID],
    )
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
