"""Tests for Grocy user settings product preset parsing.

Phase 1 covers parsing ``/api/user/settings`` into typed product preset
defaults and exposing them through coordinator master data.
"""

from __future__ import annotations

import datetime as dt
import logging
from unittest.mock import MagicMock

from custom_components.grocy_helper.scan_form_builders import ScanFormBuilder
from custom_components.grocy_helper.scan_product_builders import ProductDataBuilder
from custom_components.grocy_helper.scan_session import ScanSession
from custom_components.grocy_helper.coordinator import GrocyHelperCoordinator
from custom_components.grocy_helper.grocyapi import parse_product_presets

from tests.conftest import (
    FakeBarcodeBuddyAPI,
    FakeCoordinator,
    FakeGrocyAPI,
    make_master_data,
    make_product,
)


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


def test_get_product_defaults_merges_grocy_product_presets():
    """Grocy product presets override hardcoded base defaults for normal products."""
    coordinator = FakeCoordinator(
        master_data=make_master_data(
            product_presets={
                "location_id": 2,
                "product_group_id": 9,
                "qu_id": 4,
                "default_best_before_days": 14,
                "treat_opened_as_out_of_stock": True,
            }
        )
    )
    session = ScanSession(
        coordinator=coordinator,
        api_bbuddy=FakeBarcodeBuddyAPI(),
        config_entry_data={},
    )

    defaults = session._get_product_defaults()

    assert defaults["location_id"] == 2
    assert defaults["product_group_id"] == 9
    assert defaults["qu_id"] == 4
    assert defaults["default_best_before_days"] == 14
    assert defaults["treat_opened_as_out_of_stock"] is True


def test_recipe_product_defaults_override_grocy_presets():
    """Recipe defaults take precedence over Grocy stock presets."""
    coordinator = FakeCoordinator(
        master_data=make_master_data(
            product_presets={
                "location_id": 99,
                "product_group_id": 88,
                "qu_id": 4,
                "default_best_before_days": 14,
                "treat_opened_as_out_of_stock": True,
            }
        )
    )
    session = ScanSession(
        coordinator=coordinator,
        api_bbuddy=FakeBarcodeBuddyAPI(),
        config_entry_data={},
        scan_options={
            "locations": {
                "default_fridge": 1,
                "default_freezer": 2,
            },
            "product_groups": {"default_for_recipe_products": 7},
            "defaults_for_recipe_product": {
                "location_id": 2,
                "should_not_be_frozen": False,
                "default_best_before_days": 3,
                "default_best_before_days_after_open": 1,
                "default_best_before_days_after_freezing": 60,
                "default_best_before_days_after_thawing": 3,
            },
        },
    )

    defaults = session._get_recipe_product_defaults()

    assert defaults["location_id"] == 2
    assert defaults["product_group_id"] == 7
    assert defaults["default_best_before_days"] == 3
    assert defaults["treat_opened_as_out_of_stock"] is True


def test_build_create_product_fields_includes_treat_opened_toggle():
    """Create-product form exposes treat_opened_as_out_of_stock from presets."""
    builder = ScanFormBuilder(FakeCoordinator())

    fields = builder.build_create_product_fields(
        {
            "name": "Milk",
            "location_id": 1,
            "treat_opened_as_out_of_stock": True,
        }
    )

    field = next((f for f in fields if f.key == "treat_opened_as_out_of_stock"), None)
    assert field is not None
    assert field.default is True


def test_build_product_from_input_includes_treat_opened_flag():
    """Submitted create-product input persists treat_opened_as_out_of_stock."""
    base_product = make_product()

    product = ProductDataBuilder.build_product_from_input(
        {
            "name": "Milk",
            "location_id": 1,
            "qu_id_stock": 1,
            "qu_id_purchase": 1,
            "qu_id_consume": 1,
            "qu_id_price": 1,
            "treat_opened_as_out_of_stock": True,
        },
        base_product,
    )

    assert product["treat_opened_as_out_of_stock"] == 1


def test_build_create_product_fields_uses_qu_id_preset_for_all_units():
    """Generic qu_id preset is used as fallback for all unit fields."""
    builder = ScanFormBuilder(FakeCoordinator())

    fields = builder.build_create_product_fields(
        {
            "name": "Milk",
            "location_id": 1,
            "qu_id": 4,
        }
    )

    assert next(f for f in fields if f.key == "qu_id_stock").suggested_value == "4"
    assert next(f for f in fields if f.key == "qu_id_purchase").suggested_value == "4"
    assert next(f for f in fields if f.key == "qu_id_consume").suggested_value == "4"
    assert next(f for f in fields if f.key == "qu_id_price").suggested_value == "4"


def test_specific_qu_values_override_generic_qu_id_preset():
    """Specific unit suggestions take precedence over generic qu_id fallback."""
    builder = ScanFormBuilder(FakeCoordinator())

    fields = builder.build_create_product_fields(
        {
            "name": "Milk",
            "location_id": 1,
            "qu_id": 4,
            "qu_id_stock": 1,
            "qu_id_purchase": 2,
            "qu_id_consume": 3,
            "qu_id_price": 5,
        }
    )

    assert next(f for f in fields if f.key == "qu_id_stock").suggested_value == "1"
    assert next(f for f in fields if f.key == "qu_id_purchase").suggested_value == "2"
    assert next(f for f in fields if f.key == "qu_id_consume").suggested_value == "3"
    assert next(f for f in fields if f.key == "qu_id_price").suggested_value == "5"


def test_recipe_product_defaults_keep_qu_id_preset_when_not_overridden():
    """Recipe defaults inherit Grocy qu_id preset when recipe-specific QU is absent."""
    coordinator = FakeCoordinator(
        master_data=make_master_data(
            product_presets={
                "location_id": 99,
                "product_group_id": 88,
                "qu_id": 4,
                "default_best_before_days": 14,
                "treat_opened_as_out_of_stock": True,
            }
        )
    )
    session = ScanSession(
        coordinator=coordinator,
        api_bbuddy=FakeBarcodeBuddyAPI(),
        config_entry_data={},
        scan_options={
            "locations": {
                "default_fridge": 1,
                "default_freezer": 2,
            },
            "product_groups": {"default_for_recipe_products": 7},
            "defaults_for_recipe_product": {
                "location_id": 2,
                "should_not_be_frozen": False,
                "default_best_before_days": 3,
            },
        },
    )

    defaults = session._get_recipe_product_defaults()

    assert defaults["qu_id"] == 4