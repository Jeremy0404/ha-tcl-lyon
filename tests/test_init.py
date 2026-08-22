"""Tests for the entry-level wiring in __init__.py.

Needs Home Assistant, so the whole module is skipped where it isn't installed
(the pure parser suites still collect without it).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

# homeassistant.runner imports fcntl → skips on native Windows, runs on Linux/CI.
pytest.importorskip("homeassistant.runner")

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tcl_lyon import _auth_tracker, async_unload_entry
from custom_components.tcl_lyon.const import DOMAIN


def _entry(hass) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    return entry


async def test_auth_tracker_survives_setup_retry(hass):
    """A ConfigEntryNotReady retry doesn't unload, so the streak must carry over."""
    entry = _entry(hass)

    assert _auth_tracker(hass, entry) is _auth_tracker(hass, entry)
    # ...but entries hold separate credentials, so they never share a streak.
    assert _auth_tracker(hass, _entry(hass)) is not _auth_tracker(hass, entry)


async def test_auth_tracker_is_dropped_on_unload(hass):
    """Reauth and the options flow both reload — the new credential starts clean."""
    entry = _entry(hass)
    tracker = _auth_tracker(hass, entry)

    with patch.object(hass.config_entries, "async_unload_platforms", return_value=True):
        assert await async_unload_entry(hass, entry)

    assert _auth_tracker(hass, entry) is not tracker
