"""Tests for the webhook handler — payload parsing, validation, and queue integration.

Written BEFORE the implementation (TDD).
"""

from __future__ import annotations

import pytest

from custom_components.grocy_helper.const import SCAN_MODE
from custom_components.grocy_helper.queue import QueueStatus, ScanQueue
from custom_components.grocy_helper.webhook import (
    parse_webhook_payload,
    process_webhook_payload,
    WebhookError,
)

from tests.conftest import FakeStore


# ── Helpers ──────────────────────────────────────────────────────────


def _make_queue() -> ScanQueue:
    return ScanQueue(store=FakeStore())


# ── Tests: Payload parsing ──────────────────────────────────────────


def test_parse_single_barcode_string():
    """Single barcode as 'barcode' string."""
    result = parse_webhook_payload({"barcode": "1234567890123"})
    assert result.barcodes == ["1234567890123"]
    assert result.mode is None


def test_parse_multiple_barcodes_array():
    """Multiple barcodes as 'barcodes' array."""
    result = parse_webhook_payload(
        {"barcodes": ["1234567890123", "9876543210987"]}
    )
    assert result.barcodes == ["1234567890123", "9876543210987"]


def test_parse_structured_barcode():
    """Structured barcode with angle brackets is preserved."""
    result = parse_webhook_payload({"barcode": "<1234567890123|q:2|p:25.0>"})
    assert result.barcodes == ["1234567890123|q:2|p:25.0"]


def test_parse_mixed_array_structured_and_plain():
    """Array with both plain and structured barcodes."""
    result = parse_webhook_payload(
        {"barcodes": ["1111111111111", "<2222222222222|q:3|u:st>"]}
    )
    assert result.barcodes == ["1111111111111", "2222222222222|q:3|u:st"]


def test_parse_with_explicit_mode():
    """Explicit mode in payload."""
    result = parse_webhook_payload(
        {"barcode": "1234567890123", "mode": "BBUDDY-P"}
    )
    assert result.mode == "BBUDDY-P"


def test_parse_barcode_and_barcodes_combined():
    """Both 'barcode' and 'barcodes' present — barcodes array takes priority."""
    result = parse_webhook_payload(
        {"barcode": "1111111111111", "barcodes": ["2222222222222", "3333333333333"]}
    )
    assert result.barcodes == ["2222222222222", "3333333333333"]


def test_parse_barcode_string_stripped():
    """Whitespace is stripped from barcodes."""
    result = parse_webhook_payload({"barcode": "  1234567890123  "})
    assert result.barcodes == ["1234567890123"]


# ── Tests: Validation errors ────────────────────────────────────────


def test_parse_empty_payload_raises():
    """Empty payload raises WebhookError."""
    with pytest.raises(WebhookError, match="barcode"):
        parse_webhook_payload({})


def test_parse_empty_barcode_raises():
    """Empty barcode string raises WebhookError."""
    with pytest.raises(WebhookError, match="empty"):
        parse_webhook_payload({"barcode": ""})


def test_parse_empty_barcodes_array_raises():
    """Empty barcodes array raises WebhookError."""
    with pytest.raises(WebhookError, match="empty"):
        parse_webhook_payload({"barcodes": []})


def test_parse_non_string_barcode_raises():
    """Non-string barcode raises WebhookError."""
    with pytest.raises(WebhookError):
        parse_webhook_payload({"barcode": 12345})


def test_parse_invalid_mode_raises():
    """Invalid mode value raises WebhookError."""
    with pytest.raises(WebhookError, match="mode"):
        parse_webhook_payload({"barcode": "1234567890123", "mode": "INVALID-MODE"})


# ── Tests: process_webhook_payload — queue integration ──────────────


async def test_process_single_barcode():
    """Single barcode is added to the queue."""
    queue = _make_queue()
    results = await process_webhook_payload(
        queue, {"barcode": "1234567890123"}
    )

    assert len(results) == 1
    assert results[0]["barcode"] == "1234567890123"
    assert results[0]["status"] == "queued"
    assert len(queue.get_pending_items()) == 1


async def test_process_multiple_barcodes():
    """Multiple barcodes are all added to the queue."""
    queue = _make_queue()
    results = await process_webhook_payload(
        queue, {"barcodes": ["111", "222", "333"]}
    )

    assert len(results) == 3
    assert all(r["status"] == "queued" for r in results)
    assert len(queue.get_pending_items()) == 3


async def test_process_with_explicit_mode():
    """Explicit mode is passed to each queue item."""
    queue = _make_queue()
    results = await process_webhook_payload(
        queue, {"barcode": "111", "mode": "BBUDDY-C"}
    )

    assert results[0]["status"] == "queued"
    items = queue.get_pending_items()
    assert items[0].mode == SCAN_MODE.CONSUME


async def test_process_without_mode_uses_queue_current_mode():
    """No mode in payload uses the queue's current_mode."""
    queue = _make_queue()
    queue._current_mode = SCAN_MODE.ADD_TO_SHOPPING_LIST

    results = await process_webhook_payload(
        queue, {"barcode": "111"}
    )

    items = queue.get_pending_items()
    assert items[0].mode == SCAN_MODE.ADD_TO_SHOPPING_LIST


async def test_process_mode_barcode_switches_mode():
    """A mode barcode switches the queue mode and is not queued."""
    queue = _make_queue()
    assert queue.current_mode == SCAN_MODE.PURCHASE

    results = await process_webhook_payload(
        queue, {"barcode": "BBUDDY-AS"}
    )

    assert len(results) == 1
    assert results[0]["status"] == "mode_switched"
    assert results[0]["new_mode"] == SCAN_MODE.ADD_TO_SHOPPING_LIST
    assert queue.current_mode == SCAN_MODE.ADD_TO_SHOPPING_LIST
    assert len(queue.get_pending_items()) == 0


async def test_process_mode_barcode_in_array():
    """Mode barcode in array switches mode; regular barcodes are queued."""
    queue = _make_queue()
    results = await process_webhook_payload(
        queue,
        {"barcodes": ["BBUDDY-AS", "111", "222"]},
    )

    assert len(results) == 3
    assert results[0]["status"] == "mode_switched"
    # items after mode switch use the NEW mode
    assert results[1]["status"] == "queued"
    assert results[2]["status"] == "queued"
    items = queue.get_pending_items()
    assert all(i.mode == SCAN_MODE.ADD_TO_SHOPPING_LIST for i in items)


async def test_process_structured_barcode_metadata():
    """Structured barcode metadata is passed to the queue item."""
    queue = _make_queue()
    results = await process_webhook_payload(
        queue, {"barcode": "<1234567890123|q:2|p:25.0|n:Test Product>"}
    )

    assert results[0]["status"] == "queued"
    items = queue.get_pending_items()
    assert items[0].barcode == "1234567890123"
    assert items[0].metadata == {"q": "2", "p": "25.0", "n": "Test Product"}


async def test_process_invalid_payload_raises():
    """Invalid payload raises WebhookError."""
    queue = _make_queue()
    with pytest.raises(WebhookError):
        await process_webhook_payload(queue, {})


async def test_process_returns_item_id():
    """Each queued result includes the item_id for tracking."""
    queue = _make_queue()
    results = await process_webhook_payload(
        queue, {"barcode": "111"}
    )

    assert "item_id" in results[0]
    assert results[0]["item_id"] == queue.get_pending_items()[0].id
