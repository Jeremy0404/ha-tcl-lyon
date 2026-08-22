"""Client for the data.grandlyon.com SIRI Lite API and GTFS feed.

Thin async transport layer over aiohttp. The interesting, well-tested logic lives
in the pure modules re-exported here:

  - :mod:`.siri` — parse estimated-timetables / situation-exchange payloads
  - :mod:`.gtfs` — load the static GTFS stop/route index

so importing ``tcl_lyon.api`` gives the whole "client + loader" surface the
architecture doc describes, while keeping the parsers HA/aiohttp-free and unit
testable offline. See docs/02-data-sources.md for the API contracts.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

import aiohttp

from .const import (
    GTFS_DOWNLOAD_URL,
    SIRI_ESTIMATED_TIMETABLES_URL,
    SIRI_SITUATION_EXCHANGE_URL,
)
from .gtfs import GtfsError, GtfsIndex, Route, Stop
from .siri import (
    Departure,
    Disruption,
    build_line_ref,
    parse_departures,
    parse_ref,
    parse_situations,
    parse_time,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_LOGGER = logging.getLogger(__name__)

__all__ = [
    "Departure",
    "Disruption",
    "GtfsError",
    "GtfsIndex",
    "Route",
    "Stop",
    "TclLyonAuthError",
    "TclLyonClient",
    "TclLyonConnectionError",
    "TclLyonError",
    "build_line_ref",
    "parse_departures",
    "parse_ref",
    "parse_situations",
    "parse_time",
]

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)
GTFS_TIMEOUT = aiohttp.ClientTimeout(total=120)

# Smooth over the SIRI feed's ~58% uptime: retry transient failures (timeouts,
# dropped connections, 5xx/429, garbled JSON) with exponential backoff before a
# poll gives up, so a single blip doesn't flap every entity to "unavailable".
# Auth (401) and permanent 4xx fail fast — retrying them is pointless.
REQUEST_MAX_ATTEMPTS = 3
RETRY_BACKOFF_BASE_SECONDS = 1.0


class TclLyonError(Exception):
    """Base error for the TCL Lyon client."""


class TclLyonAuthError(TclLyonError):
    """Credentials rejected (HTTP 401) — usually the data password isn't set yet."""


class TclLyonConnectionError(TclLyonError):
    """The endpoint could not be reached, timed out, or returned a server error.

    ``retryable`` marks transient failures worth retrying (timeouts, dropped
    connections, 5xx/429, garbled JSON). A permanent 4xx sets it ``False``.
    """

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class TclLyonClient:
    """Async client for the SIRI Lite endpoints and the GTFS download.

    Auth is the user's personal GrandLyon Basic-Auth (email + *data* password).
    Methods return raw decoded payloads; pair them with :func:`.parse_departures`
    / :func:`.parse_situations` to get structured rows.
    """

    def __init__(self, session: aiohttp.ClientSession, username: str, password: str) -> None:
        self._session = session
        self._auth = aiohttp.BasicAuth(username, password)

    async def async_fetch_estimated_timetables(self, line_ref: str) -> dict[str, Any]:
        """Raw estimated-timetables payload for one line (server honours ?LineRef=)."""
        return await self._get_json(
            SIRI_ESTIMATED_TIMETABLES_URL, params={"LineRef": line_ref}, retry_auth=True
        )

    async def async_fetch_situation_exchange(self) -> dict[str, Any]:
        """Raw situation-exchange payload (bulk — the feed isn't server-filterable)."""
        return await self._get_json(SIRI_SITUATION_EXCHANGE_URL, retry_auth=True)

    async def async_validate_credentials(self) -> None:
        """Probe auth against situation-exchange.

        Returns ``None`` on success; raises :class:`TclLyonAuthError` on 401 (the
        classic "data password not set" trap) or :class:`TclLyonConnectionError`
        if the endpoint is simply down.
        """
        await self._get_json(SIRI_SITUATION_EXCHANGE_URL)

    async def async_download_gtfs_bytes(self) -> bytes:
        """Return the GTFS zip body in memory (same Basic Auth).

        The config flow only needs stops.txt + routes.txt out of it, so it parses
        the bytes with :meth:`GtfsIndex.from_bytes` rather than touching disk.
        """
        async with self._request(GTFS_DOWNLOAD_URL, timeout=GTFS_TIMEOUT) as response:
            return await response.read()

    async def async_download_gtfs(self, dest: str | Path) -> Path:
        """Download the GTFS zip to ``dest`` (same Basic Auth). Returns the path.

        Reads the ~20 MB body into memory then writes it; the write is blocking, so
        callers inside HA should run this via ``hass.async_add_executor_job``.
        """
        destination = Path(dest)
        destination.write_bytes(await self.async_download_gtfs_bytes())
        return destination

    async def _get_json(
        self, url: str, *, params: dict[str, str] | None = None, retry_auth: bool = False
    ) -> dict[str, Any]:
        """GET JSON, retrying transient failures with exponential backoff.

        Basic Auth is stateless, so a 401 amid otherwise-working polls is almost
        always a server-side blip on this flaky feed rather than a real credential
        change. Schedule-driven callers set ``retry_auth`` to ride those out; the
        config-flow probe leaves it off to fail fast on a genuinely wrong password.
        """
        for attempt in range(1, REQUEST_MAX_ATTEMPTS + 1):
            try:
                return await self._get_json_once(url, params=params)
            except TclLyonError as err:
                transient = (
                    err.retryable
                    if isinstance(err, TclLyonConnectionError)
                    else retry_auth and isinstance(err, TclLyonAuthError)
                )
                if not transient or attempt == REQUEST_MAX_ATTEMPTS:
                    raise
                delay = RETRY_BACKOFF_BASE_SECONDS * 2 ** (attempt - 1)
                _LOGGER.debug(
                    "%s failed (%s); retrying in %.1fs (attempt %d/%d)",
                    url,
                    err,
                    delay,
                    attempt,
                    REQUEST_MAX_ATTEMPTS,
                )
                await asyncio.sleep(delay)
        raise AssertionError("unreachable")  # the loop returns or raises

    async def _get_json_once(
        self, url: str, *, params: dict[str, str] | None = None
    ) -> dict[str, Any]:
        async with self._request(url, params=params, timeout=REQUEST_TIMEOUT) as response:
            try:
                # content_type=None: the endpoint sometimes mislabels JSON as text.
                return await response.json(content_type=None)
            except (aiohttp.ClientError, ValueError) as err:
                raise TclLyonConnectionError(f"Invalid JSON from {url}") from err

    @asynccontextmanager
    async def _request(
        self,
        url: str,
        *,
        params: dict[str, str] | None = None,
        timeout: aiohttp.ClientTimeout,
    ) -> AsyncIterator[aiohttp.ClientResponse]:
        """GET ``url`` with auth, mapping transport failures to typed errors."""
        try:
            async with self._session.get(
                url, params=params, auth=self._auth, timeout=timeout
            ) as response:
                if response.status == 401:
                    raise TclLyonAuthError(
                        "HTTP 401 — set your data.grandlyon.com data password "
                        "(portal → mot-de-passe-oublie)."
                    )
                response.raise_for_status()
                yield response
        except TclLyonError:
            raise
        except aiohttp.ClientResponseError as err:
            # 5xx/429 are worth retrying; a permanent 4xx (e.g. 404) is not.
            retryable = err.status >= 500 or err.status == 429
            raise TclLyonConnectionError(
                f"HTTP {err.status} from {url}", retryable=retryable
            ) from err
        except (TimeoutError, aiohttp.ClientError) as err:
            raise TclLyonConnectionError(str(err) or type(err).__name__) from err
