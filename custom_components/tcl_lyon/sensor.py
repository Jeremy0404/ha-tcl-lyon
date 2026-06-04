"""Sensor platform for TCL Lyon.

Scaffolded — entity logic arrives in v0.3.

One sensor per (stop, line, direction) tuple configured by the user:

    sensor.tcl_<stop_slug>_<line>_<direction>
        state = minutes until next passage (int, or "unavailable" if API down)
        attributes:
          next_departures: list of upcoming passes with aimed/expected times
          line_ref, direction, destination, ...

State priority: ExpectedArrivalTime if present, fallback AimedArrivalTime.
See docs/02-data-sources.md for the contract.
"""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up TCL Lyon sensors from a config entry."""
    # v0.3 will iterate the configured stops/lines and create one entity each.
    async_add_entities([])


class TclDepartureSensor(SensorEntity):
    """Sensor exposing the next-passage time at a stop for a given line."""

    # v0.3 — implementation.
