"""Persistent barcode scan queue with dynamic mode tracking.

Provides ``ScanQueue`` — a queue of barcode scan items backed by
Home Assistant's ``helpers.storage.Store`` (or any compatible store).
Items are added via webhook or service call and can be auto-resolved
or processed manually through the OptionsFlow.

The queue also maintains a *current mode* variable that persists
alongside the items.  Scanning a barcode whose value matches a
``SCAN_MODE`` member switches the current mode instead of creating
a queue item.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

from .const import SCAN_MODE

_LOGGER = logging.getLogger(__name__)

# All SCAN_MODE string values, for fast membership testing
_MODE_VALUES: frozenset[str] = frozenset(m.value for m in SCAN_MODE)


class QueueStatus(StrEnum):
    """Status of a queue item."""

    PENDING = "pending"
    RESOLVED = "resolved"
    FAILED = "failed"


@dataclass
class QueueItem:
    """A single barcode scan waiting to be processed."""

    id: str
    barcode: str
    mode: str
    added_at: str
    status: QueueStatus = QueueStatus.PENDING
    error: str | None = None
    result: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class ScanQueue:
    """Persistent barcode scan queue with dynamic mode.

    Parameters
    ----------
    store:
        An object implementing ``async_load() -> dict | None`` and
        ``async_save(data: dict) -> None``.  In production this is
        ``homeassistant.helpers.storage.Store``; in tests a
        ``FakeStore`` is used.
    """

    def __init__(self, store: Any) -> None:
        self._store = store
        self._items: list[QueueItem] = []
        self._current_mode: SCAN_MODE = SCAN_MODE.PURCHASE

    # ── Properties ───────────────────────────────────────────────────

    @property
    def current_mode(self) -> SCAN_MODE:
        return self._current_mode

    # ── Public API ───────────────────────────────────────────────────

    async def async_load(self) -> None:
        """Load queue state from persistent storage."""
        data = await self._store.async_load()
        if data is None:
            return

        raw_mode = data.get("current_mode", SCAN_MODE.PURCHASE)
        try:
            self._current_mode = SCAN_MODE(raw_mode)
        except ValueError:
            _LOGGER.warning(
                "Invalid persisted scan mode %r; falling back to %s",
                raw_mode,
                SCAN_MODE.PURCHASE,
            )
            self._current_mode = SCAN_MODE.PURCHASE

        self._items = []
        for raw in data.get("items", []):
            try:
                status = QueueStatus(raw["status"])
            except (ValueError, KeyError):
                _LOGGER.warning(
                    "Invalid persisted queue item status %r for item %r; skipping",
                    raw.get("status"),
                    raw.get("id"),
                )
                continue

            self._items.append(
                QueueItem(
                    id=raw["id"],
                    barcode=raw["barcode"],
                    mode=raw["mode"],
                    added_at=raw["added_at"],
                    status=status,
                    error=raw.get("error"),
                    result=raw.get("result"),
                    metadata=raw.get("metadata", {}),
                )
            )

    async def async_add(
        self,
        barcode: str,
        mode: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> QueueItem | None:
        """Add a barcode to the queue.

        If *barcode* matches a ``SCAN_MODE`` value the current mode is
        switched and ``None`` is returned (no item created).

        Parameters
        ----------
        barcode:
            Raw barcode string.
        mode:
            Explicit scan mode.  If ``None``, the queue's current mode
            is used.
        metadata:
            Optional structured metadata parsed from the barcode.

        Returns
        -------
        The created ``QueueItem``, or ``None`` if the barcode was a
        mode-switch command.
        """
        # Mode-switch detection
        if barcode in _MODE_VALUES:
            self._current_mode = SCAN_MODE(barcode)
            _LOGGER.info("Queue mode switched to %s", self._current_mode)
            await self._async_save()
            return None

        item = QueueItem(
            id=str(uuid.uuid4()),
            barcode=barcode,
            mode=mode or self._current_mode.value,
            added_at=dt.datetime.now(dt.timezone.utc).isoformat(),
            metadata=metadata or {},
        )
        self._items.append(item)
        await self._async_save()
        _LOGGER.debug("Queued barcode %s (mode=%s)", barcode, item.mode)
        return item

    def get_pending_items(self) -> list[QueueItem]:
        """Return items with PENDING status."""
        return [i for i in self._items if i.status == QueueStatus.PENDING]

    def get_failed_items(self) -> list[QueueItem]:
        """Return items with FAILED status."""
        return [i for i in self._items if i.status == QueueStatus.FAILED]

    async def async_mark_resolved(self, item_id: str, result_text: str) -> None:
        """Mark an item as successfully resolved."""
        for item in self._items:
            if item.id == item_id:
                item.status = QueueStatus.RESOLVED
                item.result = result_text
                await self._async_save()
                return

    async def async_mark_failed(self, item_id: str, error_text: str) -> None:
        """Mark an item as failed with an error message."""
        for item in self._items:
            if item.id == item_id:
                item.status = QueueStatus.FAILED
                item.error = error_text
                await self._async_save()
                return

    async def async_remove(self, item_id: str) -> bool:
        """Remove an item by ID. Returns True if found and removed."""
        for i, item in enumerate(self._items):
            if item.id == item_id:
                self._items.pop(i)
                await self._async_save()
                return True
        return False

    async def async_clear_resolved(self) -> None:
        """Remove all RESOLVED items from the queue."""
        self._items = [
            i for i in self._items if i.status != QueueStatus.RESOLVED
        ]
        await self._async_save()

    # ── Persistence ──────────────────────────────────────────────────

    async def _async_save(self) -> None:
        """Persist current state to the store."""
        items = []
        for item in self._items:
            serialized = asdict(item)
            serialized["status"] = item.status.value
            items.append(serialized)
        data = {
            "current_mode": self._current_mode.value,
            "items": items,
        }
        await self._store.async_save(data)
