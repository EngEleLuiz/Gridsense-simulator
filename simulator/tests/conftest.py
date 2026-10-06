"""Shared fixtures for the hosting-capacity tests."""

from __future__ import annotations

import pandapower as pp
import pandapower.networks as pn
import pytest


@pytest.fixture(scope="module")
def cigre() -> pp.pandapowerNet:
    """Nominal CIGRE LV network. Estimators must never mutate it."""
    return pn.create_cigre_network_lv()
