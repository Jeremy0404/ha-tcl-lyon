"""Data update coordinators for TCL Lyon.

Two coordinators are planned; v0.3 ships the first:

    DeparturesCoordinator   — polls SIRI estimated-timetables per followed line,
                              filters calls to the configured stops client-side.
    DisruptionsCoordinator  — v0.5, bulk situation-exchange every ~5 min.

The per-line polling decision comes from the POC: the server respects ?LineRef=
but ignores ?MonitoringRef=, so stop filtering has to happen here, not on the
wire. See docs/03-poc-findings.md.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    Departure,
    TclLyonAuthError,
    TclLyonClient,
    TclLyonConnectionError,
    parse_departures,
)
from .const import DEFAULT_DEPARTURES_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)


class DeparturesCoordinator(DataUpdateCoordinator[dict[str, list[Departure]]]):
    """Poll estimated-timetables for the followed lines, keyed by SIRI LineRef.

    Each value is the soonest-first list of departures at the configured stops for
    that line. One HTTP request per line; one failure fails the whole poll so all
    entities degrade to "unavailable" together (the feed's ~58% uptime makes a
    blanket unavailable honest — see the plan's graceful-degradation note).
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: TclLyonClient,
        line_refs: Iterable[str],
        stop_ids: Iterable[str],
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} departures",
            update_interval=DEFAULT_DEPARTURES_INTERVAL,
        )
        self._client = client
        self._line_refs = tuple(line_refs)
        self._stop_ids = frozenset(stop_ids)

    async def _async_update_data(self) -> dict[str, list[Departure]]:
        result: dict[str, list[Departure]] = {}
        for line_ref in self._line_refs:
            try:
                payload = await self._client.async_fetch_estimated_timetables(line_ref)
            except TclLyonAuthError as err:
                raise ConfigEntryAuthFailed(str(err)) from err
            except TclLyonConnectionError as err:
                raise UpdateFailed(str(err)) from err
            result[line_ref] = parse_departures(payload, stop_ids=self._stop_ids)
        return result
