"""Helpers for classifying calorie conversion basis from quantity units."""

from __future__ import annotations


LIQUID_UNIT_NAMES = {"ml", "cl", "dl", "l"}
WEIGHT_UNIT_NAMES = {"g", "hg", "kg"}


def classify_quantity_unit_basis(unit_name: str | None) -> tuple[bool, bool]:
    """Return (is_liquid, is_weight) for a quantity-unit name."""
    if not unit_name:
        return False, False

    normalized = str(unit_name).strip().lower()
    return normalized in LIQUID_UNIT_NAMES, normalized in WEIGHT_UNIT_NAMES
