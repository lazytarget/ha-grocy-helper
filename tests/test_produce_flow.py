"""Tests for SCAN_PRODUCE / SCAN_PRODUCE_CONFIRM lifecycle (Area 7b).

Covers the two-form produce workflow end-to-end: initial render, validation,
happy-path transition between forms, and the submit path with stock creation
and ingredient consumption.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from custom_components.grocy_helper.scan_session import ScanSession
from custom_components.grocy_helper.scan_types import (
    CompletedResult,
    FormRequest,
    Step,
)

from tests.conftest import (
    FakeBarcodeBuddyAPI,
    FakeCoordinator,
    FakeGrocyAPI,
    make_master_data,
    make_product,
    make_recipe,
    make_stock_info,
)


def _make_produce_session(
    recipe: dict | None = None,
    product: dict | None = None,
    scan_options: dict | None = None,
) -> ScanSession:
    """Build a ScanSession prepared for produce-flow tests."""
    product = product or make_product(id=42, name="Yoghurt", location_id=1, qu_id=1)
    recipe = recipe or make_recipe(
        id=1,
        name="Greek Yoghurt",
        product_id=42,
        base_servings=4,
        desired_servings=4,
    )

    grocy_api = FakeGrocyAPI()
    grocy_api.register_product(product, barcodes=[])

    coordinator = FakeCoordinator(
        grocy_api=grocy_api,
        master_data=make_master_data(products=[product], recipes=[recipe]),
    )
    session = ScanSession(
        coordinator=coordinator,
        api_bbuddy=FakeBarcodeBuddyAPI(),
        config_entry_data=scan_options or {},
    )

    # Wire up produce flow state:
    session._state.current_recipe = recipe
    session._state.set_stock_info(make_stock_info(product=product, barcodes=[]))
    session.barcode_queue = [f"grcy:r:{recipe['id']}"]

    # Stub _step_scan_queue so tests don't run more of the queue machinery.
    session._step_scan_queue = AsyncMock(return_value=CompletedResult(summary="done"))

    return session


# ── Form 1: _step_produce ────────────────────────────────────────────────────


async def test_produce_form_renders_on_first_call():
    """_step_produce(None) returns a FormRequest for SCAN_PRODUCE."""
    session = _make_produce_session()

    result = await session._step_produce(user_input=None)

    assert isinstance(result, FormRequest)
    assert result.step_id == Step.SCAN_PRODUCE
    field_keys = [f.key for f in result.fields]
    assert "produce_servings" in field_keys
    assert "produce_amount" in field_keys
    assert "produce_location_id" in field_keys


async def test_produce_form_includes_price_field_when_cost_available():
    """_step_produce(None) includes produce_price when fulfillment returns a cost."""
    grocy_api = FakeGrocyAPI()
    product = make_product(id=1, name="Cheese", location_id=1, qu_id=1)
    recipe = make_recipe(id=7, name="Cheesecake", product_id=1, base_servings=2)
    grocy_api.register_product(product, barcodes=[])
    # Override fulfillment to return a non-zero recipe cost
    grocy_api.get_recipe_fulfillment = AsyncMock(return_value={"costs": 8.0, "calories": 0})

    coordinator = FakeCoordinator(
        grocy_api=grocy_api,
        master_data=make_master_data(products=[product], recipes=[recipe]),
    )
    session = ScanSession(
        coordinator=coordinator,
        api_bbuddy=FakeBarcodeBuddyAPI(),
        config_entry_data={},
    )
    session._state.current_recipe = recipe
    session._state.set_stock_info(make_stock_info(product=product, barcodes=[]))
    session.barcode_queue = ["grcy:r:7"]
    session._step_scan_queue = AsyncMock(return_value=CompletedResult(summary="done"))

    result = await session._step_produce(user_input=None)

    assert isinstance(result, FormRequest)
    assert any(f.key == "produce_price" for f in result.fields)


async def test_produce_validation_rejects_servings_below_1():
    """produce_servings < 1 causes a validation error re-render."""
    session = _make_produce_session()
    # Seed cached fields (normally set on first render)
    await session._step_produce(user_input=None)

    result = await session._step_produce(
        user_input={
            "produce_servings": 0,
            "produce_amount": 0,
            "produce_location_id": "1",
            "produce_consume_ingredients": True,
        }
    )

    assert isinstance(result, FormRequest)
    assert result.step_id == Step.SCAN_PRODUCE
    assert "produce_servings" in result.errors


async def test_produce_validation_rejects_amount_exceeding_servings():
    """produce_amount > produce_servings causes a validation error re-render."""
    session = _make_produce_session()
    await session._step_produce(user_input=None)

    result = await session._step_produce(
        user_input={
            "produce_servings": 2,
            "produce_amount": 5,
            "produce_location_id": "1",
            "produce_consume_ingredients": True,
        }
    )

    assert isinstance(result, FormRequest)
    assert result.step_id == Step.SCAN_PRODUCE
    assert "produce_amount" in result.errors


async def test_produce_valid_submission_transitions_to_confirm():
    """A valid form 1 submission immediately renders the confirm form."""
    session = _make_produce_session()
    await session._step_produce(user_input=None)

    result = await session._step_produce(
        user_input={
            "produce_servings": 4,
            "produce_amount": 2,
            "produce_location_id": "1",
            "produce_consume_ingredients": True,
        }
    )

    assert isinstance(result, FormRequest)
    assert result.step_id == Step.SCAN_PRODUCE_CONFIRM


async def test_produce_valid_submission_stashes_produce_input():
    """Values from form 1 are stashed in _produce_input before moving on."""
    session = _make_produce_session()
    await session._step_produce(user_input=None)

    await session._step_produce(
        user_input={
            "produce_servings": 3,
            "produce_amount": 1,
            "produce_location_id": "1",
            "produce_consume_ingredients": False,
        }
    )

    inp = session._produce_input
    assert inp["produce_servings"] == 3
    assert inp["produce_amount"] == 1
    assert inp["produce_location_id"] == 1
    assert inp["produce_consume_ingredients"] is False


# ── Form 2: _step_produce_confirm ────────────────────────────────────────────


def _seed_produce_input(session: ScanSession, **overrides: object) -> None:
    """Pre-populate _produce_input as if form 1 was submitted."""
    session._produce_input = {
        "fulfillment_calories": None,
        "produce_consume_ingredients": True,
        "produce_servings": 4,
        "produce_amount": 2,
        "produce_location_id": 1,
        "produce_price": None,
        **overrides,
    }


async def test_produce_confirm_renders_summary():
    """_step_produce_confirm(None) returns a FormRequest for SCAN_PRODUCE_CONFIRM."""
    session = _make_produce_session()
    _seed_produce_input(session)

    result = await session._step_produce_confirm(user_input=None)

    assert isinstance(result, FormRequest)
    assert result.step_id == Step.SCAN_PRODUCE_CONFIRM
    # Summary placeholders should reference the recipe name
    assert "Greek Yoghurt" in result.description_placeholders.get("summary", "")


async def test_produce_confirm_submit_calls_add_stock():
    """Submitting confirm form creates stock for produce_amount > 0."""
    session = _make_produce_session()
    _seed_produce_input(session, produce_amount=2)

    await session._step_produce_confirm(user_input={"produce_print": False})

    # FakeCoordinator.add_stock delegates to FakeGrocyAPI.add_stock_product
    assert len(session._api_grocy._added_stock) == 1
    product_id, data = session._api_grocy._added_stock[0]
    assert product_id == 42
    assert data["amount"] == 2
    assert data["transaction_type"] == "self-production"
    assert data["location_id"] == 1


async def test_produce_confirm_submit_skips_stock_when_amount_is_0():
    """produce_amount=0 means nothing to stock; add_stock must not be called."""
    session = _make_produce_session()
    _seed_produce_input(session, produce_amount=0)

    await session._step_produce_confirm(user_input={"produce_print": False})

    assert len(session._api_grocy._added_stock) == 0


async def test_produce_confirm_submit_skips_ingredients_when_opted_out():
    """produce_consume_ingredients=False must not call consume_stock_product."""
    grocy_api = FakeGrocyAPI()
    grocy_api.consume_stock_product = AsyncMock(return_value={})
    grocy_api.get_recipes_pos_resolved = AsyncMock(
        return_value=[
            {
                "product_id": 99,
                "recipe_amount": 1.0,
                "stock_amount": 1.0,
                "only_check_single_unit_in_stock": 0,
            }
        ]
    )
    product = make_product(id=42, name="Yoghurt", location_id=1, qu_id=1)
    recipe = make_recipe(id=1, name="Greek Yoghurt", product_id=42)
    grocy_api.register_product(product, barcodes=[])
    coordinator = FakeCoordinator(
        grocy_api=grocy_api,
        master_data=make_master_data(products=[product], recipes=[recipe]),
    )
    session = ScanSession(
        coordinator=coordinator,
        api_bbuddy=FakeBarcodeBuddyAPI(),
        config_entry_data={},
    )
    session._state.current_recipe = recipe
    session._state.set_stock_info(make_stock_info(product=product, barcodes=[]))
    session.barcode_queue = ["grcy:r:1"]
    session._step_scan_queue = AsyncMock(return_value=CompletedResult(summary="done"))
    _seed_produce_input(session, produce_consume_ingredients=False)

    await session._step_produce_confirm(user_input={"produce_print": False})

    grocy_api.consume_stock_product.assert_not_called()


async def test_produce_confirm_submit_calls_step_scan_queue():
    """After produce confirm, the barcode is popped and _step_scan_queue called."""
    session = _make_produce_session()
    _seed_produce_input(session)
    initial_queue_len = len(session.barcode_queue)

    await session._step_produce_confirm(user_input={"produce_print": False})

    # Barcode must have been popped
    assert len(session.barcode_queue) == initial_queue_len - 1
    session._step_scan_queue.assert_awaited_once()
