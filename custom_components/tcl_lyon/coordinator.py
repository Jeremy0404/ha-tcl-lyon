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
import time
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
    AUTH_FAILURE_GRACE_PERIOD,
    AUTH_FAILURE_STREAK_TIMEOUT,
    DEFAULT_DEPARTURES_INTERVAL,
    DEFAULT_DISRUPTIONS_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

_GRACE_PERIOD = AUTH_FAILURE_GRACE_PERIOD.total_seconds()
_STREAK_TIMEOUT = AUTH_FAILURE_STREAK_TIMEOUT.total_seconds()


class AuthFailureTracker:
    """Tolerate transient 401s from the flaky SIRI feed before forcing reauth.

    A stateless Basic-Auth 401 mid-poll is almost always a server blip, not a real
    credential change, so we escalate to ``ConfigEntryAuthFailed`` (which prompts
    the user to re-enter their password) only once auth has been failing for
    ``AUTH_FAILURE_GRACE_PERIOD`` with no successful poll in between. Counting
    failures instead would eventually prompt on a healthy entry: on a feed with
    this much downtime, isolated blips days apart still add up to any threshold.

    One instance is shared by both coordinators of an entry, since they share the
    one credential — a success on either clears the other's suspicion. It is
    rebuilt on reload, which only ever delays escalation, never causes it.

    Elapsed time comes from ``time.monotonic`` so an NTP correction on the host
    can't fast-forward a streak into a spurious prompt.
    """

    def __init__(self) -> None:
        self._failing_since: float | None = None
        self._last_failure: float | None = None

    def on_auth_failure(self, err: TclLyonAuthError) -> NoReturn:
        """Record a 401 and either ride it out or escalate to reauth."""
        now = time.monotonic()
        # A gap this long means the feed, not the credential, was the problem in
        # between — treat this 401 as the start of a fresh streak.
        if self._last_failure is None or now - self._last_failure > _STREAK_TIMEOUT:
            self._failing_since = now
        self._last_failure = now

        failing_for = now - self._failing_since
        if failing_for >= _GRACE_PERIOD:
            raise ConfigEntryAuthFailed(str(err)) from err
        raise UpdateFailed(
            f"auth failed for {failing_for:.0f}s of the "
            f"{_GRACE_PERIOD:.0f}s grace period; treating as a transient blip"
        ) from err

    def on_success(self) -> None:
        """A poll got through, so the credential is fine — drop any streak."""
        self._failing_since = None
        self._last_failure = None


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
        auth_tracker: AuthFailureTracker,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} departures",
            update_interval=DEFAULT_DEPARTURES_INTERVAL,
        )
        self._client = client
        self._auth = auth_tracker
        self._line_refs = tuple(line_refs)
        self._stop_ids = frozenset(stop_ids)

    async def _async_update_data(self) -> dict[str, list[Departure]]:
        result: dict[str, list[Departure]] = {}
        for line_ref in self._line_refs:
            try:
                payload = await self._client.async_fetch_estimated_timetables(line_ref)
            except TclLyonAuthError as err:
                self._auth.on_auth_failure(err)
            except TclLyonConnectionError as err:
                raise UpdateFailed(str(err)) from err
            result[line_ref] = parse_departures(payload, stop_ids=self._stop_ids)
        self._auth.on_success()
        return result


class DisruptionsCoordinator(DataUpdateCoordinator[dict[str, list[Disruption]]]):
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
        auth_tracker: AuthFailureTracker,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} disruptions",
            update_interval=DEFAULT_DISRUPTIONS_INTERVAL,
        )
        self._client = client
        self._auth = auth_tracker
        self._line_refs = frozenset(line_refs)

    async def _async_update_data(self) -> dict[str, list[Disruption]]:
        try:
            payload = await self._client.async_fetch_situation_exchange()
        except TclLyonAuthError as err:
            self._auth.on_auth_failure(err)
        except TclLyonConnectionError as err:
            raise UpdateFailed(str(err)) from err
        self._auth.on_success()
        result: dict[str, list[Disruption]] = {ref: [] for ref in self._line_refs}
        for disruption in parse_situations(payload, line_refs=self._line_refs):
            for ref in disruption.affected_line_refs:
                if ref in result:
                    result[ref].append(disruption)
        return result
