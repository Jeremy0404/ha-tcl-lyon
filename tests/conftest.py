"""Shared pytest fixtures for the TCL Lyon integration tests."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):  # noqa: ARG001
    """Enable loading custom integrations during tests."""
    return
