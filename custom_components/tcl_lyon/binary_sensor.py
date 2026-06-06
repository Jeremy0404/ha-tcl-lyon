"""Binary sensor platform for TCL Lyon.

One binary_sensor per distinct followed line (deduplicated across stops):

    binary_sensor.tcl_line_<line>_disrupted
        is_on = True if any active situation affects this line's SIRI LineRef
        attributes:
          line_ref
          line_color / line_text_color: GTFS line colours ('#'-prefixed) for cards
          disruption_count: number of active situations on the line
          summary: human-readable one-line-per-situation digest (the FR text TCL
                   publishes), for dashboards/notifications without templating
          disruptions: list of {situation_number, description, keywords,
                                 report_type, validity_period}

Backed by DisruptionsCoordinator, which filters situation-exchange.json by
AffectedLine[].LineRef and keys the result per followed line.
"""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import TclLyonData, configured_stops, line_meta
from .api import Disruption, GtfsIndex
from .const import (
    ATTR_DESCRIPTION,
    ATTR_DISRUPTION_COUNT,
    ATTR_DISRUPTIONS,
    ATTR_KEYWORDS,
    ATTR_LINE_COLOR,
    ATTR_LINE_REF,
    ATTR_LINE_TEXT_COLOR,
    ATTR_REPORT_TYPE,
    ATTR_SITUATION_NUMBER,
    ATTR_SUMMARY,
    ATTR_VALIDITY_PERIOD,
    CONF_LINE_ID,
    CONF_LINE_NAME,
    CONF_LINE_REF,
    CONF_LINES,
    DOMAIN,
)
from .coordinator import DisruptionsCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up one binary sensor per distinct followed line."""
    data: TclLyonData = hass.data[DOMAIN][entry.entry_id]
    coordinator = data.disruptions
    entities: list[TclLineDisruptionSensor] = []
    seen: set[str] = set()  # a line followed at several stops -> one disruption sensor
    for stop in configured_stops(entry):
        for line in stop[CONF_LINES]:
            if line[CONF_LINE_REF] in seen:
                continue
            seen.add(line[CONF_LINE_REF])
            entities.append(TclLineDisruptionSensor(coordinator, line, data.index))
    async_add_entities(entities)


class TclLineDisruptionSensor(CoordinatorEntity[DisruptionsCoordinator], BinarySensorEntity):
    """Whether a followed line has any active disruption."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_icon = "mdi:alert"

    def __init__(
        self,
        coordinator: DisruptionsCoordinator,
        line: dict[str, object],
        index: GtfsIndex | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._line_ref = line[CONF_LINE_REF]
        # Icon stays mdi:alert (a "problem"); only the line colours are reused here.
        _, self._line_color, self._line_text_color = line_meta(line, index)
        entry_id = coordinator.config_entry.entry_id
        self._attr_unique_id = f"{entry_id}_line_{line[CONF_LINE_ID]}_disrupted"
        self._attr_name = f"{line[CONF_LINE_NAME]} disruptions"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name="TCL Lyon",
            manufacturer="TCL / SYTRAL",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def is_on(self) -> bool:
        return bool(self._disruptions())

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        disruptions = self._disruptions()
        return {
            ATTR_LINE_REF: self._line_ref,
            ATTR_LINE_COLOR: self._line_color,
            ATTR_LINE_TEXT_COLOR: self._line_text_color,
            ATTR_DISRUPTION_COUNT: len(disruptions),
            ATTR_SUMMARY: _summarize(disruptions),
            ATTR_DISRUPTIONS: [_serialize(d) for d in disruptions],
        }

    def _disruptions(self) -> list[Disruption]:
        return (self.coordinator.data or {}).get(self._line_ref, [])


def _summarize(disruptions: list[Disruption]) -> str | None:
    """One readable line per active disruption, "Keywords — description".

    None when the line is clear, so the attribute stays empty rather than "".
    """
    lines = [line for d in disruptions if (line := _summary_line(d))]
    return "\n".join(lines) or None


def _summary_line(disruption: Disruption) -> str:
    keywords = ", ".join(disruption.keywords)
    description = disruption.description or ""
    if keywords and description:
        return f"{keywords} — {description}"
    return description or keywords


def _serialize(disruption: Disruption) -> dict[str, object]:
    return {
        ATTR_SITUATION_NUMBER: disruption.situation_number,
        ATTR_DESCRIPTION: disruption.description,
        ATTR_KEYWORDS: list(disruption.keywords),
        ATTR_REPORT_TYPE: disruption.report_type,
        ATTR_VALIDITY_PERIOD: [
            {"start": _iso(start), "end": _iso(end)} for start, end in disruption.validity_periods
        ],
    }


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
