"""Framework-agnostic types for the barcode scanning workflow.

This module defines the data structures used by ``ScanSession`` to
communicate with any UI layer.  These types have **no** dependency on
Home Assistant so the scanning business logic can be driven from a
traditional GUI application, a CLI tool, or a pytest suite.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


# ---------------------------------------------------------------------------
# Workflow steps
# ---------------------------------------------------------------------------


class Step(StrEnum):
    """Identifiers for each step in the scanning workflow."""

    MAIN_MENU = "main_menu"

    SCAN_START = "scan_start"
    SCAN_QUEUE = "scan_queue"
    SCAN_MATCH_PRODUCT = "scan_match_to_product"
    SCAN_ADD_PRODUCT = "scan_add_product"
    SCAN_ADD_PRODUCT_PARENT = "scan_add_product_parent"
    SCAN_ADD_PRODUCT_BARCODE = "scan_add_product_barcode"
    SCAN_CREATE_RECIPE = "scan_create_recipe"
    SCAN_UPDATE_PRODUCT_DETAILS = "scan_update_product_details"
    SCAN_TRANSFER_START = "scan_transfer_start"
    SCAN_TRANSFER_INPUT = "scan_transfer_input"
    SCAN_PRODUCE = "scan_produce"
    SCAN_PRODUCE_CONFIRM = "scan_produce_confirm"
    SCAN_PROCESS = "scan_process"


# ---------------------------------------------------------------------------
# Form‑field primitives
# ---------------------------------------------------------------------------


class FieldType(StrEnum):
    """Supported form field types."""

    TEXT = "text"
    NUMBER = "number"
    SELECT = "select"
    BOOLEAN = "boolean"


class NumberMode(StrEnum):
    BOX = "box"
    SLIDER = "slider"


class SelectMode(StrEnum):
    DROPDOWN = "dropdown"
    LIST = "list"


@dataclass
class SelectOption:
    """A single option inside a select / dropdown field."""

    value: str
    label: str


@dataclass
class FormField:
    """Framework-agnostic form field definition.

    Contains all metadata any UI framework needs to render the field,
    validate the input and pass the value back to `ScanSession`.
    """

    key: str
    field_type: FieldType
    required: bool = True
    suggested_value: Any = None
    default: Any = None
    description: str | None = None

    # Select-specific
    options: list[SelectOption] | None = None
    custom_value: bool = False
    select_mode: SelectMode = SelectMode.DROPDOWN
    multiple: bool = False

    # Text-specific
    multiline: bool = False

    # Number-specific
    min_value: float | None = None
    max_value: float | None = None
    step: float | None = None
    number_mode: NumberMode = NumberMode.BOX


# ---------------------------------------------------------------------------
# Step results
# ---------------------------------------------------------------------------


@dataclass
class FormRequest:
    """The workflow needs user input - render this form."""

    step_id: str
    fields: list[FormField]
    description_placeholders: dict[str, str | None] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)


@dataclass
class CompletedResult:
    """The scanning workflow finished successfully."""

    summary: str
    results: list[str] = field(default_factory=list)


@dataclass
class AbortResult:
    """The workflow was aborted (validation error or early exit)."""

    reason: str


#: Union of every possible result returned by ``ScanSession.handle_step``.
StepResult = FormRequest | CompletedResult | AbortResult
