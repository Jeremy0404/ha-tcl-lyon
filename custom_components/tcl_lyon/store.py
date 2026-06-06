"""Persistent cache + shipped fallback for the GTFS search index.

The stop→lines topology barely changes, so the expensive full build (streaming
``stop_times.txt``) is kept off every interactive path:

  - a prebuilt index ships in ``data/stop_lines.json.gz`` (see scripts/build_index.py),
    so a first-ever setup filters lines instantly with no download;
  - each running instance refreshes it from the live feed into HA storage on a
    weekly timer (see __init__.py), self-healing a stale shipped file.

:func:`async_get_index` is what the config/options flow calls: HA storage (freshest)
→ shipped file → a last-resort cheap download (today's stops+routes-only behaviour).
"""

from __future__ import annotations

import gzip
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .api import GtfsError, TclLyonClient, TclLyonError
from .const import (
    GTFS_INDEX_STORAGE_KEY,
    GTFS_INDEX_STORAGE_VERSION,
    GTFS_REFRESH_INTERVAL,
)
from .gtfs import GtfsIndex

_LOGGER = logging.getLogger(__name__)

SHIPPED_INDEX_PATH = Path(__file__).parent / "data" / "stop_lines.json.gz"


def _store(hass: HomeAssistant) -> Store:
    return Store(hass, GTFS_INDEX_STORAGE_VERSION, GTFS_INDEX_STORAGE_KEY)


def _load_shipped() -> GtfsIndex | None:
    """Read the prebuilt index packaged with the integration (blocking)."""
    try:
        raw = SHIPPED_INDEX_PATH.read_bytes()
    except FileNotFoundError:
        return None
    try:
        return GtfsIndex.from_dict(json.loads(gzip.decompress(raw)))
    except (OSError, ValueError, KeyError):
        _LOGGER.warning("Shipped GTFS index at %s is unreadable; ignoring", SHIPPED_INDEX_PATH)
        return None


async def async_load_cached_index(hass: HomeAssistant) -> GtfsIndex | None:
    """The index previously saved to HA storage, or None if absent/incompatible."""
    data = await _store(hass).async_load()
    if not data:
        return None
    try:
        return GtfsIndex.from_dict(data)
    except (ValueError, KeyError):
        _LOGGER.debug("Cached GTFS index is incompatible; will rebuild")
        return None


async def async_save_index(hass: HomeAssistant, index: GtfsIndex) -> None:
    await _store(hass).async_save(index.to_dict())


async def async_load_available_index(hass: HomeAssistant) -> GtfsIndex | None:
    """Best index reachable without any network: HA storage, then the shipped file."""
    index = await async_load_cached_index(hass)
    if index is not None:
        return index
    return await hass.async_add_executor_job(_load_shipped)


async def async_get_index(hass: HomeAssistant, client: TclLyonClient) -> GtfsIndex | None:
    """Best available index for the pickers: cache → shipped → cheap download.

    The download fallback only parses stops + routes (no serving map), so the line
    picker degrades to showing every line — never worse than before this cache.
    """
    index = await async_load_available_index(hass)
    if index is not None:
        return index

    try:
        data = await client.async_download_gtfs_bytes()
        return await hass.async_add_executor_job(GtfsIndex.from_bytes, data)
    except (TclLyonError, GtfsError):
        return None


def index_is_stale(index: GtfsIndex | None) -> bool:
    """True when the index is missing, undated, or older than the refresh window."""
    if index is None or index.built_at is None:
        return True
    return datetime.now(UTC) - index.built_at >= GTFS_REFRESH_INTERVAL


async def async_refresh_index(hass: HomeAssistant, client: TclLyonClient) -> bool:
    """Download the live GTFS, rebuild the full index, and cache it. False on failure."""
    try:
        data = await client.async_download_gtfs_bytes()
        index = await hass.async_add_executor_job(GtfsIndex.from_bytes_full, data)
    except (TclLyonError, GtfsError) as err:
        _LOGGER.debug("GTFS index refresh skipped: %s", err)
        return False
    await async_save_index(hass, index)
    _LOGGER.debug("GTFS index refreshed (%d stops served)", len(index.stop_routes))
    return True
