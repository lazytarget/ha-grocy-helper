"""Tests for OpenFoodFacts calorie conversion behavior."""

from __future__ import annotations

from unittest.mock import AsyncMock

from custom_components.grocy_helper.const import CONF_ENABLE_CALORIES
from custom_components.grocy_helper.scan_form_builders import ScanFormBuilder
from custom_components.grocy_helper.scan_session import ScanSession
from custom_components.grocy_helper.scan_types import CompletedResult

from tests.conftest import (
    FakeBarcodeBuddyAPI,
    FakeCoordinator,
    FakeGrocyAPI,
    make_master_data,
    make_product,
    make_stock_info,
)


def _get_field(fields: list, key: str):
    return next((f for f in fields if f.key == key), None)


class TestCalorieConversion100g:
    """100g calorie basis conversion behavior."""

    async def test_calories_are_ceiled_per_stock_unit(self):
        """100g kcal values are converted to per-stock-unit and rounded up."""
        grocy_api = FakeGrocyAPI()
        bbuddy_api = FakeBarcodeBuddyAPI()

        class CapturingCoordinator(FakeCoordinator):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.product_updates: list[dict] = []

            async def update_product(self, product_id: int, changes: dict) -> dict:
                self.product_updates.append(changes)
                return {}

        coordinator = CapturingCoordinator(
            grocy_api=grocy_api,
            bbuddy_api=bbuddy_api,
            master_data=make_master_data(),
        )

        product = make_product(id=42, name="Yoghurt", qu_id=1)
        session = ScanSession(
            coordinator=coordinator,
            api_bbuddy=bbuddy_api,
            scan_options={CONF_ENABLE_CALORIES: True},
            config_entry_data={},
        )
        session._state.set_stock_info(make_stock_info(product=product, barcodes=[]))
        session._state.current_product_openfoodfacts = {
            "nutriments": {"energy_kcal_100g": 88}
        }
        session._step_add_product_parent = AsyncMock(
            return_value=CompletedResult(summary="ok")
        )

        session._convert_quantity = AsyncMock(
            return_value={
                "from_amount": 1,
                "from_qu_name": "Piece",
                "to_amount": 237,
                "to_qu_name": "g",
            }
        )

        result = await session._step_update_product_details({})

        assert isinstance(result, CompletedResult)
        assert coordinator.product_updates
        assert coordinator.product_updates[-1].get("calories") == 209


class TestCalorieOptionDefault:
    """Defaults for calories options in scan options form."""

    def test_enable_calories_defaults_to_true_when_missing(self):
        """Reconfigure form should suggest enabled calories if key is absent."""
        grocy_api = FakeGrocyAPI()
        coordinator = FakeCoordinator(
            grocy_api=grocy_api,
            master_data=make_master_data(),
        )
        builder = ScanFormBuilder(coordinator)

        fields = builder.build_scan_options_fields({})
        calories_field = _get_field(fields, CONF_ENABLE_CALORIES)

        assert calories_field is not None
        assert calories_field.suggested_value is True

    def test_session_defaults_enable_calories_when_missing(self):
        """Runtime scan options should default calories to enabled."""
        grocy_api = FakeGrocyAPI()
        bbuddy_api = FakeBarcodeBuddyAPI()
        coordinator = FakeCoordinator(
            grocy_api=grocy_api,
            bbuddy_api=bbuddy_api,
            master_data=make_master_data(),
        )

        session = ScanSession(
            coordinator=coordinator,
            api_bbuddy=bbuddy_api,
            scan_options=None,
            config_entry_data={},
        )

        assert session.scan_options.get(CONF_ENABLE_CALORIES) is True
