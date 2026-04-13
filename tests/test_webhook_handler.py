"""Tests for the HA webhook handler closure in __init__.py.

Covers:
- 1a: Invalid/malformed JSON → HTTP 400
- 1b: WebhookError → HTTP 400 with error message
- 1c: Unexpected exception → HTTP 500
- 1d: Auto-resolve outcomes (success/needs_manual/failed)

Written BEFORE verifying coverage (TDD).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.grocy_helper import _build_webhook_handler
from custom_components.grocy_helper.auto_resolver import AutoResolveResult
from custom_components.grocy_helper.queue import ScanQueue, QueueStatus

from tests.conftest import FakeBarcodeBuddyAPI, FakeStore


# ── Helpers ──────────────────────────────────────────────────────────

MODULE = "custom_components.grocy_helper"


def _make_request(json_data=None, *, raise_on_json=False):
    """Create a mock aiohttp.web.Request."""
    req = AsyncMock()
    if raise_on_json:
        req.json.side_effect = ValueError("Invalid JSON")
    else:
        req.json.return_value = json_data
    return req


def _make_coordinator(queue: ScanQueue | None = None):
    """Minimal coordinator mock with the attributes the handler accesses."""
    coord = MagicMock()
    coord.queue = queue or ScanQueue(store=FakeStore())
    coord._api_bbuddy = FakeBarcodeBuddyAPI()
    coord._config_entry = MagicMock()
    coord._config_entry.data = {}
    return coord


# ── 1a: Invalid / malformed JSON → 400 ─────────────────────────────


async def test_invalid_json_returns_400():
    """Malformed request body that fails JSON parsing → HTTP 400."""
    coord = _make_coordinator()
    handler = _build_webhook_handler(coord)

    request = _make_request(raise_on_json=True)
    response = await handler(MagicMock(), "webhook-id", request)

    assert response.status == 400
    body = response.body
    assert b"Invalid JSON" in body


# ── 1b: WebhookError → 400 with message ────────────────────────────


async def test_webhook_error_returns_400_with_message():
    """WebhookError from process_webhook_payload → HTTP 400 + error text."""
    coord = _make_coordinator()
    handler = _build_webhook_handler(coord)

    request = _make_request(json_data={})  # empty payload → WebhookError
    response = await handler(MagicMock(), "webhook-id", request)

    assert response.status == 400
    assert b"barcode" in response.body  # error mentions missing barcode field


# ── 1c: Unexpected exception → 500 ─────────────────────────────────


async def test_unexpected_exception_returns_500():
    """Non-WebhookError exception in process_webhook_payload → HTTP 500."""
    coord = _make_coordinator()
    handler = _build_webhook_handler(coord)

    request = _make_request(json_data={"barcode": "123"})

    with patch(
        f"{MODULE}.process_webhook_payload",
        side_effect=RuntimeError("boom"),
    ):
        response = await handler(MagicMock(), "webhook-id", request)

    assert response.status == 500
    assert b"Internal error" in response.body


# ── 1d: Auto-resolve outcomes ───────────────────────────────────────


async def test_auto_resolve_success_marks_item_resolved():
    """Successful auto-resolve → item status becomes 'auto_resolved'."""
    queue = ScanQueue(store=FakeStore())
    coord = _make_coordinator(queue=queue)
    handler = _build_webhook_handler(coord)

    request = _make_request(json_data={"barcode": "1234567890123"})

    resolve_result = AutoResolveResult(
        success=True, result_text="Added 1× Test Product"
    )
    with patch(
        f"{MODULE}.async_try_auto_resolve",
        return_value=resolve_result,
    ):
        response = await handler(MagicMock(), "webhook-id", request)

    assert response.status == 200

    # The item should be marked resolved in the queue
    pending = queue.get_pending_items()
    assert len(pending) == 0

    resolved = [i for i in queue._items if i.status == QueueStatus.RESOLVED]
    assert len(resolved) == 1
    assert resolved[0].result == "Added 1× Test Product"


async def test_auto_resolve_needs_manual_leaves_item_pending():
    """Auto-resolve needs_manual → item stays in 'pending' status."""
    queue = ScanQueue(store=FakeStore())
    coord = _make_coordinator(queue=queue)
    handler = _build_webhook_handler(coord)

    request = _make_request(json_data={"barcode": "1234567890123"})

    resolve_result = AutoResolveResult(
        success=False,
        needs_manual=True,
        error="Step 'SCAN_MATCH_PRODUCT' requires manual input",
    )
    with patch(
        f"{MODULE}.async_try_auto_resolve",
        return_value=resolve_result,
    ):
        response = await handler(MagicMock(), "webhook-id", request)

    assert response.status == 200

    # Item should remain pending (not resolved, not failed)
    pending = queue.get_pending_items()
    assert len(pending) == 1


async def test_auto_resolve_failure_marks_item_failed():
    """Auto-resolve failed (not needs_manual) → item marked as failed."""
    queue = ScanQueue(store=FakeStore())
    coord = _make_coordinator(queue=queue)
    handler = _build_webhook_handler(coord)

    request = _make_request(json_data={"barcode": "1234567890123"})

    resolve_result = AutoResolveResult(
        success=False,
        needs_manual=False,
        error="default_best_before_days is 0",
    )
    with patch(
        f"{MODULE}.async_try_auto_resolve",
        return_value=resolve_result,
    ):
        response = await handler(MagicMock(), "webhook-id", request)

    assert response.status == 200

    # Item should be marked failed
    pending = queue.get_pending_items()
    assert len(pending) == 0

    failed = queue.get_failed_items()
    assert len(failed) == 1


async def test_auto_resolve_exception_does_not_crash_handler():
    """Exception during auto-resolve is logged but handler still returns 200."""
    queue = ScanQueue(store=FakeStore())
    coord = _make_coordinator(queue=queue)
    handler = _build_webhook_handler(coord)

    request = _make_request(json_data={"barcode": "1234567890123"})

    with patch(
        f"{MODULE}.async_try_auto_resolve",
        side_effect=RuntimeError("resolver exploded"),
    ):
        response = await handler(MagicMock(), "webhook-id", request)

    # Handler should still return 200 with results
    assert response.status == 200

    # Item should remain pending (not resolved, not failed)
    pending = queue.get_pending_items()
    assert len(pending) == 1


async def test_auto_resolve_skips_mode_switched_items():
    """Mode-switched results are not sent to auto-resolve."""
    queue = ScanQueue(store=FakeStore())
    coord = _make_coordinator(queue=queue)
    handler = _build_webhook_handler(coord)

    # Send a mode barcode — should NOT trigger auto-resolve
    request = _make_request(json_data={"barcode": "BBUDDY-AS"})

    mock_resolve = AsyncMock(
        return_value=AutoResolveResult(success=True, result_text="should not be called")
    )
    with patch(f"{MODULE}.async_try_auto_resolve", mock_resolve):
        response = await handler(MagicMock(), "webhook-id", request)

    assert response.status == 200
    mock_resolve.assert_not_called()


async def test_auto_resolve_response_contains_updated_statuses():
    """Response results reflect auto-resolve status changes."""
    queue = ScanQueue(store=FakeStore())
    coord = _make_coordinator(queue=queue)
    handler = _build_webhook_handler(coord)

    request = _make_request(json_data={"barcode": "1234567890123"})

    resolve_result = AutoResolveResult(
        success=True, result_text="Done"
    )
    with patch(
        f"{MODULE}.async_try_auto_resolve",
        return_value=resolve_result,
    ):
        response = await handler(MagicMock(), "webhook-id", request)

    # Parse the response body to check status field
    import json
    body = json.loads(response.body)
    assert body["results"][0]["status"] == "auto_resolved"
