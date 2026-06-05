"""Shared pytest fixtures for the TCL Lyon integration tests.

The SIRI/GTFS parser tests are pure and need no Home Assistant. The HA-backed
``enable_custom_integrations`` fixture is only wired up when the
``pytest-homeassistant-custom-component`` plugin is installed, so the parser
suite still collects on a minimal environment.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

try:  # pragma: no cover - environment dependent
    # homeassistant.runner imports fcntl, so this fails on native Windows even
    # though the package installs — a faithful proxy for "the HA test harness
    # (and its pytest plugin) can load here". Gating on it lets the pure parser
    # suite still run on Windows via `pytest -p no:homeassistant`.
    import homeassistant.runner  # noqa: F401

    _HAS_HA = True
except ImportError:  # pragma: no cover - environment dependent
    _HAS_HA = False

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    """Load a JSON fixture from tests/fixtures/."""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


if _HAS_HA:

    @pytest.fixture(autouse=True)
    def auto_enable_custom_integrations(enable_custom_integrations):
        """Enable loading custom integrations during HA-backed tests."""
        return
