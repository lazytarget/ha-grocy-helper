"""Tests for ScanQueue — persistent barcode queue with dynamic mode.

Written BEFORE the implementation (TDD).
"""

from __future__ import annotations

import pytest

from custom_components.grocy_helper.const import SCAN_MODE
from custom_components.grocy_helper.queue import QueueStatus, ScanQueue

from tests.conftest import FakeStore


# ── Helpers ──────────────────────────────────────────────────────────


def _make_queue(store: FakeStore | None = None) -> ScanQueue:
    """Create a ScanQueue backed by a FakeStore."""
    return ScanQueue(store=store or FakeStore())


# ── Tests: Adding items ─────────────────────────────────────────────


async def test_add_item_to_queue():
    """Adding a barcode creates a PENDING queue item."""
    queue = _make_queue()
    item = await queue.async_add("1234567890123")

    assert item is not None
    assert item.barcode == "1234567890123"
    assert item.status == QueueStatus.PENDING
    assert item.id  # non-empty UUID
    assert item.error is None
    assert item.result is None


async def test_add_item_uses_current_mode_when_no_explicit_mode():
    """When no mode is passed, the queue's current_mode is used."""
    queue = _make_queue()
    queue._current_mode = SCAN_MODE.ADD_TO_SHOPPING_LIST

    item = await queue.async_add("1234567890123")

    assert item is not None
    assert item.mode == SCAN_MODE.ADD_TO_SHOPPING_LIST


async def test_add_item_with_explicit_mode():
    """An explicit mode overrides the current_mode."""
    queue = _make_queue()
    item = await queue.async_add("1234567890123", mode=SCAN_MODE.CONSUME)

    assert item is not None
    assert item.mode == SCAN_MODE.CONSUME


async def test_add_item_with_metadata():
    """Structured metadata is stored on the queue item."""
    queue = _make_queue()
    meta = {"q": 2, "p": 25.0, "u": "st"}
    item = await queue.async_add("1234567890123", metadata=meta)

    assert item is not None
    assert item.metadata == meta


# ── Tests: Mode barcode detection ───────────────────────────────────


async def test_mode_barcode_updates_current_mode():
    """Scanning a SCAN_MODE value switches current_mode, returns None."""
    queue = _make_queue()
    assert queue.current_mode == SCAN_MODE.PURCHASE  # initial default

    result = await queue.async_add("BBUDDY-AS")

    assert result is None  # not queued as an item
    assert queue.current_mode == SCAN_MODE.ADD_TO_SHOPPING_LIST


async def test_mode_barcode_all_modes():
    """All SCAN_MODE string values are detected as mode switches."""
    queue = _make_queue()
    for mode in SCAN_MODE:
        result = await queue.async_add(mode.value)
        assert result is None, f"Mode {mode.value} should not create a queue item"
        assert queue.current_mode == mode


async def test_current_mode_initializes_to_purchase():
    """Default current_mode is PURCHASE."""
    queue = _make_queue()
    assert queue.current_mode == SCAN_MODE.PURCHASE


# ── Tests: Filtering ────────────────────────────────────────────────


async def test_get_pending_items_filters_resolved():
    """get_pending_items only returns PENDING items."""
    queue = _make_queue()
    item1 = await queue.async_add("111")
    item2 = await queue.async_add("222")
    item3 = await queue.async_add("333")

    await queue.async_mark_resolved(item1.id, "OK")
    await queue.async_mark_failed(item3.id, "error")

    pending = queue.get_pending_items()
    assert len(pending) == 1
    assert pending[0].id == item2.id


async def test_get_failed_items():
    """get_failed_items returns only FAILED items."""
    queue = _make_queue()
    item1 = await queue.async_add("111")
    item2 = await queue.async_add("222")

    await queue.async_mark_failed(item2.id, "something broke")

    failed = queue.get_failed_items()
    assert len(failed) == 1
    assert failed[0].id == item2.id
    assert failed[0].error == "something broke"


# ── Tests: Status transitions ───────────────────────────────────────


async def test_mark_resolved_updates_status():
    """Marking resolved sets status and result text."""
    queue = _make_queue()
    item = await queue.async_add("111")

    await queue.async_mark_resolved(item.id, "Purchased: Milk x1")

    resolved = [i for i in queue._items if i.id == item.id]
    assert len(resolved) == 1
    assert resolved[0].status == QueueStatus.RESOLVED
    assert resolved[0].result == "Purchased: Milk x1"


async def test_mark_failed_updates_status_and_error():
    """Marking failed sets status and error text."""
    queue = _make_queue()
    item = await queue.async_add("111")

    await queue.async_mark_failed(item.id, "Product not found")

    failed = [i for i in queue._items if i.id == item.id]
    assert len(failed) == 1
    assert failed[0].status == QueueStatus.FAILED
    assert failed[0].error == "Product not found"


# ── Tests: Removal ──────────────────────────────────────────────────


async def test_remove_item():
    """Removing an item by ID removes it from the queue."""
    queue = _make_queue()
    item = await queue.async_add("111")

    removed = await queue.async_remove(item.id)

    assert removed is True
    assert len(queue._items) == 0


async def test_remove_nonexistent_item():
    """Removing a non-existent ID returns False."""
    queue = _make_queue()
    removed = await queue.async_remove("nonexistent-id")
    assert removed is False


async def test_clear_resolved():
    """clear_resolved removes only RESOLVED items."""
    queue = _make_queue()
    item1 = await queue.async_add("111")
    item2 = await queue.async_add("222")
    item3 = await queue.async_add("333")

    await queue.async_mark_resolved(item1.id, "OK")
    await queue.async_mark_failed(item3.id, "err")

    await queue.async_clear_resolved()

    assert len(queue._items) == 2  # pending + failed remain
    ids = [i.id for i in queue._items]
    assert item1.id not in ids
    assert item2.id in ids
    assert item3.id in ids


# ── Tests: Persistence ──────────────────────────────────────────────


async def test_persistence_round_trip():
    """Items and current_mode survive save → new instance → load."""
    store = FakeStore()
    queue1 = ScanQueue(store=store)
    await queue1.async_add("111")
    await queue1.async_add("222")
    # Switch mode
    await queue1.async_add("BBUDDY-AS")

    # Create a new queue instance backed by the same store
    queue2 = ScanQueue(store=store)
    await queue2.async_load()

    assert queue2.current_mode == SCAN_MODE.ADD_TO_SHOPPING_LIST
    assert len(queue2.get_pending_items()) == 2
    barcodes = [i.barcode for i in queue2.get_pending_items()]
    assert "111" in barcodes
    assert "222" in barcodes


async def test_current_mode_persists_across_reload():
    """current_mode survives save → load cycle."""
    store = FakeStore()
    queue1 = ScanQueue(store=store)
    await queue1.async_add("BBUDDY-CS")  # switch to CONSUME_SPOILED
    assert queue1.current_mode == SCAN_MODE.CONSUME_SPOILED

    queue2 = ScanQueue(store=store)
    await queue2.async_load()
    assert queue2.current_mode == SCAN_MODE.CONSUME_SPOILED


async def test_empty_load_initializes_defaults():
    """Loading from empty store gives empty queue + PURCHASE mode."""
    store = FakeStore()
    queue = ScanQueue(store=store)
    await queue.async_load()

    assert queue.current_mode == SCAN_MODE.PURCHASE
    assert len(queue.get_pending_items()) == 0


async def test_save_called_on_add(fake_store: FakeStore):
    """async_add persists the queue to the store."""
    queue = ScanQueue(store=fake_store)
    await queue.async_add("111")

    data = await fake_store.async_load()
    assert data is not None
    assert len(data["items"]) == 1


async def test_save_called_on_status_change(fake_store: FakeStore):
    """Status changes are persisted to the store."""
    queue = ScanQueue(store=fake_store)
    item = await queue.async_add("111")
    await queue.async_mark_resolved(item.id, "OK")

    data = await fake_store.async_load()
    assert data["items"][0]["status"] == QueueStatus.RESOLVED.value


# ── Serialization / Resilience ──────────────────────────────────────


async def test_enum_serialization_round_trip(fake_store: FakeStore):
    """Enums are serialized as strings, not enum instances."""
    queue = ScanQueue(store=fake_store)
    await queue.async_add("111")
    await queue.async_mark_resolved(
        queue._items[0].id, "OK"
    )

    raw = await fake_store.async_load()
    # current_mode must be a plain string
    assert isinstance(raw["current_mode"], str)
    # item status must be a plain string
    assert isinstance(raw["items"][0]["status"], str)

    # Must round-trip correctly
    queue2 = ScanQueue(store=fake_store)
    await queue2.async_load()
    assert queue2.current_mode == SCAN_MODE.PURCHASE
    assert queue2._items[0].status == QueueStatus.RESOLVED


async def test_load_invalid_mode_falls_back(fake_store: FakeStore):
    """Invalid persisted mode falls back to PURCHASE."""
    await fake_store.async_save({
        "current_mode": "INVALID_MODE",
        "items": [],
    })
    queue = ScanQueue(store=fake_store)
    await queue.async_load()
    assert queue.current_mode == SCAN_MODE.PURCHASE


async def test_load_invalid_item_status_skipped(fake_store: FakeStore):
    """Items with invalid status are skipped during load."""
    await fake_store.async_save({
        "current_mode": SCAN_MODE.PURCHASE.value,
        "items": [
            {
                "id": "good",
                "barcode": "111",
                "mode": SCAN_MODE.PURCHASE.value,
                "added_at": "2025-01-01T00:00:00+00:00",
                "status": "pending",
            },
            {
                "id": "bad",
                "barcode": "222",
                "mode": SCAN_MODE.PURCHASE.value,
                "added_at": "2025-01-01T00:00:00+00:00",
                "status": "UNKNOWN_STATUS",
            },
        ],
    })
    queue = ScanQueue(store=fake_store)
    await queue.async_load()
    assert len(queue._items) == 1
    assert queue._items[0].id == "good"
