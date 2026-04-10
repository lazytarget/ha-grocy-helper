"""Tests for the auto-resolver — headless ScanSession that fills defaults.

Written BEFORE the implementation (TDD).
"""

from __future__ import annotations


from custom_components.grocy_helper.auto_resolver import (
    AutoResolveResult,
    async_try_auto_resolve,
    _validate_product_config,
)
from custom_components.grocy_helper.const import CONF_ENABLE_PRICES, SCAN_MODE

from tests.conftest import (
    FakeBarcodeBuddyAPI,
    FakeCoordinator,
    FakeGrocyAPI,
    make_master_data,
    make_product,
)


# ── Helpers ──────────────────────────────────────────────────────────


def _setup_known_product(
    grocy_api: FakeGrocyAPI,
    coordinator: FakeCoordinator,
    barcode: str = "7340011492900",
    product_name: str = "Milk",
    product_id: int = 42,
) -> None:
    """Register a product in the fake API and coordinator master data.

    Uses well-configured product defaults so the product passes the
    auto-resolver's product config quality gate.
    """
    product = make_product(
        id=product_id,
        name=product_name,
        default_best_before_days=5,
        default_best_before_days_after_freezing=30,
    )
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
            CONF_ENABLE_PRICES: False,
            "input_bestBeforeInDays": False,
            "input_shoppingLocationId": False,
        },
    )

    assert result.success is True


async def test_auto_resolve_simpler_with_prices_disabled():
    """When CONF_ENABLE_PRICES is False in config_entry_data, the
    auto-resolver has no price field to fill — simpler auto-resolve."""
    grocy_api = FakeGrocyAPI()
    bbuddy_api = FakeBarcodeBuddyAPI()
    coordinator = FakeCoordinator(grocy_api=grocy_api, bbuddy_api=bbuddy_api)
    _setup_known_product(grocy_api, coordinator)

    result = await async_try_auto_resolve(
        coordinator=coordinator,
        api_bbuddy=bbuddy_api,
        config_entry_data={CONF_ENABLE_PRICES: False},
        barcode="7340011492900",
        mode=SCAN_MODE.PURCHASE,
    )

    assert result.success is True


# ── Tests: Product config validation ────────────────────────────────


class TestValidateProductConfig:
    """_validate_product_config checks product attributes for suspicious
    values that indicate the Grocy product config needs human review."""

    def test_well_configured_product_passes(self):
        """A product with all defaults configured has no issues."""
        product = make_product(
            default_best_before_days=7,
            default_best_before_days_after_open=3,
            default_best_before_days_after_freezing=60,
            default_best_before_days_after_thawing=3,
        )
        issues = _validate_product_config(product)
        assert issues == []

    def test_best_before_days_zero_flagged(self):
        """default_best_before_days=0 means 'expires today' — suspicious."""
        product = make_product(default_best_before_days=0)
        issues = _validate_product_config(product)
        assert any("default_best_before_days" in i for i in issues)

    def test_best_before_days_negative_one_ok(self):
        """-1 means 'never expires' — valid, no issue."""
        product = make_product(
            default_best_before_days=-1,
            default_best_before_days_after_freezing=-1,
        )
        issues = _validate_product_config(product)
        assert issues == []

    def test_best_before_days_positive_ok(self):
        """Positive values are normal configured defaults."""
        product = make_product(
            default_best_before_days=14,
            default_best_before_days_after_freezing=30,
        )
        issues = _validate_product_config(product)
        assert issues == []

    def test_after_freezing_zero_flagged(self):
        """default_best_before_days_after_freezing=0 — not configured."""
        product = make_product(
            default_best_before_days=7,
            default_best_before_days_after_freezing=0,
        )
        issues = _validate_product_config(product)
        assert any("after_freezing" in i for i in issues)

    def test_after_freezing_negative_one_ok(self):
        """-1 for after_freezing means 'never overdue' — valid."""
        product = make_product(
            default_best_before_days=7,
            default_best_before_days_after_freezing=-1,
        )
        issues = _validate_product_config(product)
        assert not any("after_freezing" in i for i in issues)

    def test_after_open_zero_not_flagged(self):
        """default_best_before_days_after_open=0 means disabled — valid."""
        product = make_product(
            default_best_before_days=7,
            default_best_before_days_after_open=0,
        )
        issues = _validate_product_config(product)
        assert not any("after_open" in i for i in issues)

    def test_after_thawing_zero_not_flagged(self):
        """default_best_before_days_after_thawing=0 means today — valid."""
        product = make_product(
            default_best_before_days=7,
            default_best_before_days_after_thawing=0,
        )
        issues = _validate_product_config(product)
        assert not any("after_thawing" in i for i in issues)

    def test_multiple_issues_collected(self):
        """Multiple suspicious values produce multiple issues."""
        product = make_product(
            default_best_before_days=0,
            default_best_before_days_after_freezing=0,
        )
        issues = _validate_product_config(product)
        assert len(issues) >= 2


# ── Tests: Auto-resolve with suspicious product config ──────────────


async def test_auto_resolve_rejects_best_before_zero():
    """Product with default_best_before_days=0 needs manual review."""
    grocy_api = FakeGrocyAPI()
    bbuddy_api = FakeBarcodeBuddyAPI()
    coordinator = FakeCoordinator(grocy_api=grocy_api, bbuddy_api=bbuddy_api)

    product = make_product(id=42, name="Milk", default_best_before_days=0)
    grocy_api.register_product(product, barcodes=["111"])
    coordinator.data = make_master_data(products=[product])

    result = await async_try_auto_resolve(
        coordinator=coordinator,
        api_bbuddy=bbuddy_api,
        config_entry_data={},
        barcode="111",
        mode=SCAN_MODE.PURCHASE,
    )

    assert result.success is False
    assert result.needs_manual is True
    assert "default_best_before_days" in result.error


async def test_auto_resolve_rejects_after_freezing_zero():
    """Product with default_best_before_days_after_freezing=0 needs review."""
    grocy_api = FakeGrocyAPI()
    bbuddy_api = FakeBarcodeBuddyAPI()
    coordinator = FakeCoordinator(grocy_api=grocy_api, bbuddy_api=bbuddy_api)

    product = make_product(
        id=42, name="Chicken",
        default_best_before_days=5,
        default_best_before_days_after_freezing=0,
    )
    grocy_api.register_product(product, barcodes=["222"])
    coordinator.data = make_master_data(products=[product])

    result = await async_try_auto_resolve(
        coordinator=coordinator,
        api_bbuddy=bbuddy_api,
        config_entry_data={},
        barcode="222",
        mode=SCAN_MODE.PURCHASE,
    )

    assert result.success is False
    assert result.needs_manual is True
    assert "after_freezing" in result.error


async def test_auto_resolve_accepts_never_expires():
    """Product with default_best_before_days=-1 auto-resolves fine."""
    grocy_api = FakeGrocyAPI()
    bbuddy_api = FakeBarcodeBuddyAPI()
    coordinator = FakeCoordinator(grocy_api=grocy_api, bbuddy_api=bbuddy_api)

    product = make_product(
        id=42, name="Salt",
        default_best_before_days=-1,
        default_best_before_days_after_freezing=-1,
    )
    grocy_api.register_product(product, barcodes=["333"])
    coordinator.data = make_master_data(products=[product])

    result = await async_try_auto_resolve(
        coordinator=coordinator,
        api_bbuddy=bbuddy_api,
        config_entry_data={},
        barcode="333",
        mode=SCAN_MODE.PURCHASE,
    )

    assert result.success is True


async def test_auto_resolve_accepts_after_open_zero():
    """after_open=0 (disabled) does not block auto-resolve."""
    grocy_api = FakeGrocyAPI()
    bbuddy_api = FakeBarcodeBuddyAPI()
    coordinator = FakeCoordinator(grocy_api=grocy_api, bbuddy_api=bbuddy_api)

    product = make_product(
        id=42, name="Pasta",
        default_best_before_days=365,
        default_best_before_days_after_open=0,
        default_best_before_days_after_freezing=90,
        default_best_before_days_after_thawing=0,
    )
    grocy_api.register_product(product, barcodes=["444"])
    coordinator.data = make_master_data(products=[product])

    result = await async_try_auto_resolve(
        coordinator=coordinator,
        api_bbuddy=bbuddy_api,
        config_entry_data={},
        barcode="444",
        mode=SCAN_MODE.PURCHASE,
    )

    assert result.success is True
