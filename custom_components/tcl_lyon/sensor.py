"""Sensor platform for TCL Lyon.

One sensor per (stop, line) the user follows:

    sensor.tcl_<line>_<stop>
        state = whole minutes until the next passage (None when none is known;
                "unavailable" when the poll fails — handled by CoordinatorEntity)
        attributes:
          line_ref
          next_departures: upcoming passes with aimed/expected times, realtime
                           flag, cancellation flag and minutes-to-go

State priority is realtime (Expected) over scheduled (Aimed), already resolved by
Departure.time. See docs/02-data-sources.md for the contract.
"""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .api import Departure, parse_ref
from .const import (
    ATTR_AIMED_TIME,
    ATTR_CANCELLED,
    ATTR_DESTINATION,
    ATTR_DIRECTION,
    ATTR_EXPECTED_TIME,
    ATTR_IS_REALTIME,
    ATTR_LINE_REF,
    ATTR_MINUTES,
    ATTR_NEXT_DEPARTURES,
    DOMAIN,
    MAX_DEPARTURES,
    V03_DEFAULT_LINE_REF,
    V03_DEFAULT_STOP_ID,
)
from .coordinator import DeparturesCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up TCL Lyon sensors from a config entry."""
    coordinator: DeparturesCoordinator = hass.data[DOMAIN][entry.entry_id]
    # v0.3: one sensor for the hardcoded pair. v0.4 iterates the configured stops.
    async_add_entities([TclDepartureSensor(coordinator, V03_DEFAULT_LINE_REF, V03_DEFAULT_STOP_ID)])


class TclDepartureSensor(CoordinatorEntity[DeparturesCoordinator], SensorEntity):
    """Minutes until the next passage of a line at a stop."""

    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_icon = "mdi:tram"

    def __init__(self, coordinator: DeparturesCoordinator, line_ref: str, stop_id: str) -> None:
        super().__init__(coordinator)
        self._line_ref = line_ref
        self._stop_id = stop_id
        line_id = parse_ref(line_ref) or line_ref
        entry_id = coordinator.config_entry.entry_id
        self._attr_unique_id = f"{entry_id}_{line_id}_{stop_id}"
        self._attr_name = f"TCL {line_id} @ {stop_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name="TCL Lyon",
            manufacturer="TCL / SYTRAL",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def native_value(self) -> int | None:
        now = dt_util.utcnow()
        nxt = self._next_departure(now)
        if nxt is None or nxt.time is None:
            return None
        return _minutes_until(nxt.time, now)

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        now = dt_util.utcnow()
        return {
            ATTR_LINE_REF: self._line_ref,
            ATTR_NEXT_DEPARTURES: [
                _serialize(departure, now) for departure in self._stop_departures()[:MAX_DEPARTURES]
            ],
        }

    def _stop_departures(self) -> list[Departure]:
        """This stop's departures, soonest-first (the coordinator already sorts)."""
        rows = (self.coordinator.data or {}).get(self._line_ref, [])
        return [d for d in rows if d.stop_id == self._stop_id]

    def _next_departure(self, now: datetime) -> Departure | None:
        """Soonest non-cancelled departure still in the future."""
        for departure in self._stop_departures():
            if departure.cancelled or departure.time is None:
                continue
            if departure.time >= now:
                return departure
        return None


def _minutes_until(when: datetime, now: datetime) -> int:
    return max(0, round((when - now).total_seconds() / 60))


def _serialize(departure: Departure, now: datetime) -> dict[str, object]:
    when = departure.time
    return {
        ATTR_DIRECTION: departure.direction,
        ATTR_DESTINATION: departure.destination_id,
        ATTR_AIMED_TIME: departure.aimed.isoformat() if departure.aimed else None,
        ATTR_EXPECTED_TIME: departure.expected.isoformat() if departure.expected else None,
        ATTR_IS_REALTIME: departure.is_realtime,
        ATTR_CANCELLED: departure.cancelled,
        ATTR_MINUTES: None if when is None else _minutes_until(when, now),
    }
