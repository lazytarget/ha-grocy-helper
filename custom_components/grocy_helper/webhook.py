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


# ── Request / Response types ─────────────────────────────────────────


@dataclass
class WebhookRequest:
    """Parsed and validated webhook request."""

    barcodes: list[str]
    mode: str | None = None


@dataclass
class WebhookItemResult:
    """Result of processing a single barcode from the webhook."""

    barcode: str
    status: str  # "queued" | "mode_switched"
    item_id: str | None = None
    mode: str | None = None
    new_mode: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"barcode": self.barcode, "status": self.status}
        if self.item_id is not None:
            d["item_id"] = self.item_id
        if self.mode is not None:
            d["mode"] = self.mode
        if self.new_mode is not None:
            d["new_mode"] = self.new_mode
        return d


@dataclass
class WebhookResponse:
    """Full webhook response."""

    status: str  # "ok"
    results: list[WebhookItemResult]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "results": [r.to_dict() for r in self.results],
        }


# ── Parsing helpers ──────────────────────────────────────────────────


def _strip_angle_brackets(barcode: str) -> str:
    """Remove angle-bracket wrapping from structured barcodes.

    ``<1234|q:2|p:25.0>`` → ``1234|q:2|p:25.0``
    """
    barcode = barcode.strip()
    if barcode.startswith("<") and barcode.endswith(">"):
        return barcode[1:-1]
    return barcode


def parse_webhook_payload(data: dict[str, Any]) -> WebhookRequest:
    """Validate and parse a webhook JSON payload.

    The ``barcode`` field accepts either a single string or an array
    of strings::

        {"barcode": "123"}
        {"barcode": ["123", "456"]}
        {"barcode": "<123|q:2|p:25.0>"}
        {"barcode": "123", "mode": "BBUDDY-P"}

    Raises
    ------
    WebhookError
        On validation failure (missing/empty barcode, invalid mode).
    """
    if "barcode" not in data:
        raise WebhookError("Payload must contain a 'barcode' field (string or array of strings)")

    val = data["barcode"]

    # Normalize to list
    if isinstance(val, str):
        barcodes_raw = [val]
    elif isinstance(val, list):
        barcodes_raw = val
    else:
        raise WebhookError(
            f"'barcode' must be a string or array of strings, got {type(val).__name__}"
        )

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

    return WebhookRequest(barcodes=cleaned, mode=mode)


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


# ── Processing ───────────────────────────────────────────────────────


async def process_webhook_payload(
    queue: ScanQueue,
    data: dict[str, Any],
) -> list[WebhookItemResult]:
    """Parse a webhook payload and add barcodes to the queue.

    Parameters
    ----------
    queue:
        The ``ScanQueue`` to add items to.
    data:
        Raw JSON payload from the webhook request.

    Returns
    -------
    A list of ``WebhookItemResult`` per barcode.

    Raises
    ------
    WebhookError
        On payload validation failure.
    """
    parsed = parse_webhook_payload(data)
    results: list[WebhookItemResult] = []

    for raw_barcode in parsed.barcodes:
        barcode, metadata = _parse_structured_barcode(raw_barcode)

        item = await queue.async_add(
            barcode=barcode,
            mode=parsed.mode,
            metadata=metadata or None,
        )

        if item is None:
            # Mode switch occurred
            results.append(WebhookItemResult(
                barcode=barcode,
                status="mode_switched",
                new_mode=queue.current_mode.value,
            ))
        else:
            results.append(WebhookItemResult(
                barcode=barcode,
                status="queued",
                item_id=item.id,
                mode=item.mode,
            ))

    return results
