"""Tests for Grocy user settings product preset parsing.

Phase 1 covers parsing ``/api/user/settings`` into typed product preset
defaults and exposing them through coordinator master data.
"""

from __future__ import annotations

import datetime as dt
import logging
from unittest.mock import MagicMock

from custom_components.grocy_helper.coordinator import GrocyHelperCoordinator
from custom_components.grocy_helper.grocyapi import parse_product_presets

from tests.conftest import FakeBarcodeBuddyAPI, FakeGrocyAPI


def test_parse_product_presets_complete_settings():
    """All relevant Grocy preset keys are parsed and normalized."""
    settings = {
        "product_presets_location_id": 2,
        "product_presets_product_group_id": 9,
        "product_presets_qu_id": 4,
        "product_presets_default_due_days": 14,
        "product_presets_treat_opened_as_out_of_stock": True,
    }

    parsed = parse_product_presets(settings)

    assert parsed == {
        "location_id": 2,
        "product_group_id": 9,
        "qu_id": 4,
        "default_best_before_days": 14,
        "treat_opened_as_out_of_stock": True,
    }


def test_parse_product_presets_unset_values_fall_back_to_none():
    """Unset Grocy preset sentinel values do not override hardcoded defaults."""
    settings = {
        "product_presets_location_id": -1,
        "product_presets_product_group_id": -1,
        "product_presets_qu_id": -1,
        "product_presets_default_due_days": 0,
        "product_presets_treat_opened_as_out_of_stock": False,
    }

    parsed = parse_product_presets(settings)

    assert parsed == {
        "location_id": None,
        "product_group_id": None,
        "qu_id": None,
        "default_best_before_days": None,
        "treat_opened_as_out_of_stock": False,
    }


async def test_fetch_data_includes_product_presets():
    """Coordinator master data includes parsed product presets."""
    grocy_api = FakeGrocyAPI()
    grocy_api._user_settings = {
        "product_presets_location_id": 2,
        "product_presets_product_group_id": -1,
        "product_presets_qu_id": 3,
        "product_presets_default_due_days": -1,
        "product_presets_treat_opened_as_out_of_stock": True,
    }

    coordinator = object.__new__(GrocyHelperCoordinator)
    coordinator._api_grocy = grocy_api
    coordinator._api_bbuddy = FakeBarcodeBuddyAPI()
    coordinator._config_entry = MagicMock()
    coordinator._hass = MagicMock()
    coordinator._logger = logging.getLogger("test")
    coordinator.update_interval = dt.timedelta(minutes=5)

    masterdata = await GrocyHelperCoordinator.fetch_data(coordinator)

    assert masterdata["product_presets"] == {
        "location_id": 2,
        "product_group_id": None,
        "qu_id": 3,
        "default_best_before_days": -1,
        "treat_opened_as_out_of_stock": True,
    }