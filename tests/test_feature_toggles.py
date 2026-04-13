"""Tests for feature toggles controlling form field visibility.

Verifies that CONF_ENABLE_CALORIES controls the calories_per_100 field
in build_update_product_details_fields.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from custom_components.grocy_helper.const import (
    CONF_ENABLE_CALORIES,
    CONF_ENABLE_PRICES,
    CONF_ENABLE_SHOPPING_LOCATIONS,
    SCAN_MODE,
)
from custom_components.grocy_helper.scan_form_builders import ScanFormBuilder
from custom_components.grocy_helper.scan_session import ScanSession
from custom_components.grocy_helper.scan_types import FormField
from custom_components.grocy_helper.scan_types import CompletedResult

from tests.conftest import (
    FakeBarcodeBuddyAPI,
    FakeCoordinator,
    FakeGrocyAPI,
    make_master_data,
    make_product,
    make_stock_info,
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


def _build_create_barcode_fields(
    suggested: dict | None = None,
    scan_options: dict | None = None,
) -> list[FormField]:
    """Build create-barcode fields using a FakeCoordinator."""
    grocy_api = FakeGrocyAPI()
    master = make_master_data(
        shopping_locations=[
            {"id": 3, "name": "Coop"},
            {"id": 7, "name": "ICA"},
        ]
    )
    coordinator = FakeCoordinator(grocy_api=grocy_api, master_data=master)
    builder = ScanFormBuilder(coordinator)

    return builder.build_create_barcode_fields(
        suggested=suggested or {},
        scan_options=scan_options,
    )


def _build_scan_process_fields(
    product: dict | None = None,
    price: str | None = None,
    best_before_in_days: int | None = None,
    shopping_location_id: str | None = None,
    scan_options: dict | None = None,
) -> list[FormField]:
    """Build scan-process fields using a FakeCoordinator."""
    grocy_api = FakeGrocyAPI()
    master = make_master_data(
        shopping_locations=[
            {"id": 3, "name": "Coop"},
            {"id": 7, "name": "ICA"},
        ]
    )
    coordinator = FakeCoordinator(grocy_api=grocy_api, master_data=master)
    builder = ScanFormBuilder(coordinator)

    return builder.build_scan_process_fields(
        _product=product or make_product(),
        price=price,
        best_before_in_days=best_before_in_days,
        shopping_location_id=shopping_location_id,
        scan_options=scan_options or {},
        current_recipe=None,
        current_product_stock_info=None,
        current_barcode=None,
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

    def test_calories_shown_when_key_missing_from_options(self):
        """When scan_options is present but CONF_ENABLE_CALORIES key is absent, defaults to shown."""
        fields = _build_details_fields(
            scan_options={"some_other_option": True},
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


class TestShoppingLocationsToggle:
    """CONF_ENABLE_SHOPPING_LOCATIONS controls barcode shopping-location fields."""

    def test_create_barcode_shopping_location_shown_by_default(self):
        """When no scan_options passed, barcode shopping_location_id is shown."""
        fields = _build_create_barcode_fields()
        assert _get_field(fields, "shopping_location_id") is not None

    def test_create_barcode_shopping_location_shown_when_key_missing(self):
        """When scan_options is present but key is absent, field defaults to shown."""
        fields = _build_create_barcode_fields(
            scan_options={"some_other_option": True},
        )
        assert _get_field(fields, "shopping_location_id") is not None

    def test_create_barcode_shopping_location_hidden_when_disabled(self):
        """When CONF_ENABLE_SHOPPING_LOCATIONS is False, barcode field is hidden."""
        fields = _build_create_barcode_fields(
            scan_options={CONF_ENABLE_SHOPPING_LOCATIONS: False},
        )
        assert _get_field(fields, "shopping_location_id") is None

    async def test_add_product_barcode_ignores_shopping_location_when_disabled(self):
        """Barcode submission ignores shopping_location_id when toggle is disabled."""
        captured: dict[str, dict] = {}

        class CapturingGrocyAPI(FakeGrocyAPI):
            async def add_product_barcode(self, data: dict):
                captured["barcode"] = data
                return {"created_object_id": 1}

        grocy_api = CapturingGrocyAPI()
        bbuddy_api = FakeBarcodeBuddyAPI()
        coordinator = FakeCoordinator(grocy_api=grocy_api, bbuddy_api=bbuddy_api)
        session = ScanSession(
            coordinator=coordinator,
            api_bbuddy=bbuddy_api,
            scan_options={
                CONF_ENABLE_SHOPPING_LOCATIONS: False,
                "input_product_details_during_provision": False,
            },
            config_entry_data={},
        )
        session.current_barcode = "1234567890123"
        session._state.set_product(make_product(id=42, name="Milk"))
        session._step_add_product_parent = AsyncMock(
            return_value=CompletedResult(summary="ok")
        )

        result = await session._step_add_product_barcode(
            {
                "note": "Milk",
                "shopping_location_id": "7",
                "qu_id": "1",
                "amount": 2,
            }
        )

        assert isinstance(result, CompletedResult)
        assert captured["barcode"]["shopping_location_id"] is None
        assert captured["barcode"]["qu_id"] == "1"
        assert captured["barcode"]["amount"] == 2


class TestAllTogglesDisabled:
    """Disabling prices, shopping locations, and calories yields minimal forms."""

    ALL_DISABLED = {
        CONF_ENABLE_PRICES: False,
        CONF_ENABLE_SHOPPING_LOCATIONS: False,
        CONF_ENABLE_CALORIES: False,
    }

    def test_scan_process_renders_only_best_before_field(self):
        """With all toggles off, SCAN_PROCESS keeps only essential fields."""
        fields = _build_scan_process_fields(
            best_before_in_days=5,
            scan_options=self.ALL_DISABLED,
        )

        keys = [field.key for field in fields]
        assert keys == ["best_before_in_days"]

    def test_update_details_hides_calories_but_keeps_core_fields(self):
        """With all toggles off, update-details hides calories and keeps core fields."""
        fields = _build_details_fields(scan_options=self.ALL_DISABLED)

        assert _get_field(fields, "calories_per_100") is None
        assert _get_field(fields, "default_consume_location_id") is not None
        assert _get_field(fields, "product_quantity") is not None
        assert _get_field(fields, "qu_id_product") is not None

    async def test_scan_process_ignores_stale_price_and_store_when_disabled(self):
        """Submitted gated values are ignored when all related toggles are disabled."""
        captured: dict[str, dict] = {}

        class CapturingBarcodeBuddyAPI(FakeBarcodeBuddyAPI):
            async def post_scan(self, request: dict) -> dict:
                captured["request"] = request
                return {"result": "OK", "barcode": request.get("barcode", "")}

        grocy_api = FakeGrocyAPI()
        bbuddy_api = CapturingBarcodeBuddyAPI()
        coordinator = FakeCoordinator(grocy_api=grocy_api, bbuddy_api=bbuddy_api)

        product = make_product(id=42, name="Milk", default_best_before_days=5)
        stock_info = make_stock_info(product=product, barcodes=[])
        grocy_api.register_product(product, barcodes=["1234567890123"])

        session = ScanSession(
            coordinator=coordinator,
            api_bbuddy=bbuddy_api,
            scan_options=self.ALL_DISABLED,
            config_entry_data={},
        )
        session.current_barcode = "1234567890123"
        session.barcode_scan_mode = SCAN_MODE.PURCHASE
        session._state.set_stock_info(stock_info)
        session._handle_scan_success = AsyncMock(
            return_value=CompletedResult(summary="ok")
        )

        result = await session._step_scan_process(
            {
                "price": "19.95",
                "best_before_in_days": "5",
                "shopping_location_id": "7",
            }
        )

        assert isinstance(result, CompletedResult)
        assert captured["request"] == {
            "barcode": "1234567890123",
            "bestBeforeInDays": 5,
        }


class TestCaloriesToggleWithOpenFoodFacts:
    """CONF_ENABLE_CALORIES=False should suppress calorie side effects even with OFF data."""

    async def test_off_calories_do_not_trigger_updates_when_disabled(self):
        """OFF kcal data should not call calorie calculation nor persist calories."""

        class CapturingCoordinator(FakeCoordinator):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.product_updates: list[dict] = []

            async def update_product(self, product_id: int, changes: dict) -> dict:
                self.product_updates.append(changes)
                return {}

        grocy_api = FakeGrocyAPI()
        bbuddy_api = FakeBarcodeBuddyAPI()
        coordinator = CapturingCoordinator(grocy_api=grocy_api, bbuddy_api=bbuddy_api)

        product = make_product(id=42, name="Milk")
        session = ScanSession(
            coordinator=coordinator,
            api_bbuddy=bbuddy_api,
            scan_options={CONF_ENABLE_CALORIES: False},
            config_entry_data={},
        )
        session._state.set_stock_info(make_stock_info(product=product, barcodes=[]))
        session._state.current_product_openfoodfacts = {
            "nutriments": {"energy_kcal_100g": 88}
        }
        session._step_add_product_parent = AsyncMock(
            return_value=CompletedResult(summary="ok")
        )
        session._calculate_calories_per_pack = AsyncMock(return_value=321)

        result = await session._step_update_product_details(
            {"default_consume_location_id": "2"}
        )

        assert isinstance(result, CompletedResult)
        session._calculate_calories_per_pack.assert_not_awaited()
        assert coordinator.product_updates
        assert all("calories" not in update for update in coordinator.product_updates)
