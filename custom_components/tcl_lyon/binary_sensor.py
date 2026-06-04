"""Binary sensor platform for TCL Lyon.

Scaffolded — entity logic arrives in v0.5.

One binary_sensor per followed line:

    binary_sensor.tcl_line_<line>_disrupted
        is_on = True if any active situation affects this LineRef
        attributes:
          disruptions: list of {description, validity_period, situation_number}

Filtered from situation-exchange.json by AffectedLine[].LineRef.
"""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up TCL Lyon binary sensors from a config entry."""
    # v0.5 — iterate configured lines, one binary_sensor each.
    async_add_entities([])


class TclLineDisruptionSensor(BinarySensorEntity):
    """Binary sensor indicating whether a line has active disruptions."""

    # v0.5 — implementation.
