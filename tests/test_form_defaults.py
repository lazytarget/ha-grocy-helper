"""Tests for Phase 5: FormField default audit on SCAN_PROCESS fields.

Verifies that `build_scan_process_fields` sets `default` alongside
`suggested_value` when a reliable value exists, enabling auto-resolve.
"""

from __future__ import annotations


from custom_components.grocy_helper.const import CONF_ENABLE_PRICES, CONF_ENABLE_SHOPPING_LOCATIONS
from custom_components.grocy_helper.scan_form_builders import ScanFormBuilder
from custom_components.grocy_helper.scan_types import FormField

from tests.conftest import (
    FakeCoordinator,
    FakeGrocyAPI,
    make_master_data,
    make_product,
    make_product_barcode,
    make_stock_info,
)


# ── Helpers ──────────────────────────────────────────────────────────


def _build_fields(
    product: dict | None = None,
    price: str | None = None,
    best_before_in_days: int | None = None,
    shopping_location_id: str | None = None,
    scan_options: dict | None = None,
    current_recipe: dict | None = None,
    current_product_stock_info: dict | None = None,
    current_barcode: str | None = None,
    shopping_locations: list | None = None,
) -> list[FormField]:
    """Build SCAN_PROCESS fields using a FakeCoordinator."""
    grocy_api = FakeGrocyAPI()
    master = make_master_data()
    if shopping_locations is not None:
        master["shopping_locations"] = shopping_locations
    coordinator = FakeCoordinator(grocy_api=grocy_api, master_data=master)
    builder = ScanFormBuilder(coordinator)

    if scan_options is None:
        scan_options = {
            CONF_ENABLE_PRICES: True,
            CONF_ENABLE_SHOPPING_LOCATIONS: True,
        }

    return builder.build_scan_process_fields(
        _product=product or make_product(),
        price=price,
        best_before_in_days=best_before_in_days,
        shopping_location_id=shopping_location_id,
        scan_options=scan_options,
        current_recipe=current_recipe,
        current_product_stock_info=current_product_stock_info,
        current_barcode=current_barcode,
    )


def _get_field(fields: list[FormField], key: str) -> FormField | None:
    return next((f for f in fields if f.key == key), None)


# ═══════════════════════════════════════════════════════════════════
# best_before_in_days
# ═══════════════════════════════════════════════════════════════════


class TestBestBeforeDefault:
    """best_before_in_days should get a `default` when the product has
    `default_best_before_days` configured in Grocy."""

    def test_default_set_from_product(self):
        """Product's default_best_before_days becomes field.default."""
        product = make_product(default_best_before_days=7)
        fields = _build_fields(product=product, best_before_in_days=7)
        field = _get_field(fields, "best_before_in_days")

        assert field is not None
        assert field.default == "7"
        assert field.suggested_value == "7"

    def test_default_set_when_zero(self):
        """0 means 'expires today' in Grocy — suspicious, needs manual review.

        default should be None (not auto-fillable) but suggested_value
        should still show '0' so the user sees the current value.
        """
        product = make_product(default_best_before_days=0)
        fields = _build_fields(product=product, best_before_in_days=0)
        field = _get_field(fields, "best_before_in_days")

        assert field is not None
        assert field.default is None  # NOT auto-fillable
        assert field.suggested_value == "0"  # shown as hint

    def test_default_set_when_negative_one(self):
        """-1 means 'never expires' in Grocy — valid, auto-resolvable."""
        product = make_product(default_best_before_days=-1)
        fields = _build_fields(product=product, best_before_in_days=-1)
        field = _get_field(fields, "best_before_in_days")

        assert field is not None
        assert field.default == "-1"
        assert field.suggested_value == "-1"

    def test_no_default_when_none(self):
        """When best_before_in_days is None, no default is set."""
        fields = _build_fields(best_before_in_days=None)
        field = _get_field(fields, "best_before_in_days")

        assert field is not None
        assert field.default is None
        assert field.suggested_value is None

    def test_suggested_value_still_set(self):
        """suggested_value is always set alongside default."""
        product = make_product(default_best_before_days=14)
        fields = _build_fields(product=product, best_before_in_days=14)
        field = _get_field(fields, "best_before_in_days")

        assert field.suggested_value == "14"
        assert field.default == "14"

    def test_field_always_shown(self):
        """best_before_in_days is always emitted (no toggle)."""
        fields = _build_fields(
            best_before_in_days=5,
            scan_options={
                CONF_ENABLE_PRICES: True,
                CONF_ENABLE_SHOPPING_LOCATIONS: True,
            },
        )
        assert _get_field(fields, "best_before_in_days") is not None


# ═══════════════════════════════════════════════════════════════════
# shopping_location_id
# ═══════════════════════════════════════════════════════════════════


class TestShoppingLocationDefault:
    """shopping_location_id should get a `default` when a value is resolved
    from the barcode or product."""

    def test_default_from_barcode_shopping_location(self):
        """Barcode-specific shopping_location_id becomes default."""
        product = make_product(id=42)
        barcode_obj = make_product_barcode(
            product_id=42, barcode="111", shopping_location_id=5
        )
        stock_info = make_stock_info(product=product, barcodes=[barcode_obj])

        fields = _build_fields(
            product=product,
            current_product_stock_info=stock_info,
            current_barcode="111",
            shopping_locations=[{"id": 5, "name": "ICA"}],
        )
        field = _get_field(fields, "shopping_location_id")

        assert field is not None
        assert field.default == "5"
        assert field.suggested_value == "5"

    def test_default_from_product_default_shopping_location(self):
        """Product's default_shopping_location_id becomes default."""
        product = make_product(id=42, shopping_location_id=3)
        stock_info = make_stock_info(product=product, barcodes=[])
        stock_info["default_shopping_location_id"] = 3

        fields = _build_fields(
            product=product,
            current_product_stock_info=stock_info,
            current_barcode="999",
            shopping_locations=[{"id": 3, "name": "Coop"}],
        )
        field = _get_field(fields, "shopping_location_id")

        assert field is not None
        assert field.default == "3"

    def test_no_default_when_no_location_resolved(self):
        """When no shopping location can be determined, default is None."""
        product = make_product(id=42)
        stock_info = make_stock_info(product=product, barcodes=[])

        fields = _build_fields(
            product=product,
            current_product_stock_info=stock_info,
            current_barcode="999",
            shopping_locations=[{"id": 1, "name": "Store"}],
        )
        field = _get_field(fields, "shopping_location_id")

        assert field is not None
        assert field.default is None

    def test_field_not_shown_when_option_disabled(self):
        """When CONF_ENABLE_SHOPPING_LOCATIONS is False, field is not emitted."""
        fields = _build_fields(
            scan_options={
                CONF_ENABLE_PRICES: True,
                CONF_ENABLE_SHOPPING_LOCATIONS: False,
            },
        )
        assert _get_field(fields, "shopping_location_id") is None

    def test_field_not_shown_during_recipe(self):
        """Shopping location field is suppressed for recipe produce."""
        fields = _build_fields(
            current_recipe={"id": 1, "name": "Test"},
            shopping_locations=[{"id": 1, "name": "Store"}],
        )
        assert _get_field(fields, "shopping_location_id") is None


# ═══════════════════════════════════════════════════════════════════
# price — should NOT get a default
# ═══════════════════════════════════════════════════════════════════


class TestPriceNoDefault:
    """price should never have a default — it varies per purchase."""

    def test_price_has_no_default(self):
        """Price field never has a default value."""
        fields = _build_fields()
        field = _get_field(fields, "price")

        assert field is not None
        assert field.default is None

    def test_price_not_shown_when_already_set(self):
        """When price is already provided, there's no price field."""
        fields = _build_fields(price="25.0")
        assert _get_field(fields, "price") is None

    def test_price_not_shown_during_recipe(self):
        """Price field is suppressed for recipe produce."""
        fields = _build_fields(
            current_recipe={"id": 1, "name": "Test"},
        )
        assert _get_field(fields, "price") is None

    def test_price_not_shown_when_prices_disabled(self):
        """When CONF_ENABLE_PRICES is False, price field is not emitted."""
        fields = _build_fields(
            scan_options={
                CONF_ENABLE_PRICES: False,
                CONF_ENABLE_SHOPPING_LOCATIONS: True,
            },
        )
        assert _get_field(fields, "price") is None

    def test_price_shown_when_prices_enabled(self):
        """When CONF_ENABLE_PRICES is True, price field is emitted."""
        fields = _build_fields(
            scan_options={
                CONF_ENABLE_PRICES: True,
                CONF_ENABLE_SHOPPING_LOCATIONS: True,
            },
        )
        assert _get_field(fields, "price") is not None


# ═══════════════════════════════════════════════════════════════════
# Auto-resolve gate: all defaults set → auto-resolvable
# ═══════════════════════════════════════════════════════════════════


class TestAutoResolveGate:
    """When all fields have defaults, the auto-resolver can fill them."""

    def test_all_fields_have_defaults_for_known_product(self):
        """A product with best_before and shopping_location configured
        produces fields that are all auto-fillable."""
        product = make_product(id=42, default_best_before_days=5)
        barcode_obj = make_product_barcode(
            product_id=42, barcode="111", shopping_location_id=3
        )
        stock_info = make_stock_info(product=product, barcodes=[barcode_obj])

        fields = _build_fields(
            product=product,
            best_before_in_days=5,
            current_product_stock_info=stock_info,
            current_barcode="111",
            shopping_locations=[{"id": 3, "name": "ICA"}],
        )

        for field in fields:
            if field.required:
                assert field.default is not None, (
                    f"Required field '{field.key}' lacks default"
                )
            # Optional fields: either have default or are truly optional
            # (auto-resolver skips optional fields without default)

    def test_no_required_fields_block_auto_resolve(self):
        """All SCAN_PROCESS fields are optional — never block auto-resolve."""
        fields = _build_fields(best_before_in_days=None)

        for field in fields:
            assert field.required is False, (
                f"Field '{field.key}' is required but should be optional"
            )
