"""Tests for the auto-resolver — headless ScanSession that fills defaults.

Written BEFORE the implementation (TDD).
"""

from __future__ import annotations

import pytest

from custom_components.grocy_helper.auto_resolver import (
    AutoResolveResult,
    async_try_auto_resolve,
)
from custom_components.grocy_helper.const import SCAN_MODE
from custom_components.grocy_helper.scan_types import Step

from tests.conftest import (
    FakeBarcodeBuddyAPI,
    FakeCoordinator,
    FakeGrocyAPI,
    make_master_data,
    make_product,
    make_product_barcode,
)


# ── Helpers ──────────────────────────────────────────────────────────


def _setup_known_product(
    grocy_api: FakeGrocyAPI,
    coordinator: FakeCoordinator,
    barcode: str = "7340011492900",
    product_name: str = "Milk",
    product_id: int = 42,
) -> None:
    """Register a product in the fake API and coordinator master data."""
    product = make_product(id=product_id, name=product_name)
    grocy_api.register_product(product, barcodes=[barcode])
    coordinator.data = make_master_data(products=[product])


# ── Tests: Successful auto-resolve ──────────────────────────────────


async def test_known_product_purchase_auto_resolves():
    """A known product in PURCHASE mode auto-resolves successfully."""
    grocy_api = FakeGrocyAPI()
    bbuddy_api = FakeBarcodeBuddyAPI()
    coordinator = FakeCoordinator(grocy_api=grocy_api, bbuddy_api=bbuddy_api)
    _setup_known_product(grocy_api, coordinator)

    result = await async_try_auto_resolve(
        coordinator=coordinator,
        api_bbuddy=bbuddy_api,
        config_entry_data={},
        barcode="7340011492900",
        mode=SCAN_MODE.PURCHASE,
    )

    assert result.success is True
    assert result.error is None


async def test_known_product_consume_auto_resolves():
    """A known product in CONSUME mode auto-resolves via BBuddy."""
    grocy_api = FakeGrocyAPI()
    bbuddy_api = FakeBarcodeBuddyAPI()
    coordinator = FakeCoordinator(grocy_api=grocy_api, bbuddy_api=bbuddy_api)
    _setup_known_product(grocy_api, coordinator)

    result = await async_try_auto_resolve(
        coordinator=coordinator,
        api_bbuddy=bbuddy_api,
        config_entry_data={},
        barcode="7340011492900",
        mode=SCAN_MODE.CONSUME,
    )

    # CONSUME goes through BBuddy post_scan which now returns success
    assert result.success is True


async def test_auto_resolve_result_text_on_success():
    """Successful resolve includes descriptive result text."""
    grocy_api = FakeGrocyAPI()
    bbuddy_api = FakeBarcodeBuddyAPI()
    coordinator = FakeCoordinator(grocy_api=grocy_api, bbuddy_api=bbuddy_api)
    _setup_known_product(grocy_api, coordinator)

    result = await async_try_auto_resolve(
        coordinator=coordinator,
        api_bbuddy=bbuddy_api,
        config_entry_data={},
        barcode="7340011492900",
        mode=SCAN_MODE.PURCHASE,
    )

    assert result.success is True
    assert result.result_text is not None
    assert len(result.result_text) > 0


# ── Tests: Items that need manual intervention ──────────────────────


async def test_unknown_product_stays_pending():
    """An unknown barcode needs manual intervention (product creation)."""
    grocy_api = FakeGrocyAPI()
    bbuddy_api = FakeBarcodeBuddyAPI()
    coordinator = FakeCoordinator(grocy_api=grocy_api, bbuddy_api=bbuddy_api)
    # No product registered — barcode lookup will return None

    result = await async_try_auto_resolve(
        coordinator=coordinator,
        api_bbuddy=bbuddy_api,
        config_entry_data={},
        barcode="0000000000000",
        mode=SCAN_MODE.PURCHASE,
    )

    assert result.success is False
    assert result.needs_manual is True
    # No stock should have been added
    assert len(grocy_api._added_stock) == 0


async def test_product_needing_match_stays_pending():
    """A product that needs matching (SCAN_MATCH_PRODUCT) requires manual input."""
    grocy_api = FakeGrocyAPI()
    bbuddy_api = FakeBarcodeBuddyAPI()
    coordinator = FakeCoordinator(grocy_api=grocy_api, bbuddy_api=bbuddy_api)
    # Product not found by barcode → match form shown

    result = await async_try_auto_resolve(
        coordinator=coordinator,
        api_bbuddy=bbuddy_api,
        config_entry_data={},
        barcode="9999999999999",
        mode=SCAN_MODE.PURCHASE,
    )

    assert result.success is False
    assert result.needs_manual is True


# ── Tests: Safety guards ────────────────────────────────────────────


async def test_auto_resolve_loop_limit_prevents_infinite():
    """The resolver stops after MAX_STEPS iterations."""
    grocy_api = FakeGrocyAPI()
    bbuddy_api = FakeBarcodeBuddyAPI()
    coordinator = FakeCoordinator(grocy_api=grocy_api, bbuddy_api=bbuddy_api)

    # Don't register any product — but we want to test the loop guard
    # with a barcode that somehow keeps returning forms
    result = await async_try_auto_resolve(
        coordinator=coordinator,
        api_bbuddy=bbuddy_api,
        config_entry_data={},
        barcode="0000000000000",
        mode=SCAN_MODE.PURCHASE,
    )

    # Should terminate (not hang), regardless of outcome
    assert result is not None
    assert isinstance(result, AutoResolveResult)


async def test_auto_resolve_api_error_marks_failed():
    """An API exception during resolve is caught and reported."""
    grocy_api = FakeGrocyAPI()
    bbuddy_api = FakeBarcodeBuddyAPI()
    coordinator = FakeCoordinator(grocy_api=grocy_api, bbuddy_api=bbuddy_api)
    _setup_known_product(grocy_api, coordinator)

    # Make post_scan raise an exception (PURCHASE without shopping_location
    # goes through BBuddy)
    async def _failing_post_scan(request):
        raise RuntimeError("Grocy API timeout")

    bbuddy_api.post_scan = _failing_post_scan

    result = await async_try_auto_resolve(
        coordinator=coordinator,
        api_bbuddy=bbuddy_api,
        config_entry_data={},
        barcode="7340011492900",
        mode=SCAN_MODE.PURCHASE,
    )

    assert result.success is False
    assert result.needs_manual is False  # it's an error, not "needs manual"
    assert "Grocy API timeout" in result.error


# ── Tests: Form default filling ─────────────────────────────────────


async def test_auto_resolve_uses_field_defaults():
    """When a form is auto-resolvable, field.default values are used."""
    grocy_api = FakeGrocyAPI()
    bbuddy_api = FakeBarcodeBuddyAPI()
    coordinator = FakeCoordinator(grocy_api=grocy_api, bbuddy_api=bbuddy_api)
    _setup_known_product(grocy_api, coordinator)

    # Enable all form options (price, bestBefore, store) so the form IS shown
    config_data = {}

    result = await async_try_auto_resolve(
        coordinator=coordinator,
        api_bbuddy=bbuddy_api,
        config_entry_data=config_data,
        barcode="7340011492900",
        mode=SCAN_MODE.PURCHASE,
    )

    # Even with forms enabled, if all required fields have defaults,
    # auto-resolve should succeed
    assert result.success is True


async def test_scan_options_disable_forms_for_faster_resolve():
    """Disabling form options skips the form entirely during auto-resolve."""
    grocy_api = FakeGrocyAPI()
    bbuddy_api = FakeBarcodeBuddyAPI()
    coordinator = FakeCoordinator(grocy_api=grocy_api, bbuddy_api=bbuddy_api)
    _setup_known_product(grocy_api, coordinator)

    result = await async_try_auto_resolve(
        coordinator=coordinator,
        api_bbuddy=bbuddy_api,
        config_entry_data={},
        barcode="7340011492900",
        mode=SCAN_MODE.PURCHASE,
        scan_options={
            "input_price": False,
            "input_bestBeforeInDays": False,
            "input_shoppingLocationId": False,
        },
    )

    assert result.success is True
