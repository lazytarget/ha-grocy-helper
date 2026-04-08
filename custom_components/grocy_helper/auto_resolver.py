"""Auto-resolve engine for the scan queue.

Drives a headless ``ScanSession`` for a single barcode, automatically
filling form defaults.  If any step requires manual input the resolver
bails out and reports *needs_manual*.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .barcodebuddyapi import BarcodeBuddyAPI
from .const import SCAN_MODE
from .coordinator import GrocyHelperCoordinator
from .scan_session import ScanSession
from .scan_types import (
    AbortResult,
    CompletedResult,
    FormField,
    FormRequest,
    Step,
    StepResult,
)

_LOGGER = logging.getLogger(__name__)

#: Maximum number of step iterations before we give up.
MAX_STEPS = 15

#: Steps where the resolver is allowed to auto-fill form defaults.
#: Any other form step requires manual intervention.
AUTO_RESOLVABLE_STEPS: frozenset[str] = frozenset({Step.SCAN_PROCESS})


# ── Result type ──────────────────────────────────────────────────────


@dataclass
class AutoResolveResult:
    """Outcome of an auto-resolve attempt."""

    success: bool
    error: str | None = None
    needs_manual: bool = False
    result_text: str | None = None


# ── Public API ───────────────────────────────────────────────────────


async def async_try_auto_resolve(
    *,
    coordinator: GrocyHelperCoordinator,
    api_bbuddy: BarcodeBuddyAPI,
    config_entry_data: dict[str, Any],
    barcode: str,
    mode: str | SCAN_MODE,
    scan_options: dict[str, Any] | None = None,
) -> AutoResolveResult:
    """Try to process *barcode* without human interaction.

    Creates a throwaway ``ScanSession``, submits the barcode, and loops
    through step results.  When a form is returned for an auto-resolvable
    step, it fills every field with its ``default`` value.  If any
    required field lacks a default, or the step is not auto-resolvable,
    the attempt is marked *needs_manual*.

    Returns
    -------
    AutoResolveResult
        ``.success`` is ``True`` only when the workflow reaches a
        ``CompletedResult``.
    """
    session = ScanSession(
        coordinator=coordinator,
        api_bbuddy=api_bbuddy,
        scan_options=scan_options,
        config_entry_data=config_entry_data,
    )

    try:
        # Kick off the session — the first call with user_input=None
        # returns the SCAN_START form; we immediately submit it.
        result: StepResult = await session.handle_step(
            Step.SCAN_START,
            {"barcodes": barcode, "mode": str(mode)},
        )

        for _ in range(MAX_STEPS):
            if isinstance(result, CompletedResult):
                return AutoResolveResult(
                    success=True,
                    result_text=result.summary,
                )

            if isinstance(result, AbortResult):
                return AutoResolveResult(
                    success=False,
                    error=result.reason,
                )

            if isinstance(result, FormRequest):
                # Error re-display: the previous submission failed
                if result.errors:
                    error_msg = "; ".join(
                        f"{k}: {v}" for k, v in result.errors.items()
                    )
                    _LOGGER.info(
                        "Auto-resolve failed: form returned with errors: %s",
                        error_msg,
                    )
                    return AutoResolveResult(
                        success=False,
                        error=error_msg,
                    )

                # Can we auto-fill this form?
                if result.step_id not in AUTO_RESOLVABLE_STEPS:
                    _LOGGER.info(
                        "Auto-resolve needs manual: step %s is not auto-resolvable",
                        result.step_id,
                    )
                    return AutoResolveResult(
                        success=False,
                        needs_manual=True,
                        error=f"Step '{result.step_id}' requires manual input",
                    )

                user_input = _build_auto_input(result.fields)
                if user_input is None:
                    # A required field has no default
                    missing = [
                        f.key
                        for f in result.fields
                        if f.required and f.default is None
                    ]
                    _LOGGER.info(
                        "Auto-resolve needs manual: missing defaults for %s",
                        missing,
                    )
                    return AutoResolveResult(
                        success=False,
                        needs_manual=True,
                        error=f"Missing defaults for fields: {missing}",
                    )

                result = await session.handle_step(result.step_id, user_input)
            else:
                # Unknown result type — shouldn't happen
                return AutoResolveResult(
                    success=False,
                    error=f"Unexpected result type: {type(result).__name__}",
                )

        # Loop limit reached
        return AutoResolveResult(
            success=False,
            error=f"Auto-resolve exceeded {MAX_STEPS} step limit",
        )

    except Exception as exc:
        _LOGGER.exception("Auto-resolve error for barcode %s", barcode)
        return AutoResolveResult(
            success=False,
            error=str(exc),
        )


# ── Helpers ──────────────────────────────────────────────────────────


def _build_auto_input(fields: list[FormField]) -> dict[str, Any] | None:
    """Build a user_input dict from form field defaults.

    Returns ``None`` if any required field lacks a ``default``.
    """
    user_input: dict[str, Any] = {}
    for f in fields:
        if f.default is not None:
            user_input[f.key] = f.default
        elif f.required:
            return None
        # Optional fields without defaults are simply omitted
    return user_input
