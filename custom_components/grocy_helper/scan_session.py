"""Framework-agnostic barcode scanning workflow.

This module contains the core business logic for scanning barcodes,
looking up products, creating/matching products, and processing barcode
actions (purchase, consume, transfer, etc.).

It is completely independent of Home Assistant so it can be driven from
any UI layer - a traditional desktop/web application, a CLI tool, or a
pytest suite.

Usage example::

    coordinator = GrocyHelperCoordinator(...)
    session = ScanSession(
        coordinator=coordinator,
        api_bbuddy=bbuddy_api,
    )

    # 1. get the initial "scan start" form
    result = await session.handle_step(Step.SCAN_START, None)

    # 2. keep submitting user input until the workflow completes
    while isinstance(result, FormRequest):
        user_input = collect_input_from_ui(result)   # UI-specific
        result = await session.handle_step(result.step_id, user_input)

    # 3. result is now CompletedResult or AbortResult
    print(result)
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import math
import re
from typing import Any

from .barcodebuddyapi import BarcodeBuddyAPI
from .calorie_basis import classify_quantity_unit_basis
from .coordinator import GrocyHelperCoordinator
from .const import (
    CONF_DEFAULT_LOCATION_FREEZER,
    CONF_DEFAULT_LOCATION_FRIDGE,
    CONF_DEFAULT_LOCATION_RECIPE_RESULT,
    CONF_DEFAULT_PRODUCT_GROUP_FOR_RECIPE_RESULT,
    CONF_ENABLE_AUTO_PRINT,
    CONF_ENABLE_CALORIES,
    CONF_ENABLE_PRICES,
    CONF_ENABLE_PRINTING,
    CONF_ENABLE_SHOPPING_LOCATIONS,
    SCAN_MODE,
)
from .grocytypes import (
    BarcodeLookup,
    ExtendedGrocyProductStockInfo,
    GrocyAddProductQuantityUnitConversion,
    GrocyMasterData,
    GrocyProduct,
    GrocyProductBarcode,
    GrocyProductGroup,
    GrocyQuantityUnitConversionResult,
    GrocyRecipe,
    GrocyStockEntry,
)
from .scan_form_builders import ScanFormBuilder
from .scan_product_builders import ProductDataBuilder
from .scan_recipe_builders import RecipeDataBuilder
from .scan_state_manager import ScanStateManager
from .scan_types import (
    AbortResult,
    CompletedResult,
    FieldType,
    FormField,
    FormRequest,
    Step,
    StepResult,
)
from .queue import QueueStatus
from .utils import parse_int, transform_input, try_parse_int

_LOGGER = logging.getLogger(__name__)


RECIPE_PRODUCT_NAME_PREFIX = "Matlåda: "


class ScanSession:
    """Framework-agnostic barcode scanning workflow session.

    Manages the state and business logic for scanning barcodes, looking
    up products, creating/matching products, and processing barcode
    actions (purchase, consume, transfer …).

    Parameters
    ----------
    coordinator:
        A ``GrocyHelperCoordinator`` instance that handles persistence
        and masterdata cache updates.
    api_bbuddy:
        A ``BarcodeBuddyAPI`` instance (or compatible).
    scan_options:
        Dict controlling which extra input fields appear during a
        "Purchase" scan.  Defaults to all enabled.
    """

    def __init__(
        self,
        coordinator: GrocyHelperCoordinator,
        api_bbuddy: BarcodeBuddyAPI,
        scan_options: dict[str, bool] | None = None,
        config_entry_data: dict[str, Any] | None = None,
    ) -> None:
        config_entry_data = config_entry_data or {}
        self._coordinator = coordinator
        self._api_grocy = coordinator._api_grocy
        self._api_bbuddy = api_bbuddy
        self._convert_quantity = coordinator.convert_quantity_for_product

        # Form builder for UI fields
        self._form_builder = ScanFormBuilder(self._coordinator)

        # Product data builder for transformations
        self._product_builder = ProductDataBuilder(self._coordinator)
        self._recipe_builder = RecipeDataBuilder(self._coordinator)

        # State manager for product/stock tracking
        self._state = ScanStateManager(self._api_grocy, self._coordinator)

        self.scan_option_defaults = {
            CONF_ENABLE_PRINTING: bool(
                config_entry_data.get(CONF_ENABLE_PRINTING, False)
            ),
            CONF_ENABLE_AUTO_PRINT: bool(
                config_entry_data.get(CONF_ENABLE_AUTO_PRINT, False)
            ),
            CONF_ENABLE_PRICES: bool(config_entry_data.get(CONF_ENABLE_PRICES, True)),
            CONF_ENABLE_SHOPPING_LOCATIONS: bool(
                config_entry_data.get(CONF_ENABLE_SHOPPING_LOCATIONS, True)
            ),
            CONF_ENABLE_CALORIES: bool(
                config_entry_data.get(CONF_ENABLE_CALORIES, True)
            ),
            "input_product_details_during_provision": True,
            # TODO: Enable detailed Barcode details; defaults for: [shopping_location_id, qu_id, amount] for specific Barcode
            # Whether the "add_product_barcode" form should be shown for manual input during the creation of a recipe produced product.
            "show_add_product_barcode_form_for_recipe_product": False,
            # The values can be pre-filled during the generation of a recipe produced product
            "locations": {
                "default_fridge": parse_int(
                    config_entry_data.get(CONF_DEFAULT_LOCATION_FRIDGE)
                ),
                "default_freezer": parse_int(
                    config_entry_data.get(CONF_DEFAULT_LOCATION_FREEZER)
                ),
            },
            "product_groups": {
                # "Färdiglagat"
                "default_for_recipe_products": parse_int(
                    config_entry_data.get(CONF_DEFAULT_PRODUCT_GROUP_FOR_RECIPE_RESULT)
                ),
            },
            # TODO: Add units? or still use "known_qu"
            "defaults_for_product": {
                "default_best_before_days": 5,
                # "default_best_before_days_after_open": 3,
            },
            "defaults_for_recipe_product": {
                "location_id": parse_int(
                    config_entry_data.get(CONF_DEFAULT_LOCATION_RECIPE_RESULT)
                ),
                "should_not_be_frozen": False,
                "default_best_before_days": 3,
                "default_best_before_days_after_open": 1,
                "default_best_before_days_after_freezing": 60,
                "default_best_before_days_after_thawing": 3,
            },
        }
        if scan_options:
            # Build a new dict so we don't mutate the caller-provided `scan_options`
            # Any passed `scan_options` are considered "overrides"
            self.scan_options: dict[str, bool] = {
                **self.scan_option_defaults,
                **scan_options,
            }
        else:
            # Use a copy to avoid accidental mutation of the defaults elsewhere
            self.scan_options: dict[str, bool] = dict(self.scan_option_defaults)

        # ── workflow state ──────────────────────────────────────────
        self.current_bb_mode: int = -1
        self.barcode_scan_mode: str | None = None
        self.barcode_queue: list[str] = []
        self.barcode_results: list[str] = []
        self.current_barcode: str | None = None
        self.current_barcode_meta: dict[str, Any] = {}

        # Cached form for error re-display
        self._cached_form: FormRequest | None = None
        # Cached process-step schema fields (for error re-display)
        self._cached_process_fields: list[FormField] | None = None
        # Stashed produce-input between form 1 and confirm form
        self._produce_input: dict[str, Any] = {}

        # Handle Queue: ordered list of (barcode, item_id) for status tracking.
        # A list (not a dict) so duplicate barcodes are handled correctly.
        self._queue_item_ids: list[tuple[str, str]] = []
        self._queue_ref: Any = None  # ScanQueue reference

    # ── public helpers ───────────────────────────────────────────────

    @property
    def masterdata(self) -> GrocyMasterData:
        return self._coordinator.data

    @property
    def current_product(self) -> GrocyProduct | None:
        """Current product being worked on (derived from stock info)."""
        return self._state.current_product

    @property
    def current_product_stock_info(self) -> ExtendedGrocyProductStockInfo | None:
        """Extended product information including stock details."""
        return self._state.current_stock_info

    @property
    def current_lookup(self) -> BarcodeLookup | None:
        """Product found during lookup phase."""
        return self._state.current_lookup

    @property
    def current_product_openfoodfacts(self) -> dict | None:
        """OpenFoodFacts product details from lookup."""
        return self._state.current_product_openfoodfacts
        # return self.current_lookup.get("off") if self.current_lookup else None

    @property
    def current_product_ica(self) -> dict | None:
        """ICA-specific product details from lookup."""
        return self._state.current_product_ica
        # return self.current_lookup.get("ica") if self.current_lookup else None

    @property
    def matching_products(self) -> list[GrocyProduct]:
        """List of products matching a search."""
        return self._state.matching_products

    @property
    def current_parent(self) -> GrocyProduct | None:
        """Parent product in parent-child relationship."""
        return self._state.current_parent

    @property
    def current_recipe(self) -> GrocyRecipe | None:
        """Recipe associated with current product."""
        return self._state.current_recipe

    @property
    def current_stock_entries(self) -> list[GrocyStockEntry]:
        """Stock entries for current product."""
        return self._state.current_stock_entries

    # =================================================================
    # Public API
    # =================================================================

    async def handle_step(
        self,
        step_id: str,
        user_input: dict[str, Any] | None,
    ) -> StepResult:
        """Advance the workflow by one step.

        Parameters
        ----------
        step_id:
            Which step to execute (value from ``Step``).
        user_input:
            ``None`` for the initial render of a form, or a ``dict``
            with the values the user submitted.

        Returns
        -------
        StepResult
            ``FormRequest`` when the workflow needs more input,
            ``CompletedResult`` when all barcodes have been processed, or
            ``AbortResult`` on errors / early exit.
        """

        handlers: dict[str, Any] = {
            Step.SCAN_START: self._step_scan_start,
            Step.SCAN_MATCH_PRODUCT: self._step_match_to_product,
            Step.SCAN_ADD_PRODUCT: self._step_add_product,
            Step.SCAN_ADD_PRODUCT_PARENT: self._step_add_product_parent,
            Step.SCAN_ADD_PRODUCT_BARCODE: self._step_add_product_barcode,
            Step.SCAN_UPDATE_PRODUCT_DETAILS: self._step_update_product_details,
            Step.SCAN_TRANSFER_START: self._step_transfer_start,
            Step.SCAN_TRANSFER_INPUT: self._step_transfer_input,
            Step.SCAN_CREATE_RECIPE: self._step_create_recipe,
            Step.SCAN_PRODUCE: self._step_produce,
            Step.SCAN_PRODUCE_CONFIRM: self._step_produce_confirm,
            Step.SCAN_PROCESS: self._step_scan_process,
            Step.HANDLE_QUEUE: self._step_handle_queue,
        }
        handler = handlers.get(step_id)
        if handler is None:
            return AbortResult(reason=f"Unknown step: {step_id}")
        return await handler(user_input)

    # =================================================================
    # Step handlers
    # =================================================================

    # ── handle_queue ─────────────────────────────────────────────────

    async def _step_handle_queue(self, user_input: dict[str, Any] | None) -> StepResult:
        """Show pending queue items and process them on confirmation."""

        queue = getattr(self._coordinator, "queue", None)
        if queue is None:
            return AbortResult(reason="No queue available")

        pending = queue.get_pending_items()
        failed = queue.get_failed_items()
        all_items = pending + failed

        if not all_items:
            return AbortResult(reason="No pending or failed items in queue")

        if user_input is None:
            # Build summary items text
            item_lines = []
            for item in all_items:
                status = "⚠ FAILED" if item.status.value == "failed" else "pending"
                item_lines.append(f"• {item.barcode} ({item.mode}) [{status}]")

            return FormRequest(
                step_id=Step.HANDLE_QUEUE,
                fields=[
                    FormField(
                        key="confirm",
                        field_type=FieldType.BOOLEAN,
                        required=False,
                        default=True,
                        description="Process all pending and failed items",
                    ),
                ],
                description_placeholders={
                    "pending_count": str(len(pending)),
                    "failed_count": str(len(failed)),
                    "items": "\n".join(item_lines),
                },
            )

        # ── user submitted the form ─────────────────────────────────
        if not user_input.get("confirm", False):
            return AbortResult(reason="Queue processing cancelled")

        # Reset failed items back to pending for reprocessing and persist
        for item in failed:
            item.status = QueueStatus.PENDING
            item.error = None
        try:
            await queue._async_save()
        except Exception:
            _LOGGER.exception("Failed to persist queue status reset")
            return AbortResult(reason="Failed to persist queue state")

        # Use the first item's mode as scan mode (convert string → enum)
        raw_mode = all_items[0].mode if all_items else None
        if isinstance(raw_mode, SCAN_MODE):
            self.barcode_scan_mode = raw_mode
        elif raw_mode:
            try:
                self.barcode_scan_mode = SCAN_MODE(raw_mode)
            except ValueError:
                _LOGGER.warning(
                    "Handle Queue: invalid stored scan mode %r, falling back to %s",
                    raw_mode,
                    SCAN_MODE.PURCHASE,
                )
                self.barcode_scan_mode = SCAN_MODE.PURCHASE
        else:
            self.barcode_scan_mode = SCAN_MODE.PURCHASE

        # Populate session barcode_queue from queue items.
        # Use a list of (barcode, item_id) tuples to handle duplicate barcodes.
        self.barcode_queue = []
        self.barcode_results = []
        self._queue_item_ids = []
        self._queue_ref = queue

        for item in all_items:
            self.barcode_queue.append(item.barcode)
            self._queue_item_ids.append((item.barcode, item.id))

        _LOGGER.info(
            "Handle Queue: processing %d items (mode=%s)",
            len(self.barcode_queue),
            self.barcode_scan_mode,
        )

        return await self._step_scan_queue()

    # ── scan_start ───────────────────────────────────────────────────

    async def _step_scan_start(self, user_input: dict[str, Any] | None) -> StepResult:
        """Show the scan-start form or begin processing barcodes."""

        if user_input is None:
            bb_mode = await self._api_bbuddy.get_mode()
            if bb_mode is not None and bb_mode >= 0:
                self.current_bb_mode = bb_mode
            scan_mode_from_bbuddy = self._api_bbuddy.convert_bbuddy_mode_to_scan_mode(
                self.current_bb_mode
            )
            _LOGGER.info("BBuddy mode is: %s (%s)", bb_mode, scan_mode_from_bbuddy)
            return FormRequest(
                step_id=Step.SCAN_START,
                fields=self._form_builder.build_scan_start_fields(
                    scan_mode_from_bbuddy
                ),
            )

        # ── user submitted the form ─────────────────────────────────
        barcodes_input = user_input["barcodes"]
        self.barcode_scan_mode = user_input.get("mode")
        _LOGGER.info("SCAN: %s", barcodes_input)
        _LOGGER.info("SCAN-mode: %s", self.barcode_scan_mode)

        self.barcode_queue = []
        self.barcode_results = []

        # Parse barcodes
        # Supports both:
        # - Regular space-separated barcodes: "123 456" -> ["123", "456"]
        # - Angle-bracket-wrapped barcodes with spaces: "<123|n:test> <456>" -> ["123|n:test", "456"]
        pattern = r"<([^>]+)>|(\S+)"
        matches = re.findall(pattern, barcodes_input)
        self.barcode_queue = [match[0] if match[0] else match[1] for match in matches]
        _LOGGER.info("Parsed barcode queue: %s", self.barcode_queue)

        return await self._step_scan_queue()

    # ── scan_queue (internal - never shows its own form) ─────────────

    async def _step_scan_queue(self) -> StepResult:
        """Process the next barcode in the queue.

        This is an *internal* step - it never renders its own form.
        It chains to whichever visible step is appropriate.
        """

        # Check if queue is empty
        if not self.barcode_queue:
            return self._complete_scan_queue()

        # Prepare current barcode
        raw_barcode = self.barcode_queue[0]

        # Parse structured barcode metadata
        barcode_data = self._parse_structured_barcode(raw_barcode)
        code = self._normalize_barcode(barcode_data["barcode"])

        if self.current_barcode != code:
            # Different barcode since last time. Clear all info
            self._clear_barcode_state()

        self.current_barcode = code
        self.current_barcode_meta = {
            "original_input": raw_barcode,
            "barcode": code,
            "quantity": barcode_data.get("q"),
            "unit": barcode_data.get("u"),
            "price": barcode_data.get("p"),
            "price_sum": barcode_data.get("s"),
            "name": barcode_data.get("n"),
        }
        _LOGGER.info("Parsed barcode metadata: %s", self.current_barcode_meta)

        # Update BarcodeBuddy mode if needed
        await self._update_bbuddy_mode_if_needed()

        # Process barcode based on mode
        if self._should_process_product_lookup():
            _LOGGER.info("Looking up barcode '%s' in Grocy...", code)

            if code == "grcy:r":
                # Create recipe
                result = await self._step_create_recipe(user_input=None)
                if result is not None:
                    return result

            # Handle recipe barcodes
            if "grcy:r:" in code:
                result = await self._handle_recipe_barcode(code)
                if result is not None:
                    return result

            # Handle normal barcodes (not BBUDDY commands)
            if "BBUDDY-" not in code:
                # Lookup or load product
                await self._ensure_product_loaded(code)

                # Check for transfer mode
                if await self._should_enter_transfer_mode():
                    return await self._step_transfer_start(user_input=None)

                # Product doesn't exist → match/create
                if not self.current_product:
                    await self._find_matching_products(code)
                    return await self._step_match_to_product(user_input=None)

        # Handle special modes
        if self.barcode_scan_mode == SCAN_MODE.PROVISION:
            return await self._process_provision_mode(code)

        if self.barcode_scan_mode == SCAN_MODE.INVENTORY:
            await self._ensure_product_loaded(code)

        # Proceed with BarcodeBuddy processing
        return await self._step_scan_process(user_input=None)

    # ── recipe barcode helper ────────────────────────────────────────

    async def _handle_recipe_barcode(self, code: str) -> StepResult | None:
        """Handle a ``grcy:r:<id>`` barcode.  Returns *None* to continue."""

        (r, i) = try_parse_int(code.replace("grcy:r:", ""))
        if not r or i <= 0:
            return AbortResult(reason=f"Could not parse recipe barcode: {code}")

        self._state.current_recipe = next(
            (recipe for recipe in self.masterdata["recipes"] if recipe["id"] == i),
            None,
        )
        if not self.current_recipe:
            # TODO: Flow for creating new recipe??
            return AbortResult(reason=f"Recipe with id '{i}' was not found")

        _LOGGER.debug("Found recipe: %s", self.current_recipe)
        if product_id := self.current_recipe.get("product_id"):
            # Recipe has a producing product, then fetch info and continue flow with product state
            await self._state.load_product_by_id(product_id)
            _LOGGER.info(
                "Recipe '%s' produces product: %s",
                self.current_recipe["id"],
                self.current_product,
            )

            # TODO: Handle "Purchase"/Consume/Inventory/Provision, /Transfer etc. on a recipe barcode. That would be super useful for meal planning, and for tracking the cost and spoilage of cooked meals.
            # TODO: "Purchase" on a recipe, without product, should start the provision product flow... (With Parent-mapping disabled, with recipe barcode, no options for shopping_location)
            # TODO: Investigate possibilty with using Recipe products, with a barcode of "grcy:r:" to help with Purchase/Consume flows?
            # TODO: Recipe produced product: Able to provision automatically:
            #           Unit??
            #           Location: Freezer
            #           Consume: Fridge
            #           Due days,   (helps prevent Freezer burn)
            #           ProductGroup: Matlåda/Färdiglagat
            #           Calories/serving  (helps with kcal per day in Meal plan)
            #           Barcode: add "grcy:r:<id>"
            #   stock entry/journal or Product overview will tell info like: Spoil rate, last purchased (is when cooked last)

            # TODO: During "Purchase"/Produce: Omit fields for ´shopping_location_id´
            # TODO: During "Purchase"/Produce: Gather the cost of the used stock entries used for this batch. And input as price for the Recipe product. That way you could track the cost historically per recipe (per serving)
            # TODO: (During "Purchase"/Produce: Pre-fill bestBeforeInDays from the Produce Product)
            # TODO: During "Purchase"/Produce: Allow to choose the outcome per serving: Eaten/Fridge/Freezer        (mark as "Open", if left in Fridge)
        else:
            # Recipe doesn't produce a product
            # Continue flow without a `self.current_product` set, to provision it (and attach to recipe)
            pass

        return None  # continue queue processing

    # ── match_to_product ─────────────────────────────────────────────

    async def _step_match_to_product(
        self, user_input: dict[str, Any] | None
    ) -> StepResult:
        """Let the user match the barcode to an existing or new product."""

        # First render - show form
        if user_input is None:
            # TODO: If about to create a Product for a Recipe, AND there is no matches, only the suggested "{prefix} {recipe_name}" alias, then SKIP matching form?
            return self._show_match_product_form()

        # Process submitted form
        _LOGGER.info("match-product input: %s", user_input)

        # Validate and process parent product
        parent_error = await self._process_parent_selection(user_input)
        if parent_error:
            return parent_error

        # Validate and process product
        product_error = await self._process_product_selection(user_input)
        if product_error:
            return product_error

        # If product is new → create it
        if not self.current_product.get("id"):
            return await self._step_add_product(user_input=None)

        # TODO: Validate that the product doesn't already belong to a (different) parent!!
        # TODO: Validate that the product doesn't already have a different barcode. Which could cause differences in quantities. (Submit again to add anyway?)
        # Allow for "" or "id" value of the actual parent

        # Existing product (or parent needs creation)
        return await self._step_add_product_parent(user_input=None)

    # ── add_product ──────────────────────────────────────────────────

    async def _step_add_product(self, user_input: dict[str, Any] | None) -> StepResult:
        """Create a new product in Grocy."""
        _LOGGER.info("form 'add_product' input: %s", user_input)

        if self.current_product and self.current_product.get("id"):
            return AbortResult(reason="Product already exists")

        new_product = (self.current_product or {}).copy()
        _LOGGER.info("pre-filled 'new_product': %s", new_product)

        # First render - show form
        if user_input is None:
            return self._show_add_product_form(user_input, new_product, {})

        # ── process submitted form ──────────────────────────────────

        # User selected existing product instead of creating new
        if user_input.get("product_id") and user_input["product_id"] != "-1":
            product_id = int(user_input["product_id"])
            await self._state.load_product_by_id(product_id)
            return await self._step_add_product_barcode(None)

        # Build new product from input
        new_product = self._product_builder.build_product_from_input(
            user_input, new_product
        )

        # Validate location
        if errors := self._product_builder.validate_product_location(new_product):
            return self._show_add_product_form(user_input, new_product, errors)

        # Create product
        _LOGGER.info("Creating product: %s", new_product)
        product = await self._coordinator.create_product(new_product)
        _LOGGER.info("Created product: %s", product)

        # Load full product info via state manager
        await self._state.load_product_by_id(product["id"])

        # Link recipe to product if needed
        if (
            self.current_product
            and self.current_recipe
            and not self.current_recipe.get("product_id")
        ):
            await self._link_recipe_to_product()

        if self.current_recipe and not self.scan_options.get(
            "show_add_product_barcode_form_for_recipe_product"
        ):
            # Submit automatically...
            return await self._step_add_product_barcode(
                user_input={"note": self.current_recipe["name"]}
            )
        return await self._step_add_product_barcode(user_input=None)

    # ── add_product_parent ───────────────────────────────────────────

    async def _step_add_product_parent(
        self, user_input: dict[str, Any] | None
    ) -> StepResult:
        """Optionally create a parent product for the current product."""

        # Check if parent step should be skipped
        skip_result = await self._should_skip_parent_step()
        if skip_result:
            return skip_result

        self._cached_form = None
        _LOGGER.info("form 'add_product_parent' user_input: %s", user_input)

        new_product: dict = (self.current_parent or {}).copy()
        creating_parent = True

        # First render - show form
        if user_input is None:
            suggested = self._product_builder.build_parent_product_suggested_values(
                new_product, {}, creating_parent, self.current_product
            )
            return self._show_add_product_parent_form(
                new_product, suggested, creating_parent, {}
            )

        # ── process submitted form ──────────────────────────────────

        # User selected existing product instead of creating new
        if user_input.get("product_id") and user_input["product_id"] != "-1":
            await self._process_parent_product_selection(user_input)
            return await self._step_scan_queue()

        # Build new parent product from input
        new_product = self._product_builder.build_parent_product_from_input(
            user_input, new_product, creating_parent, self.current_product
        )

        # Validate location
        if errors := self._product_builder.validate_product_location(new_product):
            suggested = self._product_builder.build_parent_product_suggested_values(
                new_product, user_input, creating_parent, self.current_product
            )
            return self._show_add_product_parent_form(
                new_product, suggested, creating_parent, errors
            )

        # Create parent product
        _LOGGER.info("Creating parent product: %s", new_product)
        product = await self._coordinator.create_product(new_product)
        _LOGGER.info("Created parent product: %s", product)
        self._state.current_parent = product

        # Link child product to parent if needed
        await self._link_child_to_parent()

        # Done with parent → continue with queue
        return await self._step_scan_queue()

    # ── add_product_barcode ──────────────────────────────────────────

    async def _step_add_product_barcode(
        self, user_input: dict[str, Any] | None
    ) -> StepResult:
        """Create a barcode entry for a newly-created product."""

        errors: dict[str, str] = {}
        self._cached_form = None
        code = self.current_barcode

        # new_product: GrocyProduct = (self.current_product_stock_info or {}).get(
        #     "product"
        # )
        new_product = self.current_product

        if user_input is None:
            suggested: dict[str, Any] = {
                "note": new_product["name"] if new_product else "",
            }
            fields = self._form_builder.build_create_barcode_fields(
                suggested,
                scan_options=self.scan_options,
            )
            aliases = self._get_aliases()
            plc = {
                "name": new_product.get("name") if new_product else None,
                "barcode": code,
                "recipe_info": (
                    f"## Recipe\n#{self.current_recipe['id']} {self.current_recipe['name']}"
                    if self.current_recipe
                    else None
                ),
                "product_aliases": "\n".join([f"- {a.strip()}" for a in aliases if a]),
                "lookup_output": self._format_lookup_output(),
            }
            self._cached_form = FormRequest(
                step_id=Step.SCAN_ADD_PRODUCT_BARCODE,
                fields=fields,
                description_placeholders=plc,
                errors=errors,
            )
            return self._cached_form

        # ── process ─────────────────────────────────────────────────
        shopping_location_id = None
        if self.scan_options.get(CONF_ENABLE_SHOPPING_LOCATIONS, True):
            shopping_location_id = user_input.get("shopping_location_id")

        br: GrocyProductBarcode = {
            "barcode": code,
            "note": user_input.get("note", ""),
            "product_id": new_product["id"],
            "qu_id": user_input.get("qu_id"),
            "shopping_location_id": shopping_location_id,
            "amount": user_input.get("amount"),
            "row_created_timestamp": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        pcode = await self._api_grocy.add_product_barcode(br)
        _LOGGER.info("created prod_barcode: %s", pcode)

        if self.scan_options.get("input_product_details_during_provision"):
            return await self._step_update_product_details(user_input=None)

        return await self._step_add_product_parent(user_input=None)

    # ── update_product_details ───────────────────────────────────────

    async def _step_update_product_details(
        self, user_input: dict[str, Any] | None
    ) -> StepResult:
        """Update product details (quantity, calories, shelf life)."""

        errors: dict[str, str] = {}
        _LOGGER.info("form 'update_product_details': %s", user_input)

        # product_stock_info = self.current_product_stock_info
        # product = product_stock_info["product"]
        product = self.current_product

        show_form = user_input is None
        if user_input is None:
            user_input = {}

        # Suggestions
        suggestions = (
            self._get_recipe_product_defaults()
            if self.current_recipe
            else self._get_product_defaults()
        )
        _LOGGER.info("Suggestions: %s", suggestions)

        # Initialize and transform input based on states
        user_input = transform_input(
            user_input, persisted=self.current_product, suggested=suggestions
        )
        _LOGGER.info("Transformed user_input: %s", user_input)

        if self.current_product_ica is not None:
            # TODO: fill in info from ICA...
            pass

        # TODO: fill in guess of QuantityUnit...

        # Parse OpenFoodFacts data
        (
            product_quantity,
            product_quantity_unit,
            product_quantity_unit_as_liquid,
            product_quantity_unit_as_weight,
            kcal,
        ) = self._product_builder.parse_openfoodfacts_data(
            user_input, self.current_product_openfoodfacts
        )

        # Determine quantity unit and whether conversions are needed
        (
            qu_id_product,
            skip_add_qu_conversions,
            product_quantity_unit_as_liquid,
            product_quantity_unit_as_weight,
        ) = await self._determine_quantity_unit(
            user_input,
            product,
            product_quantity_unit,
            product_quantity_unit_as_liquid,
            product_quantity_unit_as_weight,
        )

        # First render - show form
        if show_form:
            # Initial render, pre-fill some extra suggestions...
            # TODO: Dynamically set some fields
            # - Product quantity '1' ?
            # - Product quantity unit 'portion' ?
            # - Calories per 100 (calculate from ingredients)

            # user_input = transform_input(
            #     user_input, persisted=self.current_product, suggested={
            #         "calories_per_100": kcal
            #     }
            # )
            # _LOGGER.info("Transformed user_input extra: %s", user_input)

            user_input = self._prepare_form_defaults(
                user_input, qu_id_product, product_quantity, kcal
            )
            _LOGGER.info("Transformed user_input defaults: %s", user_input)
            return self._show_update_product_details_form(user_input, product, errors)

        # ── process submitted values ────────────────────────────────
        _LOGGER.info(
            "About to add conv: %s %s %s",
            product["qu_id_stock"],
            qu_id_product,
            product_quantity,
        )

        product_updates: dict = {}

        # Create quantity unit conversion if needed
        if not skip_add_qu_conversions and qu_id_product and product_quantity:
            await self._create_quantity_unit_conversion(
                product,
                qu_id_product,
                product_quantity,
                product_quantity_unit_as_liquid,
                product_quantity_unit_as_weight,
                product_updates,
            )

        # Collect other product updates
        if val := user_input.get("default_consume_location_id"):
            product_updates["default_consume_location_id"] = val
        if val := user_input.get("default_best_before_days_after_freezing"):
            product_updates["default_best_before_days_after_freezing"] = int(val)
        if val := user_input.get("default_best_before_days_after_thawing"):
            product_updates["default_best_before_days_after_thawing"] = int(val)

        # Calculate calories per pack if possible
        if kcal is not None and self.scan_options.get(CONF_ENABLE_CALORIES, True):
            calories = await self._calculate_calories_per_pack(
                product,
                kcal,
                product_quantity_unit_as_liquid,
                product_quantity_unit_as_weight,
            )
            if calories is not None:
                product_updates["calories"] = calories

        # Apply updates
        if product_updates:
            _LOGGER.info("Will update product: #%s %s", product["id"], product_updates)
            await self._coordinator.update_product(product["id"], product_updates)
            if self.current_product:
                self._state.update_current_product(product_updates)

        return await self._step_add_product_parent(user_input=None)

    # ── transfer_start ───────────────────────────────────────────────

    async def _step_transfer_start(
        self, user_input: dict[str, Any] | None
    ) -> StepResult:
        """Choose which stock entry to transfer."""

        errors: dict[str, str] = {}
        _LOGGER.info("transfer-start: %s", user_input)

        if not self.current_product_stock_info:
            return AbortResult(reason="No product info found during transfer!")
        if len(self.current_stock_entries) < 1:
            return AbortResult(reason="No stock entries to transfer")

        if user_input is None and len(self.current_stock_entries) > 1:
            _LOGGER.warning("Existing stock entries: %s", self.current_stock_entries)
            product = self.current_product_stock_info["product"]
            fields = self._form_builder.build_choose_stock_entry_fields(
                product, self.current_stock_entries
            )
            return FormRequest(
                step_id=Step.SCAN_TRANSFER_START,
                fields=fields,
                errors=errors,
            )

        stock_entry_id = (
            int(user_input.get("stock_entry_id"))
            if user_input
            else self.current_stock_entries[0]
        )

        for stock_entry in filter(
            lambda p: p["id"] == stock_entry_id,
            self.current_stock_entries,
        ):
            self._state.current_stock_entries = [stock_entry]
            _LOGGER.warning("CURRENT se: %s", self.current_stock_entries)

        return await self._step_transfer_input(user_input=None)

    # ── transfer_input ───────────────────────────────────────────────

    async def _step_transfer_input(
        self, user_input: dict[str, Any] | None
    ) -> StepResult:
        """Specify amount and target location for the transfer."""

        errors: dict[str, str] = {}
        _LOGGER.info("transfer-input: %s", user_input)

        if not self.current_product_stock_info:
            return AbortResult(reason="No product info found during transfer!")
        if len(self.current_stock_entries) != 1:
            return AbortResult(
                reason="Should only have one chosen stock entry to transfer"
            )

        if user_input is None:
            product = self.current_product_stock_info["product"]
            stock_entry = self.current_stock_entries[0]
            fields = self._form_builder.build_transfer_input_fields(
                product, stock_entry
            )
            return FormRequest(
                step_id=Step.SCAN_TRANSFER_INPUT,
                fields=fields,
                errors=errors,
            )

        product = self.current_product_stock_info["product"]
        stock_entry = self.current_stock_entries[0]
        amount = user_input.get("amount", stock_entry["amount"])
        location_to_id = user_input["location_to_id"]

        data = {
            "amount": amount,
            "location_id_from": int(stock_entry["location_id"]),
            "location_id_to": int(location_to_id),
            "stock_entry_id": stock_entry["stock_id"],
        }
        _LOGGER.warning("Posting transfer: %s", data)
        result = await self._api_grocy.transfer_stock_entry(product["id"], data=data)
        _LOGGER.info("Completed transfer: %s", result)

        self.barcode_queue.pop(0)
        self.barcode_results.append(
            f"{product['name']} transferred to loc #{location_to_id}"
        )
        return await self._step_scan_queue()

    # ── create_recipe ──────────────────────────────────────────────────

    async def _step_create_recipe(
        self, user_input: dict[str, Any] | None
    ) -> StepResult:
        """Create a new recipe in Grocy."""

        if self.current_recipe and self.current_recipe.get("id"):
            return AbortResult(reason="Recipe already exists")

        new_recipe: GrocyRecipe = {}
        enable_printing = self.scan_options.get(CONF_ENABLE_PRINTING, False)

        # First render - show form
        if user_input is None:
            return self._show_create_recipe_form(
                suggestions={
                    **new_recipe,
                    "print": self.scan_options.get(CONF_ENABLE_AUTO_PRINT, False),
                },
                printing_enabled=enable_printing,
            )

        # ── process submitted form ──────────────────────────────────

        # Build new recipe from input
        new_recipe = self._recipe_builder.build_recipe_from_input(
            user_input, new_recipe
        )
        # TODO: If URL was passed instead of name, then we should scrape recipe. Currently the idea is to use `recipe-buddy`

        # Create recipe
        recipe = await self._coordinator.create_recipe(new_recipe)
        _LOGGER.info("Created recipe: %s", recipe)

        self._state.current_recipe = recipe

        self.barcode_queue.pop(0)
        self.barcode_results.append(
            f"Created recipe {self.current_recipe['name']} with id {recipe['id']}"
        )
        _LOGGER.info(
            "Inserting barcode for created recipe into queue: %s",
            f"grcy:r:{self.current_recipe['id']}",
        )

        if enable_printing and user_input.get("print"):
            # Print label for Recipe booklet
            _LOGGER.info("Sending print command for recipe: %s", recipe)
            await self._api_grocy.print_label_for_recipe(recipe["id"])

        # TODO: Perhaps we abort the options flow here. Until we can invoke a scraper here, synchronously
        self.barcode_queue.insert(0, f"grcy:r:{self.current_recipe['id']}")
        return await self._step_scan_queue()

        # # Done with recipe creation → finish flow
        # return await self._handle_scan_success(
        #     response={
        #         "message": f"Recipe '{recipe['name']}' with id {recipe['id']} created successfully!"
        #     }
        # )

        # return None  # continue queue processing

    # ── scan_process ─────────────────────────────────────────────────

    async def _step_scan_process(self, user_input: dict[str, Any] | None) -> StepResult:
        """Final processing step: call BBuddy / Grocy to execute the action."""

        errors: dict[str, str] = {}
        code = self.current_barcode

        # Ensure stock info is loaded
        await self._ensure_product_stock_loaded()

        product = self.current_product or (self.current_product_stock_info or {}).get(
            "product", {}
        )

        # Check if in purchase mode
        in_purchase_mode = self._is_in_purchase_mode()

        # ── Produce flow (recipe context) ───────────────────────────
        if in_purchase_mode and self.current_recipe:
            return await self._step_produce(user_input)

        # Extract input values
        price, best_before_in_days, shopping_location_id = (
            self._extract_scan_process_input(user_input, product)
        )

        # Show form if needed
        if user_input is None and in_purchase_mode:
            if self.current_barcode_meta and "price" in self.current_barcode_meta:
                price = self.current_barcode_meta["price"]

            if form := self._show_scan_process_form(
                product, price, best_before_in_days, shopping_location_id, errors
            ):
                # TODO: Add fields for Amount and QU_ID
                return form

        # Build request
        request = self._product_builder.build_scan_request(
            code, in_purchase_mode, price, best_before_in_days, shopping_location_id
        )

        # Once product has been ensured to exist in Grocy, we can continue with BBuddy call
        # TODO: ignore BBuddy call if scan-mode is "lookup-barcode" or "provision-barcode"

        # Set BarcodeBuddy mode
        await self._set_bbuddy_mode()

        # Execute the action
        try:
            # TODO: make Barcode Buddy obsolete? Instead do everything via Grocy API?. Gives more control, and cuts of middlehand. But looses the BBuddy UI and it's contextual settings.
            response = await self._execute_scan_action(request, in_purchase_mode)
            # TODO: handle responses with HTML-tags (warning/error messages)
            return await self._handle_scan_success(response)
        except BaseException as be:
            return self._handle_scan_error(be, errors)

    # =================================================================
    # Form-field builders - now in scan_form_builders.py
    # =================================================================
    # All form building logic has been moved to ScanFormBuilder class

    # =================================================================
    # Public helpers
    # =================================================================

    # transform_input has been moved to utils.py

    # =================================================================
    # Private helpers
    # =================================================================

    def _parse_structured_barcode(self, barcode_str: str) -> dict[str, Any]:
        """Parse structured barcode format into a dictionary.

        Parses strings like:
        "3392590205420|q:2|u:st|p:25.0|s:50.0|n:Pizza Surdeg"

        Into:
        {
            "barcode": "3392590205420",
            "q": "2",
            "u": "st",
            "p": "25.0",
            "s": "50.0",
            "n": "Pizza Surdeg"
        }

        Parameters
        ----------
        barcode_str:
            The barcode string to parse. Can be simple ("123456"), multiple ("123 456") or
            structured ("<123456|q:1|u:st|p:10.0|n:Product Name>")

        Returns
        -------
        dict
            Dictionary with parsed values. Always includes "barcode" key.
            For simple barcodes, only "barcode" key is present.
            For structured barcodes, includes all key:value pairs found.
        """
        parts = barcode_str.split("|")
        result = {"barcode": parts[0]}

        # Parse key:value pairs from remaining parts
        for part in parts[1:]:
            if ":" in part:
                key, value = part.split(":", 1)  # Split on first ':' only
                if value:
                    result[key] = value.strip()

        return result

    def _get_aliases(self) -> list[str]:
        """Return product name aliases from lookup data or recipe."""
        aliases = []
        if self.current_lookup:
            aliases.extend(self.current_lookup.get("product_aliases") or [])

        if barcode_name := (self.current_barcode_meta or {}).get("name"):
            if barcode_name not in aliases:
                aliases.insert(0, barcode_name)

        if self.current_recipe:
            return [f"Matlåda: {self.current_recipe['name']}"]

        # TODO: also loop through ProductBarcode notes
        # TODO: ICA offer name
        # TODO: skip Active==0 products
        return aliases

    def _try_map_product_group(self) -> GrocyProductGroup | None:
        groups = self.masterdata.get("product_groups")
        if not groups:
            return None

        off = self._state.current_product_openfoodfacts or {}
        _LOGGER.debug("PGs: %s", groups)
        if lookup_categories := (off.get("categories") or []):
            # If OpenFoodFacts data has categories, try to match them to active product groups in Grocy
            _LOGGER.debug(
                "Trying to match OpenFoodFacts categories '%s' to product groups...",
                lookup_categories,
            )
            for pg in groups:
                if pg.get("active") != 1:
                    continue
                group_categories = (pg.get("userfields") or {}).get(
                    "off_categories"
                ) or ""
                if not group_categories:
                    continue
                group_categories = [
                    c.strip() for c in group_categories.split(",") if c.strip()
                ]
                if any(x in group_categories for x in lookup_categories):
                    return pg

        ica = self._state.current_product_ica or {}
        ica_article = ica.get("article") or {}
        if ica_article_group_id := (
            ica_article.get("expandedArticleGroupId")
            or ica_article.get("articleGroupId")
        ):
            # If ICA data has an 'articleGroupId' or 'expandedArticleGroupId', try to match it to an active product group in Grocy
            if pg := next(
                (
                    pg
                    for pg in groups
                    if pg.get("active") == 1
                    and (pg.get("userfields") or {}).get("ica_group_id")
                    == str(ica_article_group_id)
                ),
                None,
            ):
                return pg

        return None

    def _show_add_product_form(
        self,
        user_input: dict[str, Any],
        product: dict[str, Any],
        errors: dict[str, str],
    ) -> FormRequest:
        """Build and return the add-product form."""
        defaults = (
            self._get_recipe_product_defaults()
            if self.current_recipe
            else self._get_product_defaults()
        )
        if "product_group_id" not in defaults:
            if product_group := self._try_map_product_group():
                defaults["product_group_id"] = product_group["id"]
                if loc_id := (product_group.get("userfields") or {}).get("location_id"):
                    # If product group has a default location id, then use that instead of previous default value...
                    defaults["location_id"] = loc_id

        _LOGGER.info("Defaults: %s", defaults)

        # Resolve suggestions with a transform
        suggested = transform_input(
            user_input,
            persisted=product,
            suggested=defaults,
            keys=[
                "name",
                "product_group_id",
                "location_id",
                "should_not_be_frozen",
                "treat_opened_as_out_of_stock",
                "default_best_before_days",
                "default_best_before_days_after_open",
                "qu_id_stock",
                "qu_id_purchase",
                "qu_id_consume",
                "qu_id_price",
            ],
        )
        _LOGGER.info("Suggested: %s", suggested)
        fields = self._form_builder.build_create_product_fields(
            suggested, creating_parent=False
        )
        aliases = self._get_aliases()
        return FormRequest(
            step_id=Step.SCAN_ADD_PRODUCT,
            fields=fields,
            description_placeholders={
                "name": product.get("name"),
                "barcode": self.current_barcode,
                "product_aliases": "\n".join([f"- {a.strip()}" for a in aliases if a]),
                "lookup_output": self._format_lookup_output(),
                "name_description": f'Can be prefixed with "{RECIPE_PRODUCT_NAME_PREFIX}" for easier identification of cooked products',
            },
            errors=errors,
        )

    def _show_match_product_form(self) -> FormRequest:
        """Build and return the match-product form."""
        _LOGGER.warning("Matching products: %s", self.matching_products)
        aliases = self._get_aliases()
        allow_parent = not self.current_recipe
        fields = self._form_builder.build_match_product_fields(
            suggested_products=self.matching_products,
            aliases=aliases,
            allow_parent=allow_parent,
            current_lookup=self.current_lookup,
        )
        self._cached_form = FormRequest(
            step_id=Step.SCAN_MATCH_PRODUCT,
            fields=fields,
            description_placeholders={
                "barcode": self.current_barcode,
                "recipe_info": (
                    f"## Recipe\n#{self.current_recipe['id']} {self.current_recipe['name']}"
                    if self.current_recipe
                    else None
                ),
                "product_aliases": "\n ".join([f"- {a.strip()}" for a in aliases if a]),
                "lookup_output": self._format_lookup_output(),
                "product_matches": "\n".join(
                    f"{p['name']}" for p in self.matching_products
                ),
            },
            errors={},
        )
        return self._cached_form

    def _show_create_recipe_form(
        self,
        suggestions: dict[str, Any] | None = None,
        errors: dict[str, str] | None = None,
        printing_enabled: bool = False,
    ) -> FormRequest:
        """Build and return the add-recipe form."""
        suggestions = suggestions or {}
        fields = self._form_builder.build_create_recipe_fields(
            suggestions=suggestions,
            printing_enabled=printing_enabled,
        )
        return FormRequest(
            step_id=Step.SCAN_CREATE_RECIPE,
            fields=fields,
            description_placeholders={
                "name": suggestions.get("name"),
                "barcode": self.current_barcode,
                "recipe_product_name_prefix": RECIPE_PRODUCT_NAME_PREFIX,
            },
            errors=errors,
        )

    async def _process_parent_selection(
        self, user_input: dict[str, Any]
    ) -> StepResult | None:
        """Process the parent_product field. Returns error result or None."""
        self._state.current_parent = None

        if not (p := user_input.get("parent_product")):
            return None  # No parent specified

        # Recipe products cannot have parents
        if self.current_recipe:
            error = "Not allowed when creating a recipe product"
            if self._cached_form:
                self._cached_form.errors = {"parent_product": error}
                return self._cached_form
            return AbortResult(reason=error)

        # Try to parse as ID
        (r, i) = try_parse_int(p)
        if r and i > 0:
            self._state.current_parent = await self._api_grocy.get_product_by_id(
                i
            )  # TODO: Get from coordinator masterdata

        # If not found or was a string, treat as new parent product name
        if self.current_parent is None:
            self._state.current_parent = {"name": p if p != "-1" else None}

        return None

    async def _process_product_selection(
        self, user_input: dict[str, Any]
    ) -> StepResult | None:
        """Process the product_id field. Returns error result or None."""
        self._state.set_product(None)

        p = user_input.get("product_id")
        if not p:
            error = "Missing value"
            if self._cached_form:
                self._cached_form.errors = {"product_id": error}
                return self._cached_form
            return AbortResult(reason="Missing product_id")

        # Try to parse as ID
        (r, i) = try_parse_int(p)
        if r and i > 0:
            # Load existing product via state manager
            await self._state.load_product_by_id(i)

            # Link recipe to product if needed
            if (
                self.current_product
                and self.current_recipe
                and not self.current_recipe.get("product_id")
            ):
                await self._link_recipe_to_product()

        # If not found or was a string, create new product template
        if self.current_product is None:
            self._state.set_product(
                {
                    "name": p if p != "-1" else None,
                    "parent_product_id": (
                        self.current_parent.get("id") if self.current_parent else None
                    ),
                }
            )
            # TODO: Simplify

            # # Add recipe defaults
            # if self.current_recipe:
            #     self._apply_recipe_product_defaults()

        return None

    async def _link_recipe_to_product(self) -> None:
        """Link the current recipe to the current product."""
        recipe_changes = {"product_id": self.current_product["id"]}
        await self._coordinator.update_recipe(self.current_recipe["id"], recipe_changes)
        _LOGGER.info(
            "Linked recipe #%s to product #%s",
            self.current_recipe["id"],
            self.current_product["id"],
        )
        self.current_recipe.update(recipe_changes)

    def _get_product_defaults(self) -> dict[str, Any]:
        """Get the default values for products."""
        defaults = self.scan_options.get("defaults_for_product", {}).copy()

        if product_presets := self._coordinator.data.get("product_presets"):
            defaults |= {
                key: value
                for key, value in product_presets.items()
                if value is not None
            }

        return defaults

    def _get_recipe_product_defaults(self) -> dict[str, Any]:
        """Get the default values for recipe products."""
        locations = self.scan_options.get("locations", {})

        # Start with base product defaults
        base = self._get_product_defaults()
        suggestions = {}
        suggestions |= base

        # Resolve dynamic fields manually:
        # Since this is a 'cooked' product, it belongs in the Fridge or Freezer. As default, suggest to Freeze it first
        suggestions["location_id"] = locations.get("default_freezer")
        suggestions["default_consume_location_id"] = locations.get("default_fridge")
        suggestions["product_group_id"] = self.scan_options.get(
            "product_groups", {}
        ).get("default_for_recipe_products")

        # Update with 'recipe product' defaults
        if over := self.scan_options.get("defaults_for_recipe_product"):
            suggestions |= over

        return suggestions

    def _complete_scan_queue(self) -> CompletedResult:
        """Return completed result when queue is empty."""
        # TODO: Add result info to message...
        msg = (
            "\r\n".join(self.barcode_results)
            if self.barcode_results
            else "No barcodes were processed"
        )
        _LOGGER.info("Scan queue complete: %s items", len(self.barcode_results))
        return CompletedResult(summary=msg, results=list(self.barcode_results))

    def _normalize_barcode(self, barcode: str) -> str:
        """Normalize a barcode string."""
        return barcode.strip().strip(",").strip().lstrip("0")

    def _clear_barcode_state(self) -> None:
        """Clear state when processing a new barcode."""
        self._state.clear_barcode_state()

    async def _update_bbuddy_mode_if_needed(self) -> None:
        """Update BarcodeBuddy mode based on scan mode."""
        if self.barcode_scan_mode == SCAN_MODE.SCAN_BBUDDY:
            bb_mode = await self._api_bbuddy.get_mode()
            if bb_mode is not None and bb_mode >= 0:
                _LOGGER.info("BBuddy mode has been fetched and is: %s", bb_mode)
                self.current_bb_mode = bb_mode
        else:
            self.current_bb_mode = None

    def _should_process_product_lookup(self) -> bool:
        """Check if we should process product lookup for current scan mode."""
        return (
            self.barcode_scan_mode == SCAN_MODE.PROVISION
            or self.barcode_scan_mode not in [SCAN_MODE.INVENTORY, SCAN_MODE.QUANTITY]
        )

    async def _ensure_product_loaded(self, code: str) -> None:
        """Ensure current product info is loaded from barcode."""
        if not self.current_product and not self.current_recipe:
            try:
                await self._state.load_product_by_barcode(code)
                _LOGGER.info("Product lookup: %s", self.current_product_stock_info)

                lookup = await self._state.load_lookup(code)
                _LOGGER.info("Barcode lookup: %s", lookup)
            except Exception as ex:
                _LOGGER.error("Get product exception: %s", ex)
                raise

    async def _should_enter_transfer_mode(self) -> bool:
        """Check if should enter transfer mode and load stock entries."""
        if (
            self.current_product
            and self.current_product.get("id")
            and self.barcode_scan_mode == SCAN_MODE.TRANSFER
        ):
            stock_entries = await self._api_grocy.get_stock_entries_by_product_id(
                self.current_product["id"]
            )
            self._state.current_stock_entries = stock_entries
            return True
        return False

    async def _find_matching_products(self, code: str) -> None:
        """Find products matching the barcode via external lookup."""
        _LOGGER.info("New product, lookup against barcode providers: %s", code)

        # Perform external lookup if needed
        if not self.current_recipe and (
            not self.current_lookup or self.current_lookup["barcode"] != code
        ):
            await self._state.load_lookup(code)

        # Match against existing products by alias
        aliases = (self.current_lookup or {}).get("product_aliases") or []
        recipe_names = (
            [
                self.current_recipe["name"],
                f"Matlåda: {self.current_recipe['name']}",
            ]
            if self.current_recipe
            else []
        )

        for product in self.masterdata["products"]:
            if aliases and product["name"].casefold() in map(str.casefold, aliases):
                _LOGGER.info("Match by alias: %s", product)
                self.matching_products.append(product)
            elif recipe_names and product["name"] in recipe_names:
                _LOGGER.info("Match by recipe name: %s", product)
                self.matching_products.append(product)

    async def _process_provision_mode(self, code: str) -> StepResult:
        """Handle provision mode - just confirm product exists and continue."""
        # Mode is to simply ensure product/barcode exists
        # remove from queue, and then restart the queue...
        self.barcode_queue.pop(0)
        _LOGGER.info("Provisioned: %s", self.current_product)
        self.barcode_results.append(f"{code} maps to {self.current_product['name']}")
        return await self._step_scan_queue()

    # ── Helpers for _step_add_product_parent ─────────────────────────

    async def _should_skip_parent_step(self) -> StepResult | None:
        """Check if parent step should be skipped."""
        if self.current_parent is None:
            _LOGGER.debug(
                "Product will not be linked to a parent, continue to next step..."
            )
            return await self._step_scan_process(user_input=None)
        elif self.current_parent.get("id"):
            _LOGGER.debug("Product parent already exists, continue to next step...")
            return await self._step_scan_process(user_input=None)
        return None

    def _show_add_product_parent_form(
        self, new_product: dict, suggested: dict, creating_parent: bool, errors: dict
    ) -> FormRequest:
        """Show form for creating parent product."""
        if "product_group_id" not in suggested:
            if product_group := self._try_map_product_group():
                suggested["product_group_id"] = product_group["id"]
                if loc_id := (product_group.get("userfields") or {}).get("location_id"):
                    # If product group has a default location id, then use that instead of previous default value...
                    suggested["location_id"] = loc_id

        fields = self._form_builder.build_create_product_fields(
            suggested, creating_parent=creating_parent
        )
        aliases = self._get_aliases()
        plc = {
            "name": new_product.get("name"),
            "barcode": self.current_barcode,
            "product_aliases": "\n".join([f"- {a.strip()}" for a in aliases if a]),
            "lookup_output": self._format_lookup_output(),
        }
        self._cached_form = FormRequest(
            step_id=Step.SCAN_ADD_PRODUCT_PARENT,
            fields=fields,
            description_placeholders=plc,
            errors=errors,
        )
        return self._cached_form

    async def _process_parent_product_selection(self, user_input: dict) -> None:
        """Process user selection of existing parent product."""
        self._state.current_parent = await self._api_grocy.get_product_by_id(
            int(user_input["product_id"])
        )

    async def _link_child_to_parent(self) -> None:
        """Link child product to parent if needed."""
        if self.current_product and not self.current_product.get("parent_product_id"):
            product_updates = {
                "parent_product_id": self.current_parent["id"],
            }
            _LOGGER.info(
                "Will update product: #%s %s",
                self.current_product["id"],
                product_updates,
            )
            await self._coordinator.update_product(
                self.current_product["id"], product_updates
            )
            self._state.update_current_product(product_updates)

    # ── Helpers for _step_update_product_details ──────────────────────

    async def _determine_quantity_unit(
        self,
        user_input: dict,
        product: dict,
        product_quantity_unit: int | None,
        product_quantity_unit_as_liquid: bool,
        product_quantity_unit_as_weight: bool,
    ) -> tuple[int | None, bool, bool, bool]:
        """Determine quantity unit and whether conversions are needed."""
        qu_id_product = user_input.get("qu_id_product", product_quantity_unit)
        skip_add_qu_conversions = False

        if user_input.get("qu_id_product"):
            qu_id_product = int(user_input.get("qu_id_product"))
        elif product_quantity_unit:
            qu_id_product = product_quantity_unit
        else:
            skip_add_qu_conversions = True

        if qu_id_product:
            for qq in filter(
                lambda qu: qu.get("id") == qu_id_product,
                self.masterdata["quantity_units"],
            ):
                _LOGGER.warning("Chosen unit: %s", qq)
                (
                    product_quantity_unit_as_liquid,
                    product_quantity_unit_as_weight,
                ) = classify_quantity_unit_basis(qq.get("name"))
                break

        if not skip_add_qu_conversions:
            if qu_id_product in [
                product.get("qu_id_stock"),
                product.get("qu_id_consume"),
                product.get("qu_id_purchase"),
                product.get("qu_id_price"),
            ]:
                skip_add_qu_conversions = True
            else:
                conversions = await self._api_grocy.resolve_quantity_unit_conversions_for_product_id(
                    product["id"]
                )
                _LOGGER.warning("Convers: %s", conversions)
                # TODO: check if there already is a resolved conversion for those qu_id
                # TODO: if already exists then set ´skip_add_qu_conversions = True´

        return (
            qu_id_product,
            skip_add_qu_conversions,
            product_quantity_unit_as_liquid,
            product_quantity_unit_as_weight,
        )

    def _format_lookup_output(self) -> str:
        lookup_output = f"# Barcode lookup\nBarcode: {self.current_barcode}"
        if self.current_barcode_meta:
            if name := self.current_barcode_meta.get("name"):
                lookup_output += f"\nName: {name}"
            if q := self.current_barcode_meta.get("quantity"):
                unit = self.current_barcode_meta.get("unit")
                lookup_output += f"\nQuantity: {q} {unit}"
        if output := (self.current_lookup or {}).get("lookup_output"):
            lookup_output += f"\n\n{output}"
        return lookup_output

    def _prepare_form_defaults(
        self,
        user_input: dict,
        qu_id_product: int | None,
        product_quantity: float | None,
        kcal: float | None,
    ) -> dict:
        """Prepare defaults for form rendering."""
        if qu_id_product_val := user_input.get("qu_id_product", qu_id_product):
            user_input["qu_id_product"] = str(qu_id_product_val)
        user_input["product_quantity"] = user_input.get(
            "product_quantity", product_quantity
        )
        user_input["calories_per_100"] = user_input.get("calories_per_100", kcal)
        return user_input

    def _show_update_product_details_form(
        self, user_input: dict, product: dict, errors: dict
    ) -> FormRequest:
        """Show form for updating product details."""
        fields = self._form_builder.build_update_product_details_fields(
            user_input, product, scan_options=self.scan_options
        )
        aliases = self._get_aliases()
        plc = {
            "name": product.get("name"),
            "barcode": self.current_barcode,
            "recipe_info": (
                f"## Recipe\n#{self.current_recipe['id']} {self.current_recipe['name']}"
                if self.current_recipe
                else None
            ),
            "product_aliases": "\n".join([f"- {a.strip()}" for a in aliases if a]),
            "lookup_output": self._format_lookup_output(),
        }
        self._cached_form = FormRequest(
            step_id=Step.SCAN_UPDATE_PRODUCT_DETAILS,
            fields=fields,
            description_placeholders=plc,
            errors=errors,
        )
        return self._cached_form

    async def _create_quantity_unit_conversion(
        self,
        product: dict,
        qu_id_product: int,
        product_quantity: float,
        product_quantity_unit_as_liquid: bool,
        product_quantity_unit_as_weight: bool,
        product_updates: dict,
    ) -> None:
        """Create quantity unit conversion and update price QU."""
        conv: GrocyAddProductQuantityUnitConversion = {
            "from_qu_id": product["qu_id_stock"],
            "to_qu_id": qu_id_product,
            "product_id": product["id"],
            "row_created_timestamp": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "factor": float(product_quantity),
        }
        await self._coordinator.create_quantity_unit_conversion(conv)

        if product_quantity_unit_as_liquid:
            product_updates["qu_id_price"] = (
                self.masterdata["known_qu"].get("L", {}).get("id")
            )
        elif product_quantity_unit_as_weight:
            product_updates["qu_id_price"] = (
                self.masterdata["known_qu"].get("kg", {}).get("id")
            )
        else:
            _LOGGER.warning("Unknown quantity unit type: %s", qu_id_product)

    async def _calculate_calories_per_pack(
        self,
        product: dict,
        kcal: float,
        product_quantity_unit_as_liquid: bool,
        product_quantity_unit_as_weight: bool,
    ) -> float | None:
        """Calculate calories per pack using QU conversion."""
        if not product_quantity_unit_as_liquid and not product_quantity_unit_as_weight:
            _LOGGER.warning(
                "Unsupported OFF quantity basis for calorie conversion; skipping calories update."
            )
            return None

        gram_unit = self.masterdata["known_qu"].get("g")
        if product_quantity_unit_as_liquid:
            gram_unit = self.masterdata["known_qu"].get("ml")

        if not gram_unit:
            return None

        kcal_per_gram = kcal / 100
        c: GrocyQuantityUnitConversionResult = await self._convert_quantity(
            product["id"],
            int(product["qu_id_stock"]),
            gram_unit["id"],
            1,
        )
        if not c:
            _LOGGER.warning(
                "No conversion found from product QU to grams/ml, cannot calculate calories per pack. %s -> %s",
                product["qu_id_stock"],
                gram_unit["id"],
            )
            return None

        # TODO: handle c is None
        _LOGGER.warning(
            "Converted: %s %s -> %s %s",
            c["from_amount"],
            c["from_qu_name"],
            c["to_amount"],
            c["to_qu_name"],
        )
        grams_per_pack = c["to_amount"]
        return math.ceil(kcal_per_gram * grams_per_pack)

    # ── Produce flow ────────────────────────────────────────────────

    async def _step_produce(
        self,
        user_input: dict[str, Any] | None,
    ) -> StepResult:
        """Handle produce input form (Form 1 of 2).

        Collects servings cooked, containers to produce, location and
        total ingredient cost.  On submit, stashes the values and
        transitions to the confirmation step.
        """

        recipe = self.current_recipe
        errors: dict[str, str] = {}

        await self._ensure_product_stock_loaded()
        product = self.current_product or (self.current_product_stock_info or {}).get(
            "product", {}
        )

        # ── First render: show produce input form ───────────────────
        if user_input is None:
            recipe_cost: float | None = None
            fulfillment_calories: float | None = None
            try:
                fulfillment = await self._api_grocy.get_recipe_fulfillment(recipe["id"])

                # costs in fulfillment is scaled by desired_servings.
                # Normalize to per-base-serving so we can re-scale to user's servings.
                desired = int(recipe.get("desired_servings", 1) or 1)
                raw_costs = fulfillment.get("costs")
                if raw_costs is not None:
                    cost_per_serving = float(raw_costs) / max(desired, 1)
                    base_s = int(recipe.get("base_servings", 1) or 1)
                    # Pre-fill with cost scaled to base_servings (user can edit)
                    recipe_cost = round(cost_per_serving * base_s, 2)

                # calories in fulfillment is total for 1× base_servings
                # (amounts NOT scaled by desired_servings in the SQL view).
                raw_cal = fulfillment.get("calories")
                if raw_cal is not None:
                    fulfillment_calories = float(raw_cal)

                _LOGGER.info(
                    "Recipe #%s fulfillment — costs: %s, calories: %s",
                    recipe["id"],
                    recipe_cost,
                    fulfillment_calories,
                )
            except Exception:
                _LOGGER.warning(
                    "Could not fetch recipe fulfillment for #%s",
                    recipe["id"],
                    exc_info=True,
                )

            base_servings = int(recipe.get("base_servings", 1) or 1)

            # Default location from the producing product, not from scan_options
            default_location = product.get("location_id")

            fields = self._form_builder.build_produce_fields(
                product=product,
                location_id=default_location,
                recipe_cost=recipe_cost,
                base_servings=base_servings,
                scan_options=self.scan_options,
            )
            self._cached_process_fields = fields

            # Pre-stash fulfillment data so it survives across the form round-trip
            self._produce_input = {
                "fulfillment_calories": fulfillment_calories,
            }

            return FormRequest(
                step_id=Step.SCAN_PRODUCE,
                fields=fields,
                description_placeholders={
                    "name": product.get("name"),
                    "recipe_info": (
                        f"## Produce: {recipe['name']}\nBase servings: {base_servings}"
                    ),
                },
                errors=errors,
            )

        # ── Validate submitted values before stashing ───────────────
        _ok_s, produce_servings = try_parse_int(user_input.get("produce_servings"))
        _ok_a, produce_amount = try_parse_int(user_input.get("produce_amount"))

        if not _ok_s or produce_servings < 1:
            errors["produce_servings"] = "produce_servings_min_1"
        if (
            not _ok_a
            or produce_amount < 0
            or (_ok_s and produce_servings >= 1 and produce_amount > produce_servings)
        ):
            errors["produce_amount"] = "produce_amount_invalid"

        if errors:
            # Re-render form with the validation errors
            cached = self._cached_process_fields or []
            return FormRequest(
                step_id=Step.SCAN_PRODUCE,
                fields=cached,
                errors=errors,
                description_placeholders={
                    "name": (
                        product.get("name", "")
                        if isinstance(product, dict)
                        else getattr(product, "name", "")
                    ),
                    "recipe_info": getattr(recipe, "name", "") if recipe else "",
                },
            )

        # ── Stash submitted values and go to confirmation ───────────
        self._produce_input.update(
            {
                "produce_consume_ingredients": bool(
                    user_input.get("produce_consume_ingredients", True)
                ),
                "produce_servings": produce_servings,
                "produce_amount": produce_amount,
                "produce_location_id": int(user_input["produce_location_id"]),
                "produce_price": user_input.get("produce_price"),
            }
        )
        return await self._step_produce_confirm(user_input=None)

    # ── produce_confirm ──────────────────────────────────────────────

    async def _step_produce_confirm(
        self, user_input: dict[str, Any] | None
    ) -> StepResult:
        """Produce confirmation form (Form 2 of 2).

        Shows a summary of servings, calories and price per serving.
        On submit: consumes recipe ingredients, creates stock entries,
        prints labels.
        """
        recipe = self.current_recipe
        product = self.current_product or (self.current_product_stock_info or {}).get(
            "product", {}
        )
        inp = self._produce_input
        produce_consume_ingredients = inp.get("produce_consume_ingredients", True)
        produce_servings = inp["produce_servings"]
        produce_amount = inp["produce_amount"]
        produce_location_id = inp["produce_location_id"]
        produce_price_total_str = inp.get("produce_price")

        # ── Calculate summary values ────────────────────────────────
        base_servings = int(recipe.get("base_servings", 1) or 1)
        eaten_servings = max(0, produce_servings - produce_amount)

        # Price per serving
        price_per_serving: float | None = None
        price_per_serving_str = "—"
        if produce_price_total_str and str(produce_price_total_str).strip():
            # TODO: Validate
            try:
                total = float(produce_price_total_str)
                price_per_serving = (
                    round(total / produce_servings, 2) if produce_servings > 0 else None
                )
                if price_per_serving is not None:
                    price_per_serving_str = f"{price_per_serving}"
            except (ValueError, ZeroDivisionError):
                # TODO: Surface errors to user instead of silently ignoring
                pass

        # Calories per serving: prefer product.calories, fall back to
        # fulfillment.calories / base_servings (fulfillment calories is the
        # total for 1× base recipe, NOT scaled by desired_servings).
        calories_per_serving_str = "—"
        product_calories = product.get("calories")
        fulfillment_calories = inp.get("fulfillment_calories")
        if product_calories and float(product_calories) > 1:
            try:
                calories_per_serving_str = f"{int(float(product_calories))} kcal"
            except (ValueError, TypeError):
                pass
        elif fulfillment_calories and base_servings > 0:
            try:
                cps = round(float(fulfillment_calories) / base_servings)
                calories_per_serving_str = f"~{cps} kcal"
            except (ValueError, TypeError, ZeroDivisionError):
                pass

        # Location name
        location_name = str(produce_location_id)
        for loc in self.masterdata.get("locations", []):
            if loc["id"] == produce_location_id:
                location_name = loc["name"]
                break

        enable_printing = self.scan_options.get(CONF_ENABLE_PRINTING, False)
        auto_print = self.scan_options.get(CONF_ENABLE_AUTO_PRINT, False)
        default_stock_label_type = product.get("default_stock_label_type")
        # if enable_printing and auto_print and default_stock_label_type in [1, 2]:
        #     if user_input is None:
        #         _LOGGER.info(
        #             "Integration has printing enabled but the Grocy product already has stock label type %s so it will auto-print. Therefore disabling printing in flow to omit duplicate prints.",
        #             default_stock_label_type,
        #         )
        #     enable_printing = False # Will omit the 'produce_print' field and avoid any custom invokes for printing

        if (
            user_input is None
            and enable_printing
            and auto_print
            and default_stock_label_type in [1, 2]
        ):
            _LOGGER.debug(
                "Integration has printing enabled and the Grocy product already has stock label type %s, so it will auto-print. Verify print output and check for duplicates.",
                default_stock_label_type,
            )

        # ── First render: show confirmation form ────────────────────
        if user_input is None:
            fields = self._form_builder.build_produce_confirm_fields(
                printing_enabled=enable_printing,
                auto_print=auto_print,
            )
            summary_lines = [
                f"## Produce: {recipe['name']}",
                "",
                "| Key | Value |",
                "|---|---|",
                f"| Consume ingredients | **{'Yes' if produce_consume_ingredients else 'No'}** |",
                f"| Servings cooked | **{produce_servings}** |",
                f"| Eaten now | **{eaten_servings}** |",
                f"| Containers → {location_name} | **{produce_amount}** |",
                f"| Price / serving | **{price_per_serving_str}** |",
                f"| Calories / serving | **{calories_per_serving_str}** |",
            ]

            return FormRequest(
                step_id=Step.SCAN_PRODUCE_CONFIRM,
                fields=fields,
                description_placeholders={
                    "name": product.get("name"),
                    "summary": "\n".join(summary_lines),
                },
                errors={},
            )

        # ── Process: consume ingredients, create stock, print ───────

        # 1. Consume recipe ingredients ourselves (instead of ConsumeRecipe)
        #    so we keep full control over stock creation.
        if not produce_consume_ingredients:
            _LOGGER.info(
                "Skipping ingredient consumption for recipe #%s (user opted out)",
                recipe["id"],
            )
        else:
            #    First, make sure desired_servings matches our produce_servings
            #    so that recipes_pos_resolved returns correctly scaled amounts.
            original_desired = recipe.get("desired_servings", base_servings)
            try:
                if produce_servings != original_desired:
                    await self._coordinator.update_recipe(
                        recipe["id"], {"desired_servings": produce_servings}
                    )

                positions = await self._api_grocy.get_recipes_pos_resolved(recipe["id"])
                consumed_count = 0
                for pos in positions:
                    # Skip positions that are check-only or have no stock
                    if pos.get("only_check_single_unit_in_stock") == 1:
                        continue
                    stock_amount = float(pos.get("stock_amount", 0) or 0)
                    if stock_amount <= 0:
                        continue

                    ingredient_amount = float(pos.get("recipe_amount", 0) or 0)
                    if ingredient_amount <= 0:
                        continue

                    # Don't consume more than what's available
                    amount_to_consume = min(ingredient_amount, stock_amount)

                    try:
                        await self._api_grocy.consume_stock_product(
                            pos["product_id"],
                            amount_to_consume,
                            exact_amount=True,
                            allow_subproduct_substitution=True,
                            recipe_id=recipe["id"],
                        )
                        consumed_count += 1
                    except Exception:
                        _LOGGER.warning(
                            "Failed to consume ingredient product #%s (%.2f of %.2f)",
                            pos.get("product_id"),
                            amount_to_consume,
                            ingredient_amount,
                            exc_info=True,
                        )

                _LOGGER.info(
                    "Consumed %d/%d ingredient positions for recipe #%s (%d servings)",
                    consumed_count,
                    len(positions),
                    recipe["id"],
                    produce_servings,
                )
            except Exception:
                _LOGGER.error(
                    "Failed to consume recipe #%s ingredients",
                    recipe["id"],
                    exc_info=True,
                )
            finally:
                # Restore original desired_servings
                if produce_servings != original_desired:
                    try:
                        await self._coordinator.update_recipe(
                            recipe["id"], {"desired_servings": original_desired}
                        )
                    except Exception:
                        _LOGGER.warning(
                            "Failed to restore desired_servings on recipe #%s",
                            recipe["id"],
                            exc_info=True,
                        )

        # 2. Create stock entries — single call with stock_label_type=2
        #    to get separate entries with x-prefixed stock_ids (no merging).
        if produce_amount > 0:
            # Leftovers that should be added to stock
            best_before_days = product.get("default_best_before_days")
            best_before_date: str | None = None
            if best_before_days is not None and int(best_before_days) > 0:
                d = dt.datetime.now() + dt.timedelta(days=int(best_before_days))
                best_before_date = d.strftime("%Y-%m-%d")

            should_print = enable_printing and user_input.get("produce_print", False)
            product_id = product["id"]
            stock_data: dict[str, Any] = {
                "amount": produce_amount,
                "transaction_type": "self-production",
                "location_id": produce_location_id,
                "note": recipe["name"],
            }
            if should_print:
                stock_data["stock_label_type"] = (
                    2  # Tell Grocy to print a "Label per unit"
                )
            if price_per_serving is not None:
                stock_data["price"] = price_per_serving
            if best_before_date:
                stock_data["best_before_date"] = best_before_date

            created_count = 0
            try:
                response = await self._coordinator.add_stock(product_id, stock_data)
                # Response is a list of stock_log entries, one per unit
                if isinstance(response, list):
                    created_count = len(response)
                else:
                    created_count = produce_amount
                _LOGGER.info(
                    "Created %d stock entries for product #%s: %s",
                    created_count,
                    product_id,
                    response,
                )
            except Exception:
                _LOGGER.error(
                    "Failed to create stock entries for product #%s",
                    product_id,
                    exc_info=True,
                )

        self.barcode_queue.pop(0)
        self.barcode_results.append(
            f"Produced {produce_servings} servings of recipe '{recipe['name']}'"
        )
        if produce_amount > 0:
            self.barcode_results.append(
                f"Stocked {produce_amount} servings of recipe '{recipe['name']}'"
            )
        return await self._step_scan_queue()

    async def _print_stock_entry_label(self, add_stock_response: Any) -> None:
        """Print a label for a newly created stock entry."""
        transaction = (
            add_stock_response[0]
            if add_stock_response
            and isinstance(add_stock_response, list)
            and len(add_stock_response) > 0
            else {}
        )
        stock_row_id = transaction.get("stock_row_id")
        if not stock_row_id and (stock_id := transaction.get("stock_id")):
            stock_entry = await self._api_grocy.get_stock_by_stock_id(stock_id)
            stock_row_id = stock_entry.get("id") if stock_entry else None
        if stock_row_id:
            _LOGGER.info("Sending print command for stock_entry: %s", stock_row_id)
            await self._api_grocy.print_label_for_stock_entry(stock_row_id)
        else:
            _LOGGER.warning("Could not resolve stock_row_id for printing")

    async def _ensure_product_stock_loaded(self) -> None:
        """Ensure product stock info is loaded."""
        if self.current_product:
            await self._state.ensure_stock_info_loaded()

    def _extract_scan_process_input(
        self, user_input: dict | None, product: dict
    ) -> tuple[str | None, int | None, str | None]:
        """Extract input values for scan process."""
        # TODO: Input default price from Recipe (cost of ingredients)
        price = (
            user_input.get("price")
            if user_input and self.scan_options.get(CONF_ENABLE_PRICES, True)
            else None
        )
        best_before_in_days = (
            user_input.get(
                "best_before_in_days", product.get("default_best_before_days")
            )
            if user_input
            else product.get("default_best_before_days")
        )
        shopping_location_id = (
            user_input.get("shopping_location_id")
            if user_input
            and self.scan_options.get(CONF_ENABLE_SHOPPING_LOCATIONS, True)
            else None
        )
        return price, best_before_in_days, shopping_location_id

    def _is_in_purchase_mode(self) -> bool:
        """Check if in purchase mode."""
        return self.barcode_scan_mode in [SCAN_MODE.PURCHASE] or (
            self.barcode_scan_mode == SCAN_MODE.SCAN_BBUDDY
            and self.current_bb_mode
            == self._api_bbuddy.convert_scan_mode_to_bbuddy_mode(SCAN_MODE.PURCHASE)
        )

    def _show_scan_process_form(
        self,
        product: dict,
        price: str | None,
        best_before_in_days: int | None,
        shopping_location_id: str | None,
        errors: dict,
    ) -> FormRequest | None:
        """Show form for purchase mode if needed."""
        if fields := self._form_builder.build_scan_process_fields(
            product,
            price,
            best_before_in_days,
            shopping_location_id,
            self.scan_options,
            self.current_recipe,
            self.current_product_stock_info,
            self.current_barcode,
        ):
            self._cached_process_fields = fields
            return FormRequest(
                step_id=Step.SCAN_PROCESS,
                fields=fields,
                errors=errors,
                description_placeholders={
                    "name": product.get("name"),
                },
            )
        return None

    async def _set_bbuddy_mode(self) -> None:
        """Set BarcodeBuddy mode."""
        bb_mode = self._api_bbuddy.convert_scan_mode_to_bbuddy_mode(
            self.barcode_scan_mode
        )
        if bb_mode >= 0:
            _LOGGER.info(
                "Setting BBuddy mode to: %s (%s)",
                bb_mode,
                self.barcode_scan_mode,
            )
            await self._api_bbuddy.set_mode(bb_mode)
            self.current_bb_mode = bb_mode

    async def _execute_scan_action(self, request: dict, in_purchase_mode: bool) -> dict:
        """Execute the scan action via Grocy or BarcodeBuddy."""
        _LOGGER.info("SCAN-REQ: %s", json.dumps(request))

        if in_purchase_mode and request.get("shopping_location_id"):
            # Workaround: call Grocy directly to persist store
            if days := request.get("bestBeforeInDays"):
                d = dt.datetime.now() + dt.timedelta(days=days)
                request["best_before_date"] = d.strftime("%Y-%m-%d")
                del request["bestBeforeInDays"]
            request["transaction_type"] = "purchase"
            request["amount"] = 1  # TODO: configurable amount
            # TODO: check barcode buddy current quantity context
            # TODO: introduce a field for manual input during scan (default to Barcode amount, then to 1). If not able to fetch override from BBuddy

            # TODO: If we want to print a stock entry label, uncomment rows below. Or set "default_stock_label_type" on the product
            # if self.scan_options.get(CONF_ENABLE_PRINTING) and self.scan_options.get(CONF_ENABLE_AUTO_PRINT):
            #     # Print the label for the newly added stock entry
            #     request["stock_label_type"] = 2  # Tell Grocy to print a "Label per unit"

            product_id = self.current_product_stock_info["product"]["id"]
            request.pop("barcode", None)  # Instead go by ´product_id´
            response = await self._coordinator.add_stock(product_id, request)
            # TODO: Validate response
        else:
            # Call Barcode Buddy scan
            # TODO: make Barcode Buddy obsolete? Instead do everything via Grocy API?. Gives more control, and cuts of middlehand. But looses the BBuddy UI and it's contextual settings.
            response = await self._api_bbuddy.post_scan(request)
            # TODO: handle responses with HTML-tags (warning/error messages)

        _LOGGER.info("SCAN-RESP: %s", response)
        return response

    async def _handle_scan_success(self, response: dict | None = None) -> StepResult:
        """Handle successful scan."""
        barcode = self.barcode_queue.pop(0)
        if response:
            # TODO: handle responses with HTML-tags (warning/error messages)
            self.barcode_results.append(str(response))

        # Mark queue item as resolved if processing from Handle Queue
        await self._mark_queue_item_resolved(
            barcode, str(response) if response else "OK"
        )

        # Re-run process method until queue is empty...
        return await self._step_scan_queue()

    def _handle_scan_error(self, be: BaseException, errors: dict) -> FormRequest:
        """Handle scan error."""
        _LOGGER.error("BB-Scan excpt: %s", be)
        errors["Exception"] = str(be)
        cached = self._cached_process_fields or []
        return FormRequest(
            step_id=Step.SCAN_PROCESS,
            fields=cached,
            errors=errors,
            description_placeholders={
                "name": self.current_product.get("name")
                if self.current_product
                else self.current_barcode,
                "recipe_info": (
                    f"## Produce: {self.current_recipe['name']}\n"
                    f"Base servings: {self.current_recipe['base_servings']}"
                )
                if self.current_recipe
                else "",
            },
        )

    async def _mark_queue_item_resolved(self, barcode: str, result_text: str) -> None:
        """Mark a queue item as resolved if processing from Handle Queue.

        Pops the first matching (barcode, item_id) tuple so that duplicate
        barcodes are resolved one at a time in queue order.
        """
        if self._queue_ref is None:
            return
        for i, (bc, item_id) in enumerate(self._queue_item_ids):
            if bc == barcode:
                self._queue_item_ids.pop(i)
                await self._queue_ref.async_mark_resolved(item_id, result_text)
                _LOGGER.info("Queue item %s resolved for barcode %s", item_id, barcode)
                return
