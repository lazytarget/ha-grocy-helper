"""Tests for the Handle Queue OptionsFlow step (Phase 4).

Tests the ScanSession._step_handle_queue step which shows pending queue items
and feeds them into the normal scan workflow.
"""

from __future__ import annotations


from custom_components.grocy_helper.const import SCAN_MODE
from custom_components.grocy_helper.queue import QueueStatus, ScanQueue
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
    FakeStore,
    make_master_data,
    make_product,
)


# ── Helpers ──────────────────────────────────────────────────────────


async def _make_queue_with_items(
    barcodes: list[str],
    mode: str = SCAN_MODE.PURCHASE,
    grocy_api: FakeGrocyAPI | None = None,
) -> ScanQueue:
    """Create a ScanQueue with pending items."""
    store = FakeStore()
    queue = ScanQueue(store)
    await queue.async_load()
    # Set mode before adding
    queue._current_mode = mode
    for bc in barcodes:
        await queue.async_add(bc)
    return queue


def _make_session_with_queue(
    queue: ScanQueue,
    grocy_api: FakeGrocyAPI | None = None,
    bbuddy_api: FakeBarcodeBuddyAPI | None = None,
    products: list | None = None,
) -> ScanSession:
    """Create a ScanSession with queue attached to coordinator."""
    grocy_api = grocy_api or FakeGrocyAPI()
    bbuddy_api = bbuddy_api or FakeBarcodeBuddyAPI()
    coordinator = FakeCoordinator(
        grocy_api=grocy_api,
        bbuddy_api=bbuddy_api,
        master_data=make_master_data(products=products or []),
    )
    coordinator.queue = queue
    return ScanSession(
        coordinator=coordinator,
        api_bbuddy=bbuddy_api,
        config_entry_data={},
    )


# ── Tests: Summary form ─────────────────────────────────────────────


async def test_handle_queue_shows_pending_items():
    """Handle Queue shows a summary form with pending item count."""
    queue = await _make_queue_with_items(["111", "222", "333"])
    session = _make_session_with_queue(queue)

    result = await session.handle_step(Step.HANDLE_QUEUE, None)

    assert isinstance(result, FormRequest)
    assert result.step_id == Step.HANDLE_QUEUE
    # Should have at least one field (e.g. confirmation or item list)
    assert len(result.fields) >= 1
    # Description placeholders should include count
    assert "pending_count" in result.description_placeholders
    assert result.description_placeholders["pending_count"] == "3"


async def test_handle_queue_shows_failed_items_count():
    """Handle Queue includes failed items in the summary."""
    queue = await _make_queue_with_items(["111", "222"])
    # Mark one as failed
    pending = queue.get_pending_items()
    await queue.async_mark_failed(pending[0].id, "Some error")

    session = _make_session_with_queue(queue)
    result = await session.handle_step(Step.HANDLE_QUEUE, None)

    assert isinstance(result, FormRequest)
    assert "failed_count" in result.description_placeholders
    assert result.description_placeholders["failed_count"] == "1"
    assert result.description_placeholders["pending_count"] == "1"


async def test_handle_queue_empty_aborts():
    """Handle Queue with no items aborts with message."""
    queue = await _make_queue_with_items([])
    session = _make_session_with_queue(queue)

    result = await session.handle_step(Step.HANDLE_QUEUE, None)

    assert isinstance(result, AbortResult)
    assert "no" in result.reason.lower() or "empty" in result.reason.lower()


async def test_handle_queue_missing_queue_attribute_aborts():
    """Handle Queue when coordinator lacks queue attribute aborts."""
    queue = await _make_queue_with_items([])
    session = _make_session_with_queue(queue)

    # Remove the queue attribute to simulate misconfigured coordinator
    delattr(session._coordinator, "queue")

    result = await session.handle_step(Step.HANDLE_QUEUE, None)

    assert isinstance(result, AbortResult)
    assert result.reason == "No queue available"


async def test_handle_queue_confirm_false_aborts():
    """Submitting Handle Queue with confirm=False aborts without processing."""
    queue = await _make_queue_with_items(["111"])
    session = _make_session_with_queue(queue)

    # Show form
    result = await session.handle_step(Step.HANDLE_QUEUE, None)
    assert isinstance(result, FormRequest)

    # Submit with confirm=False — should abort
    result = await session.handle_step(Step.HANDLE_QUEUE, {"confirm": False})
    assert isinstance(result, AbortResult)
    assert "cancelled" in result.reason.lower()

    # Items should remain pending
    assert len(queue.get_pending_items()) == 1


async def test_handle_queue_feeds_barcodes_to_session():
    """Submitting Handle Queue populates session barcode_queue."""
    grocy_api = FakeGrocyAPI()
    product = make_product(id=42, name="Milk")
    grocy_api.register_product(product, barcodes=["111"])

    queue = await _make_queue_with_items(["111"])
    session = _make_session_with_queue(
        queue, grocy_api=grocy_api, products=[product]
    )

    # First call shows the form
    result = await session.handle_step(Step.HANDLE_QUEUE, None)
    assert isinstance(result, FormRequest)

    # Submit the form — should chain to scan processing
    result = await session.handle_step(Step.HANDLE_QUEUE, {"confirm": True})

    # The session should now be processing the barcode(s)
    # It should return either a FormRequest for scan_process or CompletedResult
    assert isinstance(result, (FormRequest, CompletedResult))


async def _drive_to_completion(session: ScanSession, result) -> CompletedResult | AbortResult:
    """Drive a ScanSession through forms until completion/abort.

    When a FormRequest is returned, submits empty values for all fields (the
    Handle Queue tests use optional fields only).
    """
    max_steps = 20
    for _ in range(max_steps):
        if isinstance(result, (CompletedResult, AbortResult)):
            return result
        if isinstance(result, FormRequest):
            # Build user_input from field defaults or empty
            user_input = {}
            for f in result.fields:
                if f.default is not None:
                    user_input[f.key] = f.default
                # Optional fields: just omit
            result = await session.handle_step(result.step_id, user_input)
        else:
            break
    return result


async def test_handle_queue_processes_known_products():
    """Known products in queue are processed through normal scan flow."""
    grocy_api = FakeGrocyAPI()
    product = make_product(id=42, name="Milk")
    grocy_api.register_product(product, barcodes=["7340011492900"])

    queue = await _make_queue_with_items(["7340011492900"])
    session = _make_session_with_queue(
        queue, grocy_api=grocy_api, products=[product]
    )

    # Show form
    await session.handle_step(Step.HANDLE_QUEUE, None)
    # Submit — initiates scan flow
    result = await session.handle_step(Step.HANDLE_QUEUE, {"confirm": True})
    # Drive through any intermediate forms
    result = await _drive_to_completion(session, result)

    assert isinstance(result, CompletedResult)


async def test_handle_queue_marks_resolved_on_success():
    """Completed items are marked resolved in the persistent queue."""
    grocy_api = FakeGrocyAPI()
    product = make_product(id=42, name="Milk")
    grocy_api.register_product(product, barcodes=["7340011492900"])

    queue = await _make_queue_with_items(["7340011492900"])
    item_id = queue.get_pending_items()[0].id

    session = _make_session_with_queue(
        queue, grocy_api=grocy_api, products=[product]
    )

    await session.handle_step(Step.HANDLE_QUEUE, None)
    result = await session.handle_step(Step.HANDLE_QUEUE, {"confirm": True})
    result = await _drive_to_completion(session, result)

    assert isinstance(result, CompletedResult)
    # Item should be resolved in the queue
    assert len(queue.get_pending_items()) == 0
    item = next((i for i in queue._items if i.id == item_id), None)
    assert item is not None
    assert item.status == QueueStatus.RESOLVED


async def test_handle_queue_uses_item_mode():
    """Queue items use their stored mode, not the session default."""
    grocy_api = FakeGrocyAPI()
    product = make_product(id=42, name="Milk")
    grocy_api.register_product(product, barcodes=["111"])

    # Queue items were added in CONSUME mode
    queue = await _make_queue_with_items(["111"], mode=SCAN_MODE.CONSUME)
    session = _make_session_with_queue(
        queue, grocy_api=grocy_api, products=[product]
    )

    await session.handle_step(Step.HANDLE_QUEUE, None)
    result = await session.handle_step(Step.HANDLE_QUEUE, {"confirm": True})

    # Should process in CONSUME mode (goes through BBuddy)
    assert isinstance(result, CompletedResult)
    assert session.barcode_scan_mode == SCAN_MODE.CONSUME


async def test_handle_queue_includes_failed_items():
    """Failed items are retried when Handle Queue is submitted."""
    grocy_api = FakeGrocyAPI()
    product = make_product(id=42, name="Oats")
    grocy_api.register_product(product, barcodes=["222"])

    queue = await _make_queue_with_items(["222"])
    # Mark as failed then try to reprocess
    pending = queue.get_pending_items()
    await queue.async_mark_failed(pending[0].id, "Timeout")

    session = _make_session_with_queue(
        queue, grocy_api=grocy_api, products=[product]
    )

    # Show form — should include the failed item
    result = await session.handle_step(Step.HANDLE_QUEUE, None)
    assert isinstance(result, FormRequest)
    assert result.description_placeholders["failed_count"] == "1"

    # Submit — should retry the failed item
    result = await session.handle_step(Step.HANDLE_QUEUE, {"confirm": True})
    result = await _drive_to_completion(session, result)
    assert isinstance(result, CompletedResult)


async def test_handle_queue_multiple_items_processed():
    """Multiple pending items are all fed to the scan queue."""
    grocy_api = FakeGrocyAPI()
    p1 = make_product(id=1, name="Milk")
    p2 = make_product(id=2, name="Bread")
    grocy_api.register_product(p1, barcodes=["111"])
    grocy_api.register_product(p2, barcodes=["222"])

    queue = await _make_queue_with_items(["111", "222"])
    session = _make_session_with_queue(
        queue, grocy_api=grocy_api, products=[p1, p2]
    )

    await session.handle_step(Step.HANDLE_QUEUE, None)
    result = await session.handle_step(Step.HANDLE_QUEUE, {"confirm": True})
    result = await _drive_to_completion(session, result)

    assert isinstance(result, CompletedResult)
    assert len(queue.get_pending_items()) == 0


async def test_handle_queue_duplicate_barcodes_both_resolved():
    """Two queue items with the same barcode are both resolved independently."""
    grocy_api = FakeGrocyAPI()
    product = make_product(id=42, name="Milk")
    grocy_api.register_product(product, barcodes=["111"])

    queue = await _make_queue_with_items(["111", "111"])
    items = queue.get_pending_items()
    assert len(items) == 2
    item_id_1 = items[0].id
    item_id_2 = items[1].id
    assert item_id_1 != item_id_2  # unique UUIDs

    session = _make_session_with_queue(
        queue, grocy_api=grocy_api, products=[product]
    )

    await session.handle_step(Step.HANDLE_QUEUE, None)
    result = await session.handle_step(Step.HANDLE_QUEUE, {"confirm": True})
    result = await _drive_to_completion(session, result)

    assert isinstance(result, CompletedResult)
    assert len(queue.get_pending_items()) == 0

    # Both items should be resolved
    resolved = [i for i in queue._items if i.status == QueueStatus.RESOLVED]
    assert len(resolved) == 2
    resolved_ids = {i.id for i in resolved}
    assert item_id_1 in resolved_ids
    assert item_id_2 in resolved_ids


# ── Tests: Queue isolation from manual scans ────────────────────────


async def test_manual_scan_does_not_resolve_queued_item():
    """A manual scan via SCAN_START must NOT resolve a pending queue item
    with the same barcode.

    Scenario: barcode "111" is queued via webhook but fails auto-resolve
    (e.g. product config needs review).  The user then manually scans
    the same barcode through the normal SCAN_START flow.  The queue item
    must stay PENDING — it represents a separate scan intent.
    """
    grocy_api = FakeGrocyAPI()
    product = make_product(id=42, name="Milk")
    grocy_api.register_product(product, barcodes=["111"])

    # Create a queue with one pending item
    queue = await _make_queue_with_items(["111"])
    item_id = queue.get_pending_items()[0].id

    # Create a session that has the queue on the coordinator,
    # but enter via SCAN_START (manual scan), NOT Handle Queue.
    bbuddy_api = FakeBarcodeBuddyAPI()
    coordinator = FakeCoordinator(
        grocy_api=grocy_api,
        bbuddy_api=bbuddy_api,
        master_data=make_master_data(products=[product]),
    )
    coordinator.queue = queue

    session = ScanSession(
        coordinator=coordinator,
        api_bbuddy=bbuddy_api,
        config_entry_data={},
    )

    # Manual scan: SCAN_START → submit barcode
    result = await session.handle_step(Step.SCAN_START, None)
    assert isinstance(result, FormRequest)

    result = await session.handle_step(
        Step.SCAN_START,
        {"barcodes": "111", "mode": SCAN_MODE.PURCHASE},
    )
    # Drive through any intermediate forms to completion
    result = await _drive_to_completion(session, result)
    assert isinstance(result, CompletedResult)

    # The queue item must still be PENDING — manual scan is independent
    pending = queue.get_pending_items()
    assert len(pending) == 1
    assert pending[0].id == item_id
    assert pending[0].status == QueueStatus.PENDING
