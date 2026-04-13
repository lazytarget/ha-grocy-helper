"""Tests for transfer state-machine steps in ScanSession (Area 7a).

Covers SCAN_TRANSFER_START and SCAN_TRANSFER_INPUT end-to-end behavior.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from custom_components.grocy_helper.scan_session import ScanSession
from custom_components.grocy_helper.scan_types import (
    AbortResult,
    CompletedResult,
    FormRequest,
)

from tests.conftest import (
    FakeBarcodeBuddyAPI,
    FakeCoordinator,
    FakeGrocyAPI,
    make_master_data,
    make_product,
    make_stock_info,
)


def _make_transfer_session(
    stock_entries: list[dict] | None = None,
    product: dict | None = None,
) -> ScanSession:
    """Build a ScanSession prepared for transfer flow tests."""
    grocy_api = FakeGrocyAPI()
    bbuddy_api = FakeBarcodeBuddyAPI()
    product = product or make_product(id=42, name="Milk", location_id=1, qu_id=1)

    coordinator = FakeCoordinator(
        grocy_api=grocy_api,
        bbuddy_api=bbuddy_api,
        master_data=make_master_data(products=[product]),
    )
    session = ScanSession(
        coordinator=coordinator,
        api_bbuddy=bbuddy_api,
        config_entry_data={},
    )

    if stock_entries is not None:
        session._state.set_stock_info(make_stock_info(product=product, barcodes=[]))
        session._state.current_stock_entries = stock_entries

    return session


def _stock_entry(
    *,
    entry_id: int,
    stock_id: str,
    amount: float,
    location_id: int,
    best_before_date: str = "2026-06-01",
) -> dict:
    return {
        "id": entry_id,
        "stock_id": stock_id,
        "amount": amount,
        "location_id": location_id,
        "best_before_date": best_before_date,
    }


async def test_transfer_start_aborts_when_no_product_info():
    """Without current product stock info, transfer start aborts."""
    session = _make_transfer_session(stock_entries=None)

    result = await session._step_transfer_start(user_input=None)

    assert isinstance(result, AbortResult)
    assert "No product info" in result.reason


async def test_transfer_start_aborts_when_no_stock_entries():
    """With product but no stock entries, transfer start aborts."""
    session = _make_transfer_session(stock_entries=[])

    result = await session._step_transfer_start(user_input=None)

    assert isinstance(result, AbortResult)
    assert "No stock entries" in result.reason


async def test_transfer_start_with_multiple_entries_shows_selection_form():
    """Multiple stock entries render SCAN_TRANSFER_START selection form."""
    session = _make_transfer_session(
        stock_entries=[
            _stock_entry(entry_id=10, stock_id="s10", amount=3, location_id=1),
            _stock_entry(entry_id=11, stock_id="s11", amount=2, location_id=1),
        ]
    )

    result = await session._step_transfer_start(user_input=None)

    assert isinstance(result, FormRequest)
    assert result.step_id == "scan_transfer_start"
    field = next((f for f in result.fields if f.key == "stock_entry_id"), None)
    assert field is not None
    assert field.default == "10"


async def test_transfer_start_selects_entry_and_moves_to_transfer_input():
    """Choosing stock entry narrows state to one entry and proceeds."""
    session = _make_transfer_session(
        stock_entries=[
            _stock_entry(entry_id=10, stock_id="s10", amount=3, location_id=1),
            _stock_entry(entry_id=11, stock_id="s11", amount=2, location_id=1),
        ]
    )

    result = await session._step_transfer_start(user_input={"stock_entry_id": "11"})

    assert isinstance(result, FormRequest)
    assert result.step_id == "scan_transfer_input"
    assert len(session.current_stock_entries) == 1
    assert session.current_stock_entries[0]["id"] == 11


async def test_transfer_input_shows_amount_and_location_fields():
    """Transfer input form includes amount and target location when amount > 1."""
    session = _make_transfer_session(
        stock_entries=[
            _stock_entry(entry_id=10, stock_id="s10", amount=3, location_id=1)
        ]
    )

    result = await session._step_transfer_input(user_input=None)

    assert isinstance(result, FormRequest)
    assert result.step_id == "scan_transfer_input"
    assert any(f.key == "amount" for f in result.fields)
    assert any(f.key == "location_to_id" for f in result.fields)


async def test_transfer_input_aborts_when_multiple_entries_remain():
    """Transfer input requires exactly one chosen stock entry."""
    session = _make_transfer_session(
        stock_entries=[
            _stock_entry(entry_id=10, stock_id="s10", amount=3, location_id=1),
            _stock_entry(entry_id=11, stock_id="s11", amount=2, location_id=1),
        ]
    )

    result = await session._step_transfer_input(user_input=None)

    assert isinstance(result, AbortResult)
    assert "one chosen stock entry" in result.reason


async def test_transfer_input_posts_transfer_and_completes_queue_step():
    """Submitting transfer sends Grocy payload and advances queue."""
    captured: dict[str, object] = {}

    class CapturingGrocyAPI(FakeGrocyAPI):
        async def transfer_stock_entry(self, product_id: int, data: dict):
            captured["product_id"] = product_id
            captured["data"] = data
            return {"ok": True}

    grocy_api = CapturingGrocyAPI()
    bbuddy_api = FakeBarcodeBuddyAPI()
    product = make_product(id=42, name="Milk", location_id=1, qu_id=1)
    coordinator = FakeCoordinator(
        grocy_api=grocy_api,
        bbuddy_api=bbuddy_api,
        master_data=make_master_data(products=[product]),
    )
    session = ScanSession(
        coordinator=coordinator,
        api_bbuddy=bbuddy_api,
        config_entry_data={},
    )
    session._state.set_stock_info(make_stock_info(product=product, barcodes=[]))
    session._state.current_stock_entries = [
        _stock_entry(entry_id=10, stock_id="s10", amount=3, location_id=1)
    ]
    session.barcode_queue = ["1234567890123"]
    session._step_scan_queue = AsyncMock(return_value=CompletedResult(summary="ok"))

    result = await session._step_transfer_input(
        user_input={"amount": 2, "location_to_id": "2"}
    )

    assert isinstance(result, CompletedResult)
    assert captured["product_id"] == 42
    assert captured["data"] == {
        "amount": 2,
        "location_id_from": 1,
        "location_id_to": 2,
        "stock_entry_id": "s10",
    }
    assert session.barcode_results[-1] == "Milk transferred to loc #2"


async def test_transfer_input_defaults_amount_to_stock_entry_amount():
    """When amount is omitted, transfer uses stock entry amount by default."""
    captured: dict[str, object] = {}

    class CapturingGrocyAPI(FakeGrocyAPI):
        async def transfer_stock_entry(self, product_id: int, data: dict):
            captured["data"] = data
            return {"ok": True}

    grocy_api = CapturingGrocyAPI()
    bbuddy_api = FakeBarcodeBuddyAPI()
    product = make_product(id=42, name="Milk", location_id=1, qu_id=1)
    coordinator = FakeCoordinator(
        grocy_api=grocy_api,
        bbuddy_api=bbuddy_api,
        master_data=make_master_data(products=[product]),
    )
    session = ScanSession(
        coordinator=coordinator,
        api_bbuddy=bbuddy_api,
        config_entry_data={},
    )
    session._state.set_stock_info(make_stock_info(product=product, barcodes=[]))
    session._state.current_stock_entries = [
        _stock_entry(entry_id=10, stock_id="s10", amount=3, location_id=1)
    ]
    session.barcode_queue = ["1234567890123"]
    session._step_scan_queue = AsyncMock(return_value=CompletedResult(summary="ok"))

    result = await session._step_transfer_input(user_input={"location_to_id": "2"})

    assert isinstance(result, CompletedResult)
    assert captured["data"]["amount"] == 3
