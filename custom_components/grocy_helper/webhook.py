"""Webhook handler for the Grocy-helper barcode scan queue.

This module contains the payload parsing and processing logic for
incoming webhook requests.  It is intentionally separated from the
Home Assistant webhook registration so the core logic can be tested
without HA dependencies.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .const import SCAN_MODE
from .queue import ScanQueue

_LOGGER = logging.getLogger(__name__)

# All valid SCAN_MODE string values for validation
_VALID_MODES: frozenset[str] = frozenset(m.value for m in SCAN_MODE)


class WebhookError(Exception):
    """Raised when webhook payload validation fails."""


@dataclass
class ParsedPayload:
    """Result of parsing a webhook request body."""

    barcodes: list[str] = field(default_factory=list)
    mode: str | None = None


def _strip_angle_brackets(barcode: str) -> str:
    """Remove angle-bracket wrapping from structured barcodes.

    ``<1234|q:2|p:25.0>`` → ``1234|q:2|p:25.0``
    """
    barcode = barcode.strip()
    if barcode.startswith("<") and barcode.endswith(">"):
        return barcode[1:-1]
    return barcode


def parse_webhook_payload(data: dict[str, Any]) -> ParsedPayload:
    """Validate and parse a webhook JSON payload.

    Accepts::

        {"barcode": "123"}
        {"barcode": "<123|q:2|p:25.0>"}
        {"barcodes": ["123", "456"]}
        {"barcode": "123", "mode": "BBUDDY-P"}

    When both ``barcode`` and ``barcodes`` are present, the array
    takes priority.

    Raises
    ------
    WebhookError
        On validation failure (missing/empty barcode, invalid mode).
    """
    barcodes_raw: list[str] | None = None

    if "barcodes" in data:
        val = data["barcodes"]
        if not isinstance(val, list):
            raise WebhookError("'barcodes' must be an array of strings")
        barcodes_raw = val
    elif "barcode" in data:
        val = data["barcode"]
        if not isinstance(val, str):
            raise WebhookError("'barcode' must be a string")
        barcodes_raw = [val]
    else:
        raise WebhookError("Payload must contain 'barcode' (string) or 'barcodes' (array)")

    # Strip and validate
    cleaned: list[str] = []
    for raw in barcodes_raw:
        if not isinstance(raw, str):
            raise WebhookError(f"Each barcode must be a string, got {type(raw).__name__}")
        stripped = _strip_angle_brackets(raw)
        if not stripped:
            continue
        cleaned.append(stripped)

    if not cleaned:
        raise WebhookError("No non-empty barcodes provided")

    # Validate mode
    mode: str | None = data.get("mode")
    if mode is not None:
        if not isinstance(mode, str):
            raise WebhookError("'mode' must be a string")
        if mode not in _VALID_MODES:
            raise WebhookError(
                f"Invalid mode '{mode}'. Valid modes: {sorted(_VALID_MODES)}"
            )

    return ParsedPayload(barcodes=cleaned, mode=mode)


def _parse_structured_barcode(barcode_str: str) -> tuple[str, dict[str, str]]:
    """Parse a potentially structured barcode into (barcode, metadata).

    ``"1234|q:2|p:25.0|n:Test"`` → ``("1234", {"q": "2", "p": "25.0", "n": "Test"})``
    ``"1234"`` → ``("1234", {})``
    """
    parts = barcode_str.split("|")
    barcode = parts[0]
    metadata: dict[str, str] = {}

    for part in parts[1:]:
        if ":" in part:
            key, value = part.split(":", 1)
            if value:
                metadata[key] = value.strip()

    return barcode, metadata


async def process_webhook_payload(
    queue: ScanQueue,
    data: dict[str, Any],
) -> list[dict[str, Any]]:
    """Parse a webhook payload and add barcodes to the queue.

    Parameters
    ----------
    queue:
        The ``ScanQueue`` to add items to.
    data:
        Raw JSON payload from the webhook request.

    Returns
    -------
    A list of per-barcode result dicts, each containing at minimum
    ``barcode`` and ``status`` (``"queued"`` or ``"mode_switched"``).

    Raises
    ------
    WebhookError
        On payload validation failure.
    """
    parsed = parse_webhook_payload(data)
    results: list[dict[str, Any]] = []

    for raw_barcode in parsed.barcodes:
        barcode, metadata = _parse_structured_barcode(raw_barcode)

        item = await queue.async_add(
            barcode=barcode,
            mode=parsed.mode,
            metadata=metadata or None,
        )

        if item is None:
            # Mode switch occurred
            results.append({
                "barcode": barcode,
                "status": "mode_switched",
                "new_mode": queue.current_mode.value,
            })
        else:
            results.append({
                "barcode": barcode,
                "status": "queued",
                "item_id": item.id,
                "mode": item.mode,
            })

    return results
