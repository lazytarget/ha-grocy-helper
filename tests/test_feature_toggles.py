"""Tests for feature toggles controlling form field visibility.

Verifies that CONF_ENABLE_CALORIES controls the calories_per_100 field
in build_update_product_details_fields.
"""

from __future__ import annotations

from custom_components.grocy_helper.const import CONF_ENABLE_CALORIES
from custom_components.grocy_helper.scan_form_builders import ScanFormBuilder
from custom_components.grocy_helper.scan_types import FormField

from tests.conftest import (
    FakeCoordinator,
    FakeGrocyAPI,
    make_master_data,
    make_product,
)


# ── Helpers ──────────────────────────────────────────────────────────


def _build_details_fields(
    product: dict | None = None,
    suggested: dict | None = None,
    scan_options: dict | None = None,
) -> list[FormField]:
    """Build update-product-details fields using a FakeCoordinator."""
    grocy_api = FakeGrocyAPI()
    master = make_master_data()
    coordinator = FakeCoordinator(grocy_api=grocy_api, master_data=master)
    builder = ScanFormBuilder(coordinator)

    return builder.build_update_product_details_fields(
        suggested=suggested or {},
        product=product or make_product(),
        scan_options=scan_options,
    )


def _get_field(fields: list[FormField], key: str) -> FormField | None:
    return next((f for f in fields if f.key == key), None)


# ═══════════════════════════════════════════════════════════════════
# CONF_ENABLE_CALORIES
# ═══════════════════════════════════════════════════════════════════


class TestCaloriesToggle:
    """CONF_ENABLE_CALORIES controls the calories_per_100 field."""

    def test_calories_shown_by_default(self):
        """When no scan_options passed, calories_per_100 is shown."""
        fields = _build_details_fields()
        assert _get_field(fields, "calories_per_100") is not None

    def test_calories_shown_when_enabled(self):
        """When CONF_ENABLE_CALORIES is True, calories_per_100 is shown."""
        fields = _build_details_fields(
            scan_options={CONF_ENABLE_CALORIES: True},
        )
        assert _get_field(fields, "calories_per_100") is not None

    def test_calories_hidden_when_disabled(self):
        """When CONF_ENABLE_CALORIES is False, calories_per_100 is not emitted."""
        fields = _build_details_fields(
            scan_options={CONF_ENABLE_CALORIES: False},
        )
        assert _get_field(fields, "calories_per_100") is None

    def test_other_fields_unaffected_when_calories_disabled(self):
        """Disabling calories doesn't remove other fields."""
        fields = _build_details_fields(
            scan_options={CONF_ENABLE_CALORIES: False},
        )
        assert _get_field(fields, "default_consume_location_id") is not None
        assert _get_field(fields, "product_quantity") is not None
        assert _get_field(fields, "qu_id_product") is not None
