"""Tests for OpenFoodFacts calorie conversion behavior."""

from __future__ import annotations

from unittest.mock import AsyncMock

from custom_components.grocy_helper.const import CONF_ENABLE_CALORIES
from custom_components.grocy_helper.scan_product_builders import ProductDataBuilder
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
            "product_quantity": 100,
            "product_quantity_unit": "g",
            "nutriments": {"energy_kcal_100g": 88},
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


class TestCalorieConversion100ml:
    """100ml calorie basis conversion behavior."""

    async def test_prefers_100ml_for_liquid_products(self):
        """Liquid OFF data should use 100ml calories when available."""
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

        product = make_product(id=43, name="Oat Drink", qu_id=1)
        session = ScanSession(
            coordinator=coordinator,
            api_bbuddy=bbuddy_api,
            scan_options={CONF_ENABLE_CALORIES: True},
            config_entry_data={},
        )
        session._state.set_stock_info(make_stock_info(product=product, barcodes=[]))
        session._state.current_product_openfoodfacts = {
            "product_quantity": 1000,
            "product_quantity_unit": "ml",
            "nutriments": {
                "energy_kcal_100g": 45,
            },
        }
        session._step_add_product_parent = AsyncMock(
            return_value=CompletedResult(summary="ok")
        )
        session._convert_quantity = AsyncMock(
            return_value={
                "from_amount": 1,
                "from_qu_name": "Piece",
                "to_amount": 330,
                "to_qu_name": "ml",
            }
        )

        result = await session._step_update_product_details({})

        assert isinstance(result, CompletedResult)
        assert coordinator.product_updates
        assert coordinator.product_updates[-1].get("calories") == 149

    def test_parser_uses_canonical_kcal_field_for_liquid_units(self):
        """Liquid products still read kcal from canonical OFF field."""
        coordinator = FakeCoordinator(
            grocy_api=FakeGrocyAPI(),
            bbuddy_api=FakeBarcodeBuddyAPI(),
            master_data=make_master_data(),
        )
        builder = ProductDataBuilder(coordinator)

        (
            _product_quantity,
            _product_quantity_unit,
            is_liquid,
            _is_weight,
            kcal,
        ) = builder.parse_openfoodfacts_data(
            user_input={},
            current_product_openfoodfacts={
                "product_quantity": 1000,
                "product_quantity_unit": "ml",
                "nutriments": {
                    "energy_kcal_100g": 45,
                },
            },
        )

        assert is_liquid is True
        assert kcal == 45.0


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


class TestCalorieUnsupportedBasis:
    """Unsupported OFF quantity bases should skip calorie updates."""

    async def test_piece_unit_does_not_write_calories(self):
        """Per-piece style quantity unit should not be converted via g/ml."""
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

        product = make_product(id=44, name="Chocolate Bar", qu_id=1)
        session = ScanSession(
            coordinator=coordinator,
            api_bbuddy=bbuddy_api,
            scan_options={CONF_ENABLE_CALORIES: True},
            config_entry_data={},
        )
        session._state.set_stock_info(make_stock_info(product=product, barcodes=[]))
        session._state.current_product_openfoodfacts = {
            "product_quantity": 1,
            "product_quantity_unit": "Piece",
            "nutriments": {"energy_kcal_100g": 480},
        }
        session._step_add_product_parent = AsyncMock(
            return_value=CompletedResult(summary="ok")
        )
        session._convert_quantity = AsyncMock(
            return_value={
                "from_amount": 1,
                "from_qu_name": "Piece",
                "to_amount": 50,
                "to_qu_name": "g",
            }
        )

        result = await session._step_update_product_details({})

        assert isinstance(result, CompletedResult)
        assert not coordinator.product_updates


class TestCalorieMissingConversion:
    """Supported OFF basis with missing QU conversion should not write calories."""

    async def test_weight_basis_without_conversion_skips_calories(self):
        """100g basis without stock->g conversion must not write calories."""
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

        product = make_product(id=45, name="Granola", qu_id=1)
        session = ScanSession(
            coordinator=coordinator,
            api_bbuddy=bbuddy_api,
            scan_options={CONF_ENABLE_CALORIES: True},
            config_entry_data={},
        )
        session._state.set_stock_info(make_stock_info(product=product, barcodes=[]))
        session._state.current_product_openfoodfacts = {
            "product_quantity": 100,
            "product_quantity_unit": "g",
            "nutriments": {"energy_kcal_100g": 410},
        }
        session._step_add_product_parent = AsyncMock(
            return_value=CompletedResult(summary="ok")
        )
        session._convert_quantity = AsyncMock(return_value=None)

        result = await session._step_update_product_details({})

        assert isinstance(result, CompletedResult)
        for update in coordinator.product_updates:
            assert "calories" not in update

    async def test_liquid_basis_without_conversion_skips_calories(self):
        """100ml basis without stock->ml conversion must not write calories."""
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

        product = make_product(id=46, name="Juice", qu_id=1)
        session = ScanSession(
            coordinator=coordinator,
            api_bbuddy=bbuddy_api,
            scan_options={CONF_ENABLE_CALORIES: True},
            config_entry_data={},
        )
        session._state.set_stock_info(make_stock_info(product=product, barcodes=[]))
        session._state.current_product_openfoodfacts = {
            "product_quantity": 1000,
            "product_quantity_unit": "ml",
            "nutriments": {"energy_kcal_100g": 46},
        }
        session._step_add_product_parent = AsyncMock(
            return_value=CompletedResult(summary="ok")
        )
        session._convert_quantity = AsyncMock(return_value=None)

        result = await session._step_update_product_details({})

        assert isinstance(result, CompletedResult)
        for update in coordinator.product_updates:
            assert "calories" not in update
