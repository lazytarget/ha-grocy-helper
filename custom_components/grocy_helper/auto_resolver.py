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

        # ── Product config quality gate ─────────────────────────────
        # After SCAN_START the session has looked up the product.
        # Check for suspicious configuration before proceeding.
        product = session.current_product
        if product:
            issues = _validate_product_config(product)
            if issues:
                _LOGGER.info(
                    "Auto-resolve needs manual: product config issues: %s",
                    issues,
                )
                return AutoResolveResult(
                    success=False,
                    needs_manual=True,
                    error=f"Product config needs review: {'; '.join(issues)}",
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
                    # A required field has no default, or a field has
                    # a suggested_value but no default (needs review)
                    needs_review = [
                        f.key
                        for f in result.fields
                        if f.suggested_value is not None and f.default is None
                    ]
                    missing = [
                        f.key
                        for f in result.fields
                        if f.required and f.default is None
                    ]
                    reason_parts: list[str] = []
                    if needs_review:
                        reason_parts.append(
                            f"Fields need review: {needs_review}"
                        )
                    if missing:
                        reason_parts.append(
                            f"Missing defaults for fields: {missing}"
                        )
                    reason = "; ".join(reason_parts) or "Cannot auto-fill form"
                    _LOGGER.info(
                        "Auto-resolve needs manual: %s",
                        reason,
                    )
                    return AutoResolveResult(
                        success=False,
                        needs_manual=True,
                        error=reason,
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

    Returns ``None`` if any required field lacks a ``default``, or if
    any field has a ``suggested_value`` but no ``default`` (meaning the
    value exists but was deemed unreliable and needs human review).
    """
    user_input: dict[str, Any] = {}
    for f in fields:
        if f.default is not None:
            user_input[f.key] = f.default
        elif f.suggested_value is not None:
            # Value exists but was explicitly NOT promoted to default
            # → needs human confirmation
            return None
        elif f.required:
            return None
        # Optional fields with neither default nor suggested_value are omitted
    return user_input


def _validate_product_config(product: dict) -> list[str]:
    """Check product attributes for suspicious values.

    Returns a list of human-readable issue descriptions.  An empty list
    means the product configuration is suitable for auto-resolve.

    Rules
    -----
    - ``default_best_before_days == 0``: Grocy default, means "expires
      today" — almost certainly not intentionally configured.
    - ``default_best_before_days_after_freezing == 0``: not configured,
      should be set so freezing extends shelf life correctly.
    - ``default_best_before_days_after_open == 0``: means "disabled" in
      Grocy — valid, not suspicious.
    - ``default_best_before_days_after_thawing == 0``: means "due date
      defaults to today" — valid Grocy behaviour.
    - ``-1`` is always valid (means "never expires/overdue").
    """
    issues: list[str] = []

    bb = product.get("default_best_before_days", 0)
    if bb == 0:
        issues.append(
            "default_best_before_days is 0 (expires today) — likely not configured"
        )

    bb_freeze = product.get("default_best_before_days_after_freezing", 0)
    if bb_freeze == 0:
        issues.append(
            "default_best_before_days_after_freezing is 0 — not configured"
        )

    return issues
