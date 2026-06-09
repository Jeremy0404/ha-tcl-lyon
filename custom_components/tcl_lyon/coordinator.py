"""Data update coordinators for TCL Lyon.

Two coordinators:

    DeparturesCoordinator   — polls SIRI estimated-timetables per followed line,
                              filters calls to the configured stops client-side.
    DisruptionsCoordinator  — bulk situation-exchange every ~5 min, filtered to
                              the followed lines and keyed by SIRI LineRef.

The per-line polling decision comes from the POC: the server respects ?LineRef=
but ignores ?MonitoringRef=, so stop filtering has to happen here, not on the
wire. situation-exchange isn't server-filterable at all, hence the bulk poll.
See docs/03-poc-findings.md.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import NoReturn

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    Departure,
    Disruption,
    TclLyonAuthError,
    TclLyonClient,
    TclLyonConnectionError,
    parse_departures,
    parse_situations,
)
from .const import (
    AUTH_FAILURE_THRESHOLD,
    DEFAULT_DEPARTURES_INTERVAL,
    DEFAULT_DISRUPTIONS_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class _AuthFailureTracker:
    """Tolerate transient 401s from the flaky SIRI feed before forcing reauth.

    A stateless Basic-Auth 401 mid-poll is almost always a server blip, not a real
    credential change, so we escalate to ``ConfigEntryAuthFailed`` (which prompts
    the user to re-enter their password) only after several *consecutive* polls
    fail auth. Isolated blips just fail the poll like any other outage; a genuinely
    wrong password 401s every poll and still trips the threshold within minutes.
    """

    _auth_failures: int = 0

    def _on_auth_failure(self, err: TclLyonAuthError) -> NoReturn:
        self._auth_failures += 1
        if self._auth_failures >= AUTH_FAILURE_THRESHOLD:
            raise ConfigEntryAuthFailed(str(err)) from err
        raise UpdateFailed(
            f"auth failed ({self._auth_failures}/{AUTH_FAILURE_THRESHOLD}); "
            "treating as a transient blip"
        ) from err

    def _on_auth_success(self) -> None:
        self._auth_failures = 0


class DeparturesCoordinator(_AuthFailureTracker, DataUpdateCoordinator[dict[str, list[Departure]]]):
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
                self._on_auth_failure(err)
            except TclLyonConnectionError as err:
                raise UpdateFailed(str(err)) from err
            result[line_ref] = parse_departures(payload, stop_ids=self._stop_ids)
        self._on_auth_success()
        return result


class DisruptionsCoordinator(
    _AuthFailureTracker, DataUpdateCoordinator[dict[str, list[Disruption]]]
):
    """Poll situation-exchange in bulk, keyed by followed SIRI LineRef.

    One request every ~5 min (the feed is small and not server-filterable). The
    result maps each followed LineRef to the active disruptions touching it; a
    single disruption can affect several lines, so it lands under each. Lines with
    no active disruption keep an empty list, so every followed line has an entry.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: TclLyonClient,
        line_refs: Iterable[str],
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} disruptions",
            update_interval=DEFAULT_DISRUPTIONS_INTERVAL,
        )
        self._client = client
        self._line_refs = frozenset(line_refs)

    async def _async_update_data(self) -> dict[str, list[Disruption]]:
        try:
            payload = await self._client.async_fetch_situation_exchange()
        except TclLyonAuthError as err:
            self._on_auth_failure(err)
        except TclLyonConnectionError as err:
            raise UpdateFailed(str(err)) from err
        self._on_auth_success()
        result: dict[str, list[Disruption]] = {ref: [] for ref in self._line_refs}
        for disruption in parse_situations(payload, line_refs=self._line_refs):
            for ref in disruption.affected_line_refs:
                if ref in result:
                    result[ref].append(disruption)
        return result
