"""Tests for Areas 7c and 7d.

7c: _handle_scan_error error recovery branch
    Verifies the method returns a SCAN_PROCESS FormRequest with the
    exception message surfaced as an error key.

7d: SCAN_ADD_PRODUCT with location validation failures
    Verifies that validate_product_location catches the freezer constraint and
    that _step_add_product re-renders the form with the location_is_freezer error.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from custom_components.grocy_helper.scan_session import ScanSession
from custom_components.grocy_helper.scan_types import (
    AbortResult,
    CompletedResult,
    FormRequest,
    Step,
)

from tests.conftest import (
    FakeBarcodeBuddyAPI,
    FakeCoordinator,
    FakeGrocyAPI,
    make_location,
    make_master_data,
    make_product,
    make_stock_info,
)


# ── Helpers ──────────────────────────────────────────────────────────


def _make_process_session(
    product: dict | None = None,
    scan_options: dict | None = None,
) -> ScanSession:
    """Build a ScanSession ready for SCAN_PROCESS / error tests."""
    product = product or make_product(id=1, name="Milk", location_id=1, qu_id=1)
    grocy_api = FakeGrocyAPI()
    grocy_api.register_product(product, barcodes=["111"])
    coordinator = FakeCoordinator(
        grocy_api=grocy_api,
        master_data=make_master_data(products=[product]),
    )
    session = ScanSession(
        coordinator=coordinator,
        api_bbuddy=FakeBarcodeBuddyAPI(),
        config_entry_data=scan_options or {},
    )
    session._state.set_stock_info(make_stock_info(product=product, barcodes=[]))
    session.current_barcode = "111"
    session.barcode_queue = ["111"]
    return session


def _make_add_product_session(
    locations: list[dict] | None = None,
    scan_options: dict | None = None,
) -> ScanSession:
    """Build a ScanSession ready for SCAN_ADD_PRODUCT tests."""
    locations = locations or [
        make_location(id=1, name="Fridge", is_freezer=0),
        make_location(id=2, name="Freezer", is_freezer=1),
    ]
    grocy_api = FakeGrocyAPI()
    master = make_master_data(locations=locations)
    coordinator = FakeCoordinator(grocy_api=grocy_api, master_data=master)
    session = ScanSession(
        coordinator=coordinator,
        api_bbuddy=FakeBarcodeBuddyAPI(),
        config_entry_data=scan_options or {},
    )
    session.current_barcode = "NEW123"
    session.barcode_queue = ["NEW123"]
    return session


# ── Area 7c: _handle_scan_error ──────────────────────────────────────


class TestHandleScanError:
    """Tests for the _handle_scan_error recovery path."""

    def test_returns_form_request_for_scan_process(self):
        """_handle_scan_error always returns a SCAN_PROCESS FormRequest."""
        session = _make_process_session()
        exc = RuntimeError("BBuddy is down")
        errors: dict[str, str] = {}

        result = session._handle_scan_error(exc, errors)

        assert isinstance(result, FormRequest)
        assert result.step_id == Step.SCAN_PROCESS

    def test_error_contains_exception_message(self):
        """The 'Exception' key in errors holds the exception's string repr."""
        session = _make_process_session()
        exc = RuntimeError("connection refused")
        errors: dict[str, str] = {}

        result = session._handle_scan_error(exc, errors)

        assert "Exception" in result.errors
        assert "connection refused" in result.errors["Exception"]

    def test_uses_cached_fields_when_available(self):
        """Cached fields from previous render are reused in the error response."""
        from custom_components.grocy_helper.scan_types import FormField, FieldType

        session = _make_process_session()
        sentinel_field = FormField(key="sentinel", field_type=FieldType.TEXT)
        session._cached_process_fields = [sentinel_field]

        result = session._handle_scan_error(ValueError("oops"), {})

        assert any(f.key == "sentinel" for f in result.fields)

    def test_uses_empty_fields_when_no_cache(self):
        """Without cached fields, the error response uses an empty field list."""
        session = _make_process_session()
        session._cached_process_fields = None

        result = session._handle_scan_error(ValueError("oops"), {})

        assert result.fields == []

    def test_description_placeholders_contain_product_name(self):
        """description_placeholders includes the current product name."""
        session = _make_process_session()

        result = session._handle_scan_error(RuntimeError("err"), {})

        name = result.description_placeholders.get("name")
        assert name == "Milk"

    async def test_scan_process_step_triggers_error_on_raised_exception(self):
        """When _execute_scan_action raises, _step_scan_process returns a FormRequest."""
        session = _make_process_session()
        # Arrange: executing the scan action raises an error
        session._execute_scan_action = AsyncMock(side_effect=RuntimeError("network error"))
        # Stub the BBuddy mode setter so it doesn't fail
        session._set_bbuddy_mode = AsyncMock()
        # Stub _show_scan_process_form to return None so we reach execute
        session._show_scan_process_form = lambda *a, **kw: None

        result = await session._step_scan_process(user_input={"dummy": True})

        assert isinstance(result, FormRequest)
        assert result.step_id == Step.SCAN_PROCESS
        assert "Exception" in result.errors


# ── Area 7d: SCAN_ADD_PRODUCT freezer location validation ────────────


class TestAddProductLocationValidation:
    """Tests for validate_product_location and its effect on _step_add_product."""

    def test_validate_product_location_passes_for_normal_location(self):
        """Non-freezer product in non-freezer location returns empty errors."""
        from custom_components.grocy_helper.scan_product_builders import ProductDataBuilder

        master = make_master_data(
            locations=[
                make_location(id=1, name="Fridge", is_freezer=0),
            ]
        )
        coordinator = FakeCoordinator(master_data=master)
        builder = ProductDataBuilder(coordinator)
        product = make_product(id=1, name="Milk", location_id=1, should_not_be_frozen=0)
        product["should_not_be_frozen"] = 0
        product["location_id"] = 1

        errors = builder.validate_product_location(product)

        assert errors == {}

    def test_validate_product_location_fails_for_freezer_when_should_not_be_frozen(self):
        """Product with should_not_be_frozen=1 placed in freezer → location_is_freezer."""
        from custom_components.grocy_helper.scan_product_builders import ProductDataBuilder

        master = make_master_data(
            locations=[
                make_location(id=1, name="Fridge", is_freezer=0),
                make_location(id=2, name="Freezer", is_freezer=1),
            ]
        )
        coordinator = FakeCoordinator(master_data=master)
        builder = ProductDataBuilder(coordinator)
        product = make_product(id=1, name="Fresh Yoghurt", location_id=2, qu_id=1)
        product["should_not_be_frozen"] = 1
        product["location_id"] = 2

        errors = builder.validate_product_location(product)

        assert errors.get("location_id") == "location_is_freezer"

    def test_validate_product_location_passes_frozen_in_freezer(self):
        """Product that CAN be frozen placed in freezer → no error."""
        from custom_components.grocy_helper.scan_product_builders import ProductDataBuilder

        master = make_master_data(
            locations=[
                make_location(id=2, name="Freezer", is_freezer=1),
            ]
        )
        coordinator = FakeCoordinator(master_data=master)
        builder = ProductDataBuilder(coordinator)
        product = make_product(id=1, name="Frozen Peas", location_id=2, qu_id=1)
        product["should_not_be_frozen"] = 0
        product["location_id"] = 2

        errors = builder.validate_product_location(product)

        assert errors == {}

    async def test_step_add_product_rerenders_form_on_freezer_error(self):
        """When validate_product_location returns location_is_freezer, the form re-renders."""
        session = _make_add_product_session()

        # Simulate a user submitting a new product with location_id = freezer (id=2)
        # and should_not_be_frozen=1 (the product should not be frozen)
        user_input = {
            "name": "Fresh Milk",
            "location_id": "2",  # Freezer
            "qu_id_stock": "1",
            "qu_id_purchase": "1",
            "qu_id_consume": "1",
            "qu_id_price": "1",
            "default_best_before_days": "7",
            "default_best_before_days_after_open": "3",
            "should_not_be_frozen": True,
            "treat_opened_as_out_of_stock": False,
            "product_group_id": None,
            "product_id": "-1",  # -1 means "create new"
        }

        result = await session._step_add_product(user_input=user_input)

        assert isinstance(result, FormRequest)
        assert result.step_id == Step.SCAN_ADD_PRODUCT
        assert result.errors.get("location_id") == "location_is_freezer"

    async def test_step_add_product_proceeds_when_location_is_valid(self):
        """Valid location allows product creation to proceed past the form."""
        session = _make_add_product_session()
        # Stub downstream steps to avoid running the full creation chain
        session._step_add_product_barcode = AsyncMock(
            return_value=CompletedResult(summary="done")
        )

        user_input = {
            "name": "Fresh Milk",
            "location_id": "1",  # Fridge (not freezer)
            "qu_id_stock": "1",
            "qu_id_purchase": "1",
            "qu_id_consume": "1",
            "qu_id_price": "1",
            "default_best_before_days": "7",
            "default_best_before_days_after_open": "3",
            "should_not_be_frozen": True,
            "treat_opened_as_out_of_stock": False,
            "product_group_id": None,
            "product_id": "-1",
        }

        result = await session._step_add_product(user_input=user_input)

        # Should proceed past add-product (no location_is_freezer error)
        assert not isinstance(result, AbortResult)
        session._step_add_product_barcode.assert_awaited_once()
