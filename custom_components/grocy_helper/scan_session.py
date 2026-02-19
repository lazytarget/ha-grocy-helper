"""Framework-agnostic barcode scanning workflow.

This module contains the core business logic for scanning barcodes,
looking up products, creating/matching products, and processing barcode
actions (purchase, consume, transfer, etc.).

It is completely independent of Home Assistant so it can be driven from
any UI layer - a traditional desktop/web application, a CLI tool, or a
pytest suite.

Usage example::

    session = ScanSession(
        api_grocy=grocy_api,
        api_bbuddy=bbuddy_api,
        masterdata=masterdata,
        lookup_barcode=my_lookup_fn,
        convert_quantity=my_convert_fn,
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
from typing import Any, Awaitable, Callable

from .barcodebuddyapi import BarcodeBuddyAPI
from .const import SCAN_MODE
from .grocyapi import GrocyAPI
from .grocytypes import (
    BarcodeLookup,
    ExtendedGrocyProductStockInfo,
    GrocyAddProductQuantityUnitConversion,
    GrocyMasterData,
    GrocyProduct,
    GrocyProductBarcode,
    GrocyQuantityUnitConversionResult,
    GrocyRecipe,
    GrocyStockEntry,
    OpenFoodFactsProduct,
)
from .scan_types import (
    AbortResult,
    CompletedResult,
    FieldType,
    FormField,
    FormRequest,
    NumberMode,
    SelectMode,
    SelectOption,
    Step,
    StepResult,
)
from .utils import try_parse_int

_LOGGER = logging.getLogger(__name__)

# Fields whose suggested values should NOT be converted to ``str``
# (they are numeric / boolean and the UI must receive them as-is).
_NUMERIC_FIELDS = frozenset(
    {
        "should_not_be_frozen",
        "calories_per_100",
        "default_best_before_days",
        "default_best_before_days_after_open",
        "default_best_before_days_after_freezing",
        "default_best_before_days_after_thawing",
    }
)
# TODO: This is a lazy hack. Improve!


class ScanSession:
    """Framework-agnostic barcode scanning workflow session.

    Manages the state and business logic for scanning barcodes, looking
    up products, creating/matching products, and processing barcode
    actions (purchase, consume, transfer …).

    Parameters
    ----------
    api_grocy:
        A ``GrocyAPI`` instance (or compatible) for Grocy REST calls.
    api_bbuddy:
        A ``BarcodeBuddyAPI`` instance (or compatible).
    masterdata:
        A ``GrocyMasterData`` dict with locations, products, etc.
        The session may **mutate** this dict (local cache updates).
    lookup_barcode:
        Async callable ``(code: str) -> BarcodeLookup``.
        Performs barcode lookups against external providers
        (OpenFoodFacts, ICA, …).
    convert_quantity:
        Async callable with signature
        ``(product_id, from_qu_id, to_qu_id, amount) -> result | None``.
    scan_options:
        Dict controlling which extra input fields appear during a
        "Purchase" scan.  Defaults to all enabled.
    """

    def __init__(
        self,
        api_grocy: GrocyAPI,
        api_bbuddy: BarcodeBuddyAPI,
        masterdata: GrocyMasterData,
        lookup_barcode: Callable[[str], Awaitable[BarcodeLookup]],
        convert_quantity: Callable[..., Awaitable[GrocyQuantityUnitConversionResult | None]],
        scan_options: dict[str, bool] | None = None,
        # CRUD operations (injected from coordinator)
        create_product: Callable[[dict], Awaitable[GrocyProduct]] | None = None,
        update_product: Callable[[int, dict], Awaitable[dict]] | None = None,
        create_barcode: Callable[[dict], Awaitable[dict]] | None = None,
        create_qu_conversion: Callable[[dict], Awaitable[dict]] | None = None,
        transfer_stock: Callable[[int, dict], Awaitable[dict]] | None = None,
        add_stock: Callable[[int, dict], Awaitable[dict]] | None = None,
        update_recipe: Callable[[int, dict], Awaitable[dict]] | None = None,
    ) -> None:
        self._api_grocy = api_grocy
        self._api_bbuddy = api_bbuddy
        self._masterdata = masterdata
        self._lookup_barcode = lookup_barcode
        self._convert_quantity = convert_quantity
        
        # CRUD operations - fallback to direct API calls if not provided
        self._create_product = create_product or self._default_create_product
        self._update_product = update_product or self._default_update_product
        self._create_barcode = create_barcode or self._default_create_barcode
        self._create_qu_conversion = create_qu_conversion or self._default_create_qu_conversion
        self._transfer_stock = transfer_stock or self._default_transfer_stock
        self._add_stock = add_stock or self._default_add_stock
        self._update_recipe = update_recipe or self._default_update_recipe

        self.scan_options: dict[str, bool] = scan_options or {
            "input_price": True,
            "input_bestBeforeInDays": True,
            "input_shoppingLocationId": True,
            "input_product_details_during_provision": True,
        }

        # ── workflow state ──────────────────────────────────────────
        self.current_bb_mode: int = -1
        self.barcode_scan_mode: str | None = None
        self.barcode_queue: list[str] = []
        self.barcode_results: list[str] = []

        self.current_barcode: str | None = None
        self.current_product_stock_info: ExtendedGrocyProductStockInfo | None = None
        self.current_product_openfoodfacts: OpenFoodFactsProduct | None = None
        self.current_product_ica: dict | None = None
        self.current_lookup: BarcodeLookup | None = None

        self.matching_products: list[GrocyProduct] = []
        self.current_stock_entries: list[GrocyStockEntry] = []

        self.current_product: GrocyProduct | None = None
        self.current_parent: GrocyProduct | None = None
        self.current_recipe: GrocyRecipe | None = None
        self.current_recipe_id: int | None = None

        # Cached form for error re-display
        self._cached_form: FormRequest | None = None
        # Cached process-step schema fields (for error re-display)
        self._cached_process_fields: list[FormField] | None = None

    # ── default CRUD operations (fallback to direct API calls) ──────

    async def _default_create_product(self, product_data: dict) -> GrocyProduct:
        """Default: create product via direct API call."""
        product_data["row_created_timestamp"] = dt.datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        return await self._api_grocy.add_product(product_data)

    async def _default_update_product(self, product_id: int, changes: dict) -> dict:
        """Default: update product via direct API call."""
        result = await self._api_grocy.update_product(product_id, changes)
        # Update local cache
        if self.current_product and self.current_product.get("id") == product_id:
            self.current_product.update(changes)
        return result

    async def _default_create_barcode(self, barcode_data: dict) -> dict:
        """Default: create barcode via direct API call."""
        barcode_data["row_created_timestamp"] = dt.datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        return await self._api_grocy.add_product_barcode(barcode_data)

    async def _default_create_qu_conversion(self, conversion_data: dict) -> dict:
        """Default: create QU conversion via direct API call."""
        conversion_data["row_created_timestamp"] = dt.datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        return await self._api_grocy.add_product_quantity_unit_conversion(
            conversion_data
        )

    async def _default_transfer_stock(
        self, product_id: int, transfer_data: dict
    ) -> dict:
        """Default: transfer stock via direct API call."""
        return await self._api_grocy.transfer_stock_entry(product_id, transfer_data)

    async def _default_add_stock(self, product_id: int, stock_data: dict) -> dict:
        """Default: add stock via direct API call."""
        return await self._api_grocy.add_stock_product(product_id, stock_data)

    async def _default_update_recipe(self, recipe_id: int, changes: dict) -> dict:
        """Default: update recipe via direct API call."""
        result = await self._api_grocy.update_recipe(recipe_id, changes)
        # Update local cache
        if self._masterdata and "recipes" in self._masterdata:
            for recipe in self._masterdata["recipes"]:
                if recipe["id"] == recipe_id:
                    recipe.update(changes)
                    break
        return result

    # ── public helpers ───────────────────────────────────────────────

    @property
    def masterdata(self) -> GrocyMasterData:
        return self._masterdata

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
            Step.SCAN_PROCESS: self._step_scan_process,
        }
        handler = handlers.get(step_id)
        if handler is None:
            return AbortResult(reason=f"Unknown step: {step_id}")
        return await handler(user_input)

    # =================================================================
    # Step handlers
    # =================================================================

    # ── scan_start ───────────────────────────────────────────────────

    async def _step_scan_start(
        self, user_input: dict[str, Any] | None
    ) -> StepResult:
        """Show the scan-start form or begin processing barcodes."""

        if user_input is None:
            bb_mode = await self._api_bbuddy.get_mode()
            if bb_mode is not None and bb_mode >= 0:
                self.current_bb_mode = bb_mode
            scan_mode_from_bbuddy = (
                self._api_bbuddy.convert_bbuddy_mode_to_scan_mode(self.current_bb_mode)
            )
            _LOGGER.info("BBuddy mode is: %s (%s)", bb_mode, scan_mode_from_bbuddy)
            return FormRequest(
                step_id=Step.SCAN_START,
                fields=self._build_scan_start_fields(scan_mode_from_bbuddy),
            )

        # ── user submitted the form ─────────────────────────────────
        barcodes_input = user_input["barcodes"]
        self.barcode_scan_mode = user_input.get("mode")
        _LOGGER.info("SCAN: %s", barcodes_input)
        _LOGGER.info("SCAN-mode: %s", self.barcode_scan_mode)

        self.barcode_queue = []
        self.barcode_results = []

        # Parse barcodes (split by whitespace)
        self.barcode_queue = [part for part in barcodes_input.split() if part]

        return await self._step_scan_queue()

    # ── scan_queue (internal - never shows its own form) ─────────────

    async def _step_scan_queue(self) -> StepResult:    # noqa: C901 - complex but faithful to original
        """Process the next barcode in the queue.

        This is an *internal* step - it never renders its own form.
        It chains to whichever visible step is appropriate.
        """

        self.current_product_stock_info = None
        current_barcode = (
            self.barcode_queue[0] if len(self.barcode_queue) > 0 else None
        )

        if not current_barcode:
            msg = (
                "\r\n".join(self.barcode_results)
                if self.barcode_results
                else "No barcodes were processed"
            )
            _LOGGER.info(
                "Nothing more in scan queue!: %s", len(self.barcode_results)
            )
            return CompletedResult(summary=msg, results=list(self.barcode_results))

        code = current_barcode.strip().strip(",").strip().lstrip("0")
        if self.current_barcode != code:
            # New barcode - clear contextual state
            self.current_product_stock_info = None
            self.current_product_openfoodfacts = None
            self.current_product_ica = None
            self.current_product = None
            self.current_parent = None
            self.current_recipe = None
            self.current_recipe_id = None
            self.current_lookup = None
            self.matching_products = []
        self.current_barcode = code

        if self.barcode_scan_mode == SCAN_MODE.SCAN_BBUDDY:
            bb_mode = await self._api_bbuddy.get_mode()
            if bb_mode is not None and bb_mode >= 0:
                _LOGGER.info(
                    "BBuddy mode is: %s (%s)", bb_mode, self.barcode_scan_mode
                )
                self.current_bb_mode = bb_mode
        else:
            self.current_bb_mode = None

        masterdata = self._masterdata

        if (
            self.barcode_scan_mode == SCAN_MODE.PROVISION
            or self.barcode_scan_mode not in [SCAN_MODE.INVENTORY, SCAN_MODE.QUANTITY]
        ):
            # ── recipe barcode ──────────────────────────────────────
            if "grcy:r:" in code:
                result = await self._handle_recipe_barcode(code, masterdata)
                if result is not None:
                    return result

            # ── normal barcode ──────────────────────────────────────
            if "BBUDDY-" not in code:
                # Lookup product in Grocy
                if not self.current_product and not self.current_recipe:
                    try:
                        self.current_product_stock_info = (
                            await self._api_grocy.get_stock_product_by_barcode(code)
                        )
                        self.current_product = (
                            self.current_product_stock_info or {}
                        ).get("product")
                        _LOGGER.info(
                            "GrocyProduct lookup: %s",
                            self.current_product_stock_info,
                        )
                    except BaseException as be:
                        _LOGGER.error("Get product excep: %s", be)
                        raise

                # ── transfer mode ──────────────────────────────────────────
                if (
                    self.current_product
                    and self.current_product.get("id")
                    and self.barcode_scan_mode == SCAN_MODE.TRANSFER
                ):
                    stock_entries = (
                        await self._api_grocy.get_stock_entries_by_product_id(
                            self.current_product["id"]
                        )
                    )
                    self.current_stock_entries = stock_entries
                    return await self._step_transfer_start(user_input=None)

                # Product doesn't exist → create / match
                if not self.current_product:
                    _LOGGER.info(
                        "New product, doing lookup against barcode providers: %s",
                        code,
                    )

                    if not self.current_recipe and (
                        not self.current_lookup
                        or self.current_lookup["barcode"] != code
                    ):
                        self.current_lookup = await self._lookup_barcode(code)
                        self.current_product_openfoodfacts = (
                            self.current_lookup.get("off")
                        )
                        self.current_product_ica = self.current_lookup.get("ica")

                    # Match against existing products by alias
                    for matching_product in filter(
                        lambda p: (
                            (
                                (self.current_lookup or {}).get("product_aliases")
                                and (
                                    p["name"].casefold()
                                    in map(
                                        str.casefold,
                                        self.current_lookup["product_aliases"],
                                    )
                                )
                            )
                            or (
                                self.current_recipe
                                and (
                                    p["name"]
                                    in [
                                        self.current_recipe["name"],
                                        f"Matlåda: {self.current_recipe['name']}",
                                    ]
                                )
                            )
                        ),
                        masterdata["products"],
                    ):
                        _LOGGER.info("Match: %s", matching_product)
                        self.matching_products.append(matching_product)

                    return await self._step_match_to_product(user_input=None)

        # ── provision mode ──────────────────────────────────────────
        if self.barcode_scan_mode == SCAN_MODE.PROVISION:
            self.barcode_queue.pop(0)
            _LOGGER.info("Provisioned: %s", self.current_product)
            self.barcode_results.append(
                f"{code} maps to {self.current_product['name']}"
            )
            return await self._step_scan_queue()

        # ── inventory mode ──────────────────────────────────────────
        if self.barcode_scan_mode == SCAN_MODE.INVENTORY:
            if not self.current_product_stock_info:
                self.current_product_stock_info = (
                    await self._api_grocy.get_stock_product_by_barcode(code)
                )
                self.current_product = (
                    self.current_product_stock_info or {}
                ).get("product")

        # Proceed with BarcodeBuddy processing
        return await self._step_scan_process(user_input=None)

    # ── recipe barcode helper ────────────────────────────────────────

    async def _handle_recipe_barcode(
        self, code: str, masterdata: GrocyMasterData
    ) -> StepResult | None:
        """Handle a ``grcy:r:<id>`` barcode.  Returns *None* to continue."""

        (r, i) = try_parse_int(code.replace("grcy:r:", ""))
        if r and i > 0:
            self.current_recipe_id = i
            self.current_recipe = next(
                (
                    recipe
                    for recipe in masterdata["recipes"]
                    if recipe["id"] == self.current_recipe_id
                ),
                None,
            )
            if not self.current_recipe:
                return AbortResult(
                    reason=f"Recipe with id '{i}' was not found"
                )

            _LOGGER.debug("Found recipe: %s", self.current_recipe)
            if product_id := self.current_recipe["product_id"]:
                self.current_product_stock_info = (
                    await self._api_grocy.get_stock_product_by_id(product_id)
                )
                self.current_product = (
                    self.current_product_stock_info or {}
                ).get("product")
                _LOGGER.info(
                    "Recipe '%s' produces product: %s",
                    self.current_recipe["id"],
                    self.current_product,
                )
        else:
            return AbortResult(
                reason=f"Could not parse recipe barcode: {code}"
            )
        return None  # continue queue processing

    # ── match_to_product ─────────────────────────────────────────────

    async def _step_match_to_product(
        self, user_input: dict[str, Any] | None
    ) -> StepResult:
        """Let the user match the barcode to an existing or new product."""

        # First render - show form
        if user_input is None:
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

        # Existing product (or parent needs creation)
        return await self._step_add_product_parent(user_input=None)

    # ── add_product ──────────────────────────────────────────────────

    async def _step_add_product(
        self, user_input: dict[str, Any] | None
    ) -> StepResult:
        """Create a new product in Grocy."""

        if self.current_product and self.current_product.get("id"):
            return AbortResult(
                reason="Product already exists"
            )

        new_product = (self.current_product or {}).copy()
        
        # First render - show form
        if user_input is None:
            return self._show_add_product_form(new_product, {})

        # ── process submitted form ──────────────────────────────────
        
        # User selected existing product instead of creating new
        if user_input.get("product_id") and user_input["product_id"] != "-1":
            product_id = int(user_input["product_id"])
            self.current_product_stock_info = (
                await self._api_grocy.get_stock_product_by_id(product_id)
            )
            return await self._step_add_product_barcode(None)

        # Build new product from input
        new_product = self._build_product_from_input(user_input, new_product)

        # Validate location
        errors = self._validate_product_location(new_product)
        if errors:
            return self._show_add_product_form(new_product, errors)

        # Create product
        _LOGGER.info("Creating product: %s", new_product)
        product = await self._create_product(new_product)
        _LOGGER.info("Created product: %s", product)
        
        # Load full product info
        self.current_product_stock_info = (
            await self._api_grocy.get_stock_product_by_id(product["id"])
        )
        self.current_product = (
            self.current_product_stock_info or {}).get("product") or product

        return await self._step_add_product_barcode(None)

    # ── add_product_parent ───────────────────────────────────────────

    async def _step_add_product_parent(  # noqa: C901
        self, user_input: dict[str, Any] | None
    ) -> StepResult:
        """Optionally create a parent product for the current product."""

        errors: dict[str, str] = {}
        self._cached_form = None
        masterdata = self._masterdata
        _LOGGER.info("form 'add_product_parent' user_input: %s", user_input)

        _LOGGER.info("Create parent: %s", self.current_parent)
        new_product: dict = (self.current_parent or {}).copy()
        creating_parent = True

        if self.current_parent is None:
            _LOGGER.debug(
                "Product will not be linked to a parent, continue to next step..."
            )
            return await self._step_scan_process(user_input=None)
        elif self.current_parent.get("id"):
            _LOGGER.debug(
                "Product parent already exists, continue to next step..."
            )
            return await self._step_scan_process(user_input=None)

        code = self.current_barcode
        first_render = user_input is None
        if user_input is None:
            user_input = {}

        # Keys for parent product form
        parent_keys = ["name", "qu_id_stock", "qu_id_price"]
        if not creating_parent:
            parent_keys.extend([
                "location_id", "should_not_be_frozen",
                "default_best_before_days", "default_best_before_days_after_open",
                "qu_id_purchase", "qu_id_consume",
            ])

        # Merge values - copy from child product when creating parent
        suggested: dict[str, Any] = {}
        for k in parent_keys:
            val = user_input.get(k, new_product.get(k))
            if not val and creating_parent and self.current_product:
                if k not in ("id", "name", "description"):
                    _LOGGER.warning(
                        "COPY prop to parent: %s=%s", k, self.current_product.get(k)
                    )
                    val = self.current_product.get(k)
            if k not in _NUMERIC_FIELDS:
                val = str(val) if val is not None else None
            suggested[k] = val

        # Adjust QU for parent on first render
        if first_render:
            piece_qu = masterdata["known_qu"].get("Piece")
            pack_qu = masterdata["known_qu"].get("Pack")
            piece_id = (
                piece_qu.get("id")
                if isinstance(piece_qu, dict)
                else getattr(piece_qu, "id", None)
            )
            pack_id = (
                pack_qu.get("id")
                if isinstance(pack_qu, dict)
                else getattr(pack_qu, "id", None)
            )
            if (
                int(suggested.get("qu_id_stock") or -99) in [piece_id, pack_id]
            ) and (
                int(suggested.get("qu_id_price") or -99) not in [piece_id, pack_id]
            ):
                _LOGGER.warning(
                    "Copying qu_id_price into qu_id_stock: %s. Known: %s",
                    suggested,
                    masterdata["known_qu"],
                )
                suggested["qu_id_stock"] = suggested["qu_id_price"]

        if first_render:
            fields = self._build_create_product_fields(
                suggested, creating_parent=creating_parent
            )
            aliases = self._get_aliases()
            plc = {
                "name": new_product.get("name"),
                "barcode": code,
                "product_aliases": "\n".join(
                    [f"- {a.strip()}" for a in aliases if a]
                ),
                "lookup_output": (self.current_lookup or {}).get("lookup_output"),
            }
            self._cached_form = FormRequest(
                step_id=Step.SCAN_ADD_PRODUCT_PARENT,
                fields=fields,
                description_placeholders=plc,
                errors=errors,
            )
            return self._cached_form

        # ── process submitted form ──────────────────────────────────
        if user_input.get("product_id") and user_input["product_id"] != "-1":
            self.current_parent = await self._api_grocy.get_product_by_id(
                int(user_input["product_id"])
            )
        else:
            new_product["name"] = user_input["name"]
            new_product["location_id"] = user_input.get(
                "location_id",
                self.current_product["location_id"] if self.current_product else None,
            )
            new_product["should_not_be_frozen"] = (
                1
                if user_input.get(
                    "should_not_be_frozen",
                    (self.current_product or {}).get("should_not_be_frozen", False),
                )
                else 0
            )

            loc = next(
                (
                    loc
                    for loc in masterdata["locations"]
                    if str(loc["id"]) == str(new_product["location_id"])
                ),
                None,
            )
            if not loc:
                errors["location_id"] = "invalid_location"
            elif new_product["should_not_be_frozen"] == 1 and loc["is_freezer"] == 1:
                errors["location_id"] = "location_is_freezer"

            if val := user_input.get("default_best_before_days"):
                new_product["default_best_before_days"] = int(val)
            if val := user_input.get("default_best_before_days_after_open"):
                new_product["default_best_before_days_after_open"] = int(val)

            new_product["qu_id_stock"] = user_input.get(
                "qu_id_stock", user_input.get("qu_id")
            )
            new_product["qu_id_purchase"] = user_input.get(
                "qu_id_purchase", new_product.get("qu_id_stock")
            )
            new_product["qu_id_consume"] = user_input.get(
                "qu_id_consume", new_product.get("qu_id_stock")
            )
            new_product["qu_id_price"] = user_input.get(
                "qu_id_price", user_input.get("qu_id")
            )
            new_product["row_created_timestamp"] = dt.datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            )

            if creating_parent:
                new_product["description"] = user_input.get(
                    "description", new_product.get("description")
                )
                new_product["no_own_stock"] = 1
                new_product["hide_on_stock_overview"] = 1
                new_product["disable_open"] = 1
                new_product["cumulate_min_stock_amount_of_sub_products"] = 1
                new_product["parent_product_id"] = None
            else:
                new_product["description"] = user_input.get(
                    "description", new_product.get("description")
                )
                new_product["parent_product_id"] = user_input.get(
                    "parent_product_id", new_product.get("parent_product_id")
                )

            _LOGGER.info("user_input: %s", user_input)
            _LOGGER.info("new_product: %s", new_product)
            if errors:
                _LOGGER.warning("Input errors: %s", errors)
                fields = self._build_create_product_fields(
                    suggested, creating_parent=creating_parent
                )
                aliases = self._get_aliases()
                plc = {
                    "name": new_product.get("name"),
                    "barcode": code,
                    "product_aliases": "\n".join(
                        [f"- {a.strip()}" for a in aliases if a]
                    ),
                    "lookup_output": (self.current_lookup or {}).get(
                        "lookup_output"
                    ),
                }
                return FormRequest(
                    step_id=Step.SCAN_ADD_PRODUCT_PARENT,
                    fields=fields,
                    description_placeholders=plc,
                    errors=errors,
                )

            product = await self._api_grocy.add_product(new_product)
            _LOGGER.info("created prod: %s", product)
            self.current_parent = product

            if self.current_product and not self.current_product.get(
                "parent_product_id"
            ):
                product_updates = {
                    "parent_product_id": self.current_parent["id"],
                }
                _LOGGER.info(
                    "Will update product: #%s %s",
                    self.current_product["id"],
                    product_updates,
                )
                await self._api_grocy.update_product(
                    self.current_product["id"], product_updates
                )
                self.current_product.update(product_updates)

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

        new_product: GrocyProduct = (self.current_product_stock_info or {}).get(
            "product"
        )

        if user_input is None:
            suggested: dict[str, Any] = {}
            suggested["note"] = new_product["name"] if new_product else ""
            fields = self._build_create_barcode_fields(suggested)
            aliases = self._get_aliases()
            plc = {
                "name": new_product.get("name") if new_product else None,
                "barcode": code,
                "product_aliases": "\n".join(
                    [f"- {a.strip()}" for a in aliases if a]
                ),
                "lookup_output": (self.current_lookup or {}).get("lookup_output"),
            }
            self._cached_form = FormRequest(
                step_id=Step.SCAN_ADD_PRODUCT_BARCODE,
                fields=fields,
                description_placeholders=plc,
                errors=errors,
            )
            return self._cached_form

        # ── process ─────────────────────────────────────────────────
        br: GrocyProductBarcode = {
            "barcode": code,
            "note": user_input["note"],
            "product_id": new_product["id"],
            "qu_id": user_input.get("qu_id"),
            "shopping_location_id": user_input.get("shopping_location_id"),
            "amount": user_input.get("amount"),
            "row_created_timestamp": dt.datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
        }
        pcode = await self._api_grocy.add_product_barcode(br)
        _LOGGER.info("created prod_barcode: %s", pcode)

        if self.scan_options.get("input_product_details_during_provision"):
            return await self._step_update_product_details(user_input=None)

        return await self._step_add_product_parent(user_input=None)

    # ── update_product_details ───────────────────────────────────────

    async def _step_update_product_details(  # noqa: C901
        self, user_input: dict[str, Any] | None
    ) -> StepResult:
        """Update product details (quantity, calories, shelf life)."""

        errors: dict[str, str] = {}
        _LOGGER.info("form update-product: %s", user_input)
        masterdata = self._masterdata
        product_stock_info = self.current_product_stock_info
        product = product_stock_info["product"]

        show_form = user_input is None
        if user_input is None:
            user_input = {}

        # Append defaults from current product
        for key in (
            "should_not_be_frozen",
            "default_consume_location_id",
            "default_best_before_days_after_freezing",
            "default_best_before_days_after_thawing",
        ):
            val = user_input.get(key, (self.current_product or {}).get(key))
            if key not in _NUMERIC_FIELDS:
                val = str(val) if val is not None else None
            user_input[key] = val

        _LOGGER.info("Updated input: %s", user_input)

        product_quantity = None
        product_quantity_unit: int | None = None
        product_quantity_unit_as_liquid = False
        product_quantity_unit_as_weight = False

        if self.current_product_openfoodfacts is not None:
            product_quantity = user_input.get(
                "product_quantity",
                self.current_product_openfoodfacts.get("product_quantity"),
            )
            unit = self.current_product_openfoodfacts.get("product_quantity_unit")
            if unit:
                for qq in filter(
                    lambda qu: qu.get("name") == unit,
                    masterdata["quantity_units"],
                ):
                    product_quantity_unit = qq["id"]
                    _LOGGER.warning("Unit: %s, QQ: %s", unit, qq)
                    product_quantity_unit_as_liquid = qq["name"] in [
                        "ml", "cl", "dl", "l", "L",
                    ]
                    product_quantity_unit_as_weight = qq["name"] in [
                        "g", "hg", "kg",
                    ]

        # TODO: fill in info from ICA

        kcal = user_input.get("calories_per_100") or (
            self.current_product_openfoodfacts or {}
        ).get("nutriments", {}).get("energy_kcal_100g")
        user_input["calories_per_100"] = kcal
        if kcal:
            kcal = float(kcal)

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
                masterdata["quantity_units"],
            ):
                _LOGGER.warning("Chosen unit: %s", qq)
                product_quantity_unit_as_liquid = qq["name"] in [
                    "ml", "cl", "dl", "l", "L",
                ]
                product_quantity_unit_as_weight = qq["name"] in ["g", "hg", "kg"]
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

        if show_form:
            qu_id_product_val = user_input.get("qu_id_product", qu_id_product)
            if qu_id_product_val:
                user_input["qu_id_product"] = str(qu_id_product_val)
            user_input["product_quantity"] = user_input.get(
                "product_quantity", product_quantity
            )
            user_input["calories_per_100"] = user_input.get(
                "calories_per_100", kcal
            )

            fields = self._build_update_product_details_fields(
                user_input, product
            )
            aliases = self._get_aliases()
            plc = {
                "name": product.get("name"),
                "barcode": self.current_barcode,
                "product_aliases": "\n".join(
                    [f"- {a.strip()}" for a in aliases if a]
                ),
                "lookup_output": (self.current_lookup or {}).get("lookup_output"),
            }
            self._cached_form = FormRequest(
                step_id=Step.SCAN_UPDATE_PRODUCT_DETAILS,
                fields=fields,
                description_placeholders=plc,
                errors=errors,
            )
            return self._cached_form

        # ── process submitted values ────────────────────────────────
        _LOGGER.info(
            "About to add conv: %s %s %s",
            product["qu_id_stock"],
            qu_id_product,
            product_quantity,
        )
        product_updates: dict = {}

        if not skip_add_qu_conversions and qu_id_product and product_quantity:
            conv: GrocyAddProductQuantityUnitConversion = {
                "from_qu_id": product["qu_id_stock"],
                "to_qu_id": int(qu_id_product),
                "product_id": product["id"],
                "row_created_timestamp": dt.datetime.now().strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
                "factor": float(product_quantity),
            }
            await self._api_grocy.add_product_quantity_unit_conversion(conv)

            if product_quantity_unit_as_liquid:
                product_updates["qu_id_price"] = (
                    masterdata["known_qu"].get("L", {}).get("id")
                )
            elif product_quantity_unit_as_weight:
                product_updates["qu_id_price"] = (
                    masterdata["known_qu"].get("kg", {}).get("id")
                )
            else:
                _LOGGER.warning(
                    "Unknown quantity unit type: %s", product_quantity_unit
                )

        if val := user_input.get("default_consume_location_id"):
            product_updates["default_consume_location_id"] = val
        if val := user_input.get("default_best_before_days_after_freezing"):
            product_updates["default_best_before_days_after_freezing"] = int(val)
        if val := user_input.get("default_best_before_days_after_thawing"):
            product_updates["default_best_before_days_after_thawing"] = int(val)

        gram_unit = masterdata["known_qu"].get("g")
        if product_quantity_unit_as_liquid:
            gram_unit = masterdata["known_qu"].get("ml")

        if kcal and gram_unit:
            kcal_per_gram = float(kcal) / 100
            c: GrocyQuantityUnitConversionResult = (
                await self._convert_quantity(
                    product["id"],
                    int(product["qu_id_stock"]),
                    gram_unit["id"],
                    1,
                )
            )
            # TODO: handle c is None
            _LOGGER.warning(
                "Converted: %s %s -> %s %s",
                c["from_amount"],
                c["from_qu_name"],
                c["to_amount"],
                c["to_qu_name"],
            )
            grams_per_pack = c["to_amount"]
            kcal_per_pack = kcal_per_gram * grams_per_pack
            product_updates["calories"] = kcal_per_pack

        if product_updates:
            _LOGGER.info(
                "Will update product: #%s %s", product["id"], product_updates
            )
            await self._api_grocy.update_product(product["id"], product_updates)
            if self.current_product:
                self.current_product.update(product_updates)

        return await self._step_add_product_parent(user_input=None)

    # ── transfer_start ───────────────────────────────────────────────

    async def _step_transfer_start(
        self, user_input: dict[str, Any] | None
    ) -> StepResult:
        """Choose which stock entry to transfer."""

        errors: dict[str, str] = {}
        _LOGGER.info("transfer-start: %s", user_input)

        if not self.current_product_stock_info:
            return AbortResult(
                reason="No product info found during transfer!"
            )
        if len(self.current_stock_entries) < 1:
            return AbortResult(reason="No stock entries to transfer")

        if user_input is None and len(self.current_stock_entries) > 1:
            _LOGGER.warning(
                "Existing stock entries: %s", self.current_stock_entries
            )
            fields = self._build_choose_stock_entry_fields()
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
            self.current_stock_entries = [stock_entry]
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
            return AbortResult(
                reason="No product info found during transfer!"
            )
        if len(self.current_stock_entries) != 1:
            return AbortResult(
                reason="Should only have one chosen stock entry to transfer"
            )

        if user_input is None:
            fields = self._build_transfer_input_fields()
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
        result = await self._api_grocy.transfer_stock_entry(
            product["id"], data=data
        )
        _LOGGER.info("Completed transfer: %s", result)

        self.barcode_queue.pop(0)
        self.barcode_results.append(
            f"{product['name']} transferred to loc #{location_to_id}"
        )
        return await self._step_scan_queue()

    # ── scan_process ─────────────────────────────────────────────────

    async def _step_scan_process(  # noqa: C901
        self, user_input: dict[str, Any] | None
    ) -> StepResult:
        """Final processing step: call BBuddy / Grocy to execute the action."""

        errors: dict[str, str] = {}
        code = self.current_barcode

        # Ensure stock info is loaded
        if self.current_product and not self.current_product_stock_info:
            _LOGGER.warning("Product stock was not loaded, loading it now!")
            self.current_product_stock_info = (
                await self._api_grocy.get_stock_product_by_id(
                    self.current_product["id"]
                )
            )
            self.current_product = (
                self.current_product_stock_info or {}
            ).get("product")

        product = (
            self.current_product
            or (self.current_product_stock_info or {}).get("product", {})
        )

        price = user_input.get("price") if user_input else None
        bestBeforeInDays = (
            user_input.get(
                "bestBeforeInDays", product.get("default_best_before_days")
            )
            if user_input
            else product.get("default_best_before_days")
        )
        shopping_location_id = (
            user_input.get("shopping_location_id") if user_input else None
        )

        in_purchase_mode = self.barcode_scan_mode in [SCAN_MODE.PURCHASE] or (
            self.barcode_scan_mode == SCAN_MODE.SCAN_BBUDDY
            and self.current_bb_mode
            == self._api_bbuddy.convert_scan_mode_to_bbuddy_mode(
                SCAN_MODE.PURCHASE
            )
        )

        # ── build purchase-mode form if needed ──────────────────────
        if user_input is None and in_purchase_mode:
            fields = self._build_scan_process_fields(
                product, price, bestBeforeInDays, shopping_location_id
            )
            if fields:
                self._cached_process_fields = fields
                return FormRequest(
                    step_id=Step.SCAN_PROCESS,
                    fields=fields,
                    errors=errors,
                )

        # ── execute the action ──────────────────────────────────────
        request: dict[str, Any] = {"barcode": str(code)}

        if in_purchase_mode:
            if price is not None and len(str(price)) > 0:
                request["price"] = float(price)
            if bestBeforeInDays is not None and len(str(bestBeforeInDays)) > 0:
                request["bestBeforeInDays"] = int(bestBeforeInDays)
            if shopping_location_id is not None and int(shopping_location_id) > 0:
                request["shopping_location_id"] = int(shopping_location_id)

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

        try:
            _LOGGER.info("SCAN-REQ: %s", json.dumps(request))
            if in_purchase_mode and request.get("shopping_location_id"):
                # Workaround: call Grocy directly to persist store
                if days := request.get("bestBeforeInDays"):
                    d = dt.datetime.now() + dt.timedelta(days=days)
                    request["best_before_date"] = d.strftime("%Y-%m-%d")
                    del request["bestBeforeInDays"]
                request["transaction_type"] = "purchase"
                request["amount"] = 1  # TODO: configurable amount
                product_id = self.current_product_stock_info["product"]["id"]
                request.pop("barcode", None)
                response = await self._api_grocy.add_stock_product(
                    product_id, request
                )
            else:
                response = await self._api_bbuddy.post_scan(request)
            _LOGGER.info("SCAN-RESP: %s", response)

            self.barcode_queue.pop(0)
            self.barcode_results.append(str(response))

            return await self._step_scan_queue()

        except BaseException as be:
            _LOGGER.error("BB-Scan excpt: %s", be)
            errors["Exception"] = str(be)

            cached = self._cached_process_fields or []
            return FormRequest(
                step_id=Step.SCAN_PROCESS,
                fields=cached,
                errors=errors,
            )

    # =================================================================
    # Form-field builders
    # =================================================================

    def _build_scan_start_fields(
        self, scan_mode: SCAN_MODE | None
    ) -> list[FormField]:
        """Build fields for the scan-start form."""

        bbuddy_mode_str = scan_mode.name if scan_mode is not None else "Unknown"

        return [
            FormField(
                key="mode",
                field_type=FieldType.SELECT,
                required=False,
                suggested_value=SCAN_MODE.SCAN_BBUDDY,
                select_mode=SelectMode.LIST,
                options=[
                    SelectOption(
                        value=SCAN_MODE.SCAN_BBUDDY,
                        label=f"Barcode Buddy ({bbuddy_mode_str})",
                    ),
                    SelectOption(value=SCAN_MODE.CONSUME, label="Consume"),
                    SelectOption(
                        value=SCAN_MODE.CONSUME_SPOILED,
                        label="Consume (Spoiled)",
                    ),
                    SelectOption(
                        value=SCAN_MODE.CONSUME_ALL,
                        label="Consume (All)",
                    ),
                    SelectOption(
                        value=SCAN_MODE.PURCHASE,
                        label="Purchase / Produce",
                    ),
                    SelectOption(value=SCAN_MODE.TRANSFER, label="Transfer"),
                    SelectOption(value=SCAN_MODE.OPEN, label="Open"),
                    SelectOption(value=SCAN_MODE.INVENTORY, label="Inventory"),
                    SelectOption(
                        value=SCAN_MODE.ADD_TO_SHOPPING_LIST,
                        label="Add to Shopping list",
                    ),
                    SelectOption(
                        value=SCAN_MODE.PROVISION,
                        label="Provision barcode",
                    ),
                ],
            ),
            FormField(
                key="barcodes",
                field_type=FieldType.TEXT,
                required=True,
                multiline=True,
                suggested_value="4011800420413",  # DEV default
            ),
        ]

    def _build_match_product_fields(
        self,
        suggested_products: list[GrocyProduct],
        aliases: list[str],
        allow_parent: bool,
        suggested_values: dict[str, str] | None = None,
    ) -> list[FormField]:
        """Build fields for the match-to-product form."""

        masterdata = self._masterdata
        suggested_values = suggested_values or {}
        lookup = self.current_lookup

        child_products = [
            p for p in masterdata["products"] if p["parent_product_id"]
        ]
        parent_product_ids = [
            p["parent_product_id"] for p in child_products
        ]
        parent_products = [
            p for p in masterdata["products"] if p["id"] in parent_product_ids
        ]

        suggested_product_ids = [p["id"] for p in suggested_products]
        non_suggested = [
            p
            for p in masterdata["products"]
            if p["id"] not in suggested_product_ids
            and p["id"] not in parent_product_ids
        ]
        non_suggested.sort(key=lambda p: p["name"])

        product_options = list(suggested_products) + non_suggested
        prods = [
            SelectOption(value=str(p["id"]), label=p["name"])
            for p in product_options
            if p["active"] == 1
        ]

        selected_product_id = ""
        resolved_aliases = aliases or (lookup or {}).get("product_aliases", [])
        if len(suggested_products) == 0 and len(resolved_aliases) > 0:
            selected_product_id = resolved_aliases[0]
        elif len(suggested_products) > 0:
            prods.insert(
                len(suggested_products),
                SelectOption(
                    value="-1",
                    label=f"\t[{len(suggested_products)} SUGGESTIONS ABOVE]",
                ),
            )
            if len(suggested_products) == 1:
                selected_product_id = str(
                    suggested_values.get(
                        "product_id", suggested_products[0]["id"]
                    )
                )
            else:
                selected_product_id = "-1"

        fields: list[FormField] = [
            FormField(
                key="product_id",
                field_type=FieldType.SELECT,
                required=True,
                suggested_value=selected_product_id,
                options=prods,
                custom_value=True,
                select_mode=SelectMode.DROPDOWN,
            ),
        ]

        if allow_parent:
            fields.append(
                FormField(
                    key="parent_product",
                    field_type=FieldType.SELECT,
                    required=False,
                    suggested_value=suggested_values.get("parent_product"),
                    options=[
                        SelectOption(value=str(p["id"]), label=p["name"])
                        for p in parent_products
                        if p["active"] == 1
                    ],
                    custom_value=True,
                    select_mode=SelectMode.DROPDOWN,
                ),
            )

        return fields

    def _build_create_product_fields(
        self,
        suggested: dict[str, Any],
        creating_parent: bool = False,
    ) -> list[FormField]:
        """Build fields for the create-product form."""

        loc_options = self._location_options()
        qu_options = self._qu_options()

        fields: list[FormField] = [
            FormField(
                key="name",
                field_type=FieldType.TEXT,
                required=True,
                suggested_value=suggested.get("name"),
            ),
        ]

        if not creating_parent:
            fields.extend([
                FormField(
                    key="location_id",
                    field_type=FieldType.SELECT,
                    required=True,
                    suggested_value=self._str_val(suggested.get("location_id")),
                    options=loc_options,
                    select_mode=SelectMode.DROPDOWN,
                ),
                FormField(
                    key="should_not_be_frozen",
                    field_type=FieldType.BOOLEAN,
                    required=True,
                    default=suggested.get("should_not_be_frozen", False),
                ),
                FormField(
                    key="default_best_before_days",
                    field_type=FieldType.NUMBER,
                    required=False,
                    suggested_value=suggested.get("default_best_before_days"),
                    step=1,
                ),
                FormField(
                    key="default_best_before_days_after_open",
                    field_type=FieldType.NUMBER,
                    required=False,
                    suggested_value=suggested.get(
                        "default_best_before_days_after_open"
                    ),
                    step=1,
                ),
            ])

        fields.append(
            FormField(
                key="qu_id_stock",
                field_type=FieldType.SELECT,
                required=True,
                suggested_value=self._str_val(
                    suggested.get("qu_id_stock", suggested.get("qu_id"))
                ),
                options=qu_options,
                select_mode=SelectMode.DROPDOWN,
            ),
        )

        if not creating_parent:
            fields.extend([
                FormField(
                    key="qu_id_purchase",
                    field_type=FieldType.SELECT,
                    required=True,
                    suggested_value=self._str_val(
                        suggested.get(
                            "qu_id_purchase", suggested.get("qu_id")
                        )
                    ),
                    options=qu_options,
                    select_mode=SelectMode.DROPDOWN,
                ),
                FormField(
                    key="qu_id_consume",
                    field_type=FieldType.SELECT,
                    required=True,
                    suggested_value=self._str_val(
                        suggested.get(
                            "qu_id_consume", suggested.get("qu_id")
                        )
                    ),
                    options=qu_options,
                    select_mode=SelectMode.DROPDOWN,
                ),
            ])

        fields.append(
            FormField(
                key="qu_id_price",
                field_type=FieldType.SELECT,
                required=True,
                suggested_value=self._str_val(
                    suggested.get("qu_id_price", suggested.get("qu_id"))
                ),
                options=qu_options,
                select_mode=SelectMode.DROPDOWN,
            ),
        )

        return fields

    def _build_create_barcode_fields(
        self, suggested: dict[str, Any]
    ) -> list[FormField]:
        """Build fields for the create-barcode form."""

        masterdata = self._masterdata
        shopping_locations = sorted(
            masterdata["shopping_locations"], key=lambda loc: loc["name"]
        )
        shop_options = [
            SelectOption(value=str(s["id"]), label=s["name"])
            for s in shopping_locations
        ]
        qu_options = self._qu_options()

        return [
            FormField(
                key="note",
                field_type=FieldType.TEXT,
                required=False,
                suggested_value=suggested.get("note"),
            ),
            FormField(
                key="shopping_location_id",
                field_type=FieldType.SELECT,
                required=False,
                options=shop_options,
                select_mode=SelectMode.DROPDOWN,
            ),
            FormField(
                key="qu_id",
                field_type=FieldType.SELECT,
                required=False,
                suggested_value=self._str_val(
                    suggested.get("qu_id", suggested.get("qu_id_purchase"))
                ),
                options=qu_options,
                select_mode=SelectMode.DROPDOWN,
            ),
            FormField(
                key="amount",
                field_type=FieldType.NUMBER,
                required=False,
                suggested_value=suggested.get("amount"),
            ),
        ]

    def _build_update_product_details_fields(
        self,
        suggested: dict[str, Any],
        product: GrocyProduct,
    ) -> list[FormField]:
        """Build fields for the update-product-details form."""

        masterdata = self._masterdata
        locations = [
            loc
            for loc in masterdata["locations"]
            if product["should_not_be_frozen"] == 0 or loc["is_freezer"] == 0
        ]
        locations.sort(key=lambda loc: loc["name"])
        loc_options = [
            SelectOption(value=str(loc["id"]), label=loc["name"])
            for loc in locations
        ]

        qu_options = self._qu_options(include_blank=True)

        fields: list[FormField] = [
            FormField(
                key="default_consume_location_id",
                field_type=FieldType.SELECT,
                required=False,
                suggested_value=self._str_val(
                    suggested.get("default_consume_location_id")
                ),
                options=loc_options,
                select_mode=SelectMode.DROPDOWN,
            ),
            FormField(
                key="product_quantity",
                field_type=FieldType.NUMBER,
                required=False,
                suggested_value=suggested.get("product_quantity"),
                step=1,
            ),
            FormField(
                key="qu_id_product",
                field_type=FieldType.SELECT,
                required=False,
                suggested_value=self._str_val(
                    suggested.get("qu_id_product")
                ),
                description="What quantity unit does the product package have?",
                options=qu_options,
                select_mode=SelectMode.DROPDOWN,
            ),
            FormField(
                key="calories_per_100",
                field_type=FieldType.NUMBER,
                required=False,
                suggested_value=suggested.get("calories_per_100"),
                step=1,
            ),
        ]

        if not product.get("should_not_be_frozen", 0):
            fields.extend([
                FormField(
                    key="default_best_before_days_after_freezing",
                    field_type=FieldType.NUMBER,
                    required=False,
                    suggested_value=suggested.get(
                        "default_best_before_days_after_freezing"
                    ),
                    step=1,
                ),
                FormField(
                    key="default_best_before_days_after_thawing",
                    field_type=FieldType.NUMBER,
                    required=False,
                    suggested_value=suggested.get(
                        "default_best_before_days_after_thawing"
                    ),
                    step=1,
                ),
            ])

        return fields

    def _build_choose_stock_entry_fields(self) -> list[FormField]:
        """Build fields for choosing a stock entry to transfer."""

        masterdata = self._masterdata
        product = self.current_product_stock_info["product"]

        qu = None
        for qq in filter(
            lambda p: p["id"] == product["qu_id_stock"],
            masterdata["quantity_units"],
        ):
            qu = qq
            break

        options = [
            SelectOption(
                value=str(e["id"]),
                label=(
                    f"{product['name']} {e['amount']} "
                    f"{qu['name_plural'] if e['amount'] > 1 else qu['name']}, "
                    f"due: {e['best_before_date']}"
                ),
            )
            for e in self.current_stock_entries
        ]

        selected = (
            str(self.current_stock_entries[0]["id"])
            if self.current_stock_entries
            else None
        )

        return [
            FormField(
                key="stock_entry_id",
                field_type=FieldType.SELECT,
                required=True,
                suggested_value=selected,
                default=selected,
                options=options,
                select_mode=SelectMode.DROPDOWN,
            ),
        ]

    def _build_transfer_input_fields(self) -> list[FormField]:
        """Build fields for specifying transfer details."""

        masterdata = self._masterdata
        product = self.current_product_stock_info["product"]
        stock_entry = self.current_stock_entries[0]

        locations = [
            loc
            for loc in masterdata["locations"]
            if loc["id"] != stock_entry["location_id"]
            and (product["should_not_be_frozen"] == 0 or loc["is_freezer"] == 0)
        ]
        locations.sort(key=lambda loc: loc["name"])

        default_location = (
            str(locations[0]["id"]) if len(locations) > 0 else None
        )

        fields: list[FormField] = []

        if stock_entry["amount"] > 1:
            fields.append(
                FormField(
                    key="amount",
                    field_type=FieldType.NUMBER,
                    required=True,
                    suggested_value=stock_entry["amount"],
                    default=stock_entry["amount"],
                    number_mode=NumberMode.SLIDER,
                    step=product.get("quick_consume_amount", 1) or 1,
                    min_value=product.get("quick_consume_amount", 1) or 1,
                    max_value=stock_entry["amount"],
                ),
            )

        fields.append(
            FormField(
                key="location_to_id",
                field_type=FieldType.SELECT,
                required=True,
                suggested_value=default_location,
                default=default_location,
                options=[
                    SelectOption(value=str(loc["id"]), label=loc["name"])
                    for loc in locations
                ],
                select_mode=SelectMode.DROPDOWN,
            ),
        )

        return fields

    def _build_scan_process_fields(
        self,
        product: dict,
        price: Any,
        bestBeforeInDays: Any,
        shopping_location_id: Any,
    ) -> list[FormField]:
        """Build extra input fields for purchase mode.

        Returns an empty list when no extra input is required.
        """

        masterdata = self._masterdata
        fields: list[FormField] = []

        if (
            price is None
            and self.scan_options.get("input_price")
            and not self.current_recipe
        ):
            fields.append(
                FormField(
                    key="price",
                    field_type=FieldType.TEXT,
                    required=False,
                    suggested_value=price,
                ),
            )

        if self.scan_options.get("input_bestBeforeInDays"):
            fields.append(
                FormField(
                    key="bestBeforeInDays",
                    field_type=FieldType.TEXT,
                    required=False,
                    suggested_value=(
                        str(bestBeforeInDays) if bestBeforeInDays is not None else None
                    ),
                ),
            )

        if (
            shopping_location_id is None
            and self.scan_options.get("input_shoppingLocationId")
            and not self.current_recipe
        ):
            shopping_locations = sorted(
                masterdata.get("shopping_locations", []),
                key=lambda loc: loc["name"],
            )

            # Check default store on product barcode
            if self.current_product_stock_info and self.current_product_stock_info.get(
                "product_barcodes"
            ):
                for barcode in self.current_product_stock_info["product_barcodes"]:
                    if (
                        barcode.get("barcode", "").casefold()
                        == (self.current_barcode or "").casefold()
                    ):
                        shopping_location_id = barcode.get(
                            "shopping_location_id"
                        )
                        if shopping_location_id:
                            break

            if self.current_product_stock_info and not shopping_location_id:
                shopping_location_id = self.current_product_stock_info.get(
                    "default_shopping_location_id",
                    self.current_product_stock_info.get("product", {}).get(
                        "default_shopping_location_id"
                    ),
                )

            fields.append(
                FormField(
                    key="shopping_location_id",
                    field_type=FieldType.SELECT,
                    required=False,
                    suggested_value=(
                        str(shopping_location_id)
                        if shopping_location_id
                        else None
                    ),
                    options=[
                        SelectOption(value=str(loc["id"]), label=loc["name"])
                        for loc in shopping_locations
                    ],
                    select_mode=SelectMode.DROPDOWN,
                ),
            )

        return fields

    # =================================================================
    # Private helpers
    # =================================================================

    def _get_aliases(self) -> list[str]:
        """Return product name aliases from lookup data or recipe."""

        if self.current_lookup:
            return self.current_lookup.get("product_aliases") or []
        if self.current_recipe:
            return [f"Matlåda: {self.current_recipe['name']}"]
        return []

    def _location_options(self) -> list[SelectOption]:
        return [
            SelectOption(value=str(loc["id"]), label=loc["name"])
            for loc in self._masterdata["locations"]
        ]

    def _qu_options(self, include_blank: bool = False) -> list[SelectOption]:
        options = [
            SelectOption(value=str(qu["id"]), label=qu["name"])
            for qu in self._masterdata["quantity_units"]
        ]
        if include_blank:
            options.insert(0, SelectOption(value="", label=""))
        return options

    @staticmethod
    def _str_val(val: Any) -> str | None:
        """Convert a value to ``str`` for select field suggested values."""
        return str(val) if val is not None else None

    @staticmethod
    def _merge_product_values(
        user_input: dict[str, Any],
        product: dict[str, Any],
        keys: list[str],
    ) -> dict[str, Any]:
        """Merge user input with product state for form suggested values.

        User input takes precedence.  Non-numeric values are converted
        to ``str`` (required for select-field suggested values).
        """

        suggested: dict[str, Any] = {}
        for k in keys:
            val = user_input.get(k, product.get(k))
            if k not in _NUMERIC_FIELDS:
                val = str(val) if val is not None else None
            suggested[k] = val
        return suggested

    def _show_add_product_form(
        self, product: dict[str, Any], errors: dict[str, str]
    ) -> FormRequest:
        """Build and return the add-product form."""
        suggested = self._merge_product_values(
            {},
            product,
            [
                "name",
                "location_id",
                "should_not_be_frozen",
                "default_best_before_days",
                "default_best_before_days_after_open",
                "qu_id_stock",
                "qu_id_purchase",
                "qu_id_consume",
                "qu_id_price",
            ],
        )
        fields = self._build_create_product_fields(suggested, creating_parent=False)
        aliases = self._get_aliases()
        return FormRequest(
            step_id=Step.SCAN_ADD_PRODUCT,
            fields=fields,
            description_placeholders={
                "name": product.get("name"),
                "barcode": self.current_barcode,
                "product_aliases": "\n".join([f"- {a.strip()}" for a in aliases if a]),
                "lookup_output": (self.current_lookup or {}).get("lookup_output"),
            },
            errors=errors,
        )

    def _build_product_from_input(
        self, user_input: dict[str, Any], base_product: dict[str, Any]
    ) -> dict[str, Any]:
        """Build a new product dict from user input."""
        product = base_product.copy()
        product["name"] = user_input["name"]
        product["location_id"] = user_input["location_id"]
        product["should_not_be_frozen"] = (
            1 if user_input.get("should_not_be_frozen", False) else 0
        )
        
        # Optional fields
        if val := user_input.get("default_consume_location_id"):
            product["default_consume_location_id"] = int(val)
        if val := user_input.get("default_best_before_days"):
            product["default_best_before_days"] = int(val)
        if val := user_input.get("default_best_before_days_after_open"):
            product["default_best_before_days_after_open"] = int(val)
        if val := user_input.get("default_best_before_days_after_freezing"):
            product["default_best_before_days_after_freezing"] = int(val)
        if val := user_input.get("default_best_before_days_after_thawing"):
            product["default_best_before_days_after_thawing"] = int(val)
        
        # Quantity units
        product["qu_id_stock"] = user_input.get(
            "qu_id_stock", user_input.get("qu_id")
        )
        product["qu_id_purchase"] = user_input.get(
            "qu_id_purchase", user_input.get("qu_id")
        )
        product["qu_id_consume"] = user_input.get(
            "qu_id_consume", user_input.get("qu_id")
        )
        product["qu_id_price"] = user_input.get(
            "qu_id_price", user_input.get("qu_id")
        )
        
        product["description"] = user_input.get("description", product.get("description"))
        product["parent_product_id"] = user_input.get(
            "parent_product_id", product.get("parent_product_id")
        )
        
        return product

    def _validate_product_location(
        self, product: dict[str, Any]
    ) -> dict[str, str]:
        """Validate product location constraints. Returns errors dict."""
        errors: dict[str, str] = {}
        
        loc = next(
            (
                loc
                for loc in self._masterdata["locations"]
                if str(loc["id"]) == str(product["location_id"])
            ),
            None,
        )
        
        if not loc:
            errors["location_id"] = "invalid_location"
        elif product.get("should_not_be_frozen") == 1 and loc.get("is_freezer") == 1:
            errors["location_id"] = "location_is_freezer"
        
        return errors

    def _show_match_product_form(self) -> FormRequest:
        """Build and return the match-product form."""
        _LOGGER.warning("Matching products: %s", self.matching_products)
        aliases = self._get_aliases()
        allow_parent = not self.current_recipe
        fields = self._build_match_product_fields(
            suggested_products=self.matching_products,
            aliases=aliases,
            allow_parent=allow_parent,
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
                "lookup_output": (self.current_lookup or {}).get("lookup_output"),
                "product_matches": "\n".join(
                    f"{p['name']}" for p in self.matching_products
                ),
            },
            errors={},
        )
        return self._cached_form

    async def _process_parent_selection(
        self, user_input: dict[str, Any]
    ) -> StepResult | None:
        """Process the parent_product field. Returns error result or None."""
        self.current_parent = None
        
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
            # TODO: Remove this WIP testing code
            if i == 1337:
                user_input["product_id"] = None
            self.current_parent = await self._api_grocy.get_product_by_id(i)
        
        # If not found or was a string, treat as new parent product name
        if self.current_parent is None:
            self.current_parent = {"name": p if p != "-1" else None}
        
        return None

    async def _process_product_selection(
        self, user_input: dict[str, Any]
    ) -> StepResult | None:
        """Process the product_id field. Returns error result or None."""
        self.current_product = None
        
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
            # Load existing product
            self.current_product_stock_info = (
                await self._api_grocy.get_stock_product_by_id(i)
            )
            self.current_product = (self.current_product_stock_info or {}).get(
                "product"
            )
            
            # Link recipe to product if needed
            if self.current_product and self.current_recipe:
                await self._link_recipe_to_product()
        
        # If not found or was a string, create new product template
        if self.current_product is None:
            self.current_product = {
                "name": p if p != "-1" else None,
                "parent_product_id": (
                    self.current_parent.get("id") if self.current_parent else None
                ),
            }
            
            # Add recipe defaults
            if self.current_recipe:
                self._apply_recipe_product_defaults()
        
        return None

    async def _link_recipe_to_product(self) -> None:
        """Link the current recipe to the current product."""
        recipe_changes = {"product_id": self.current_product["id"]}
        await self._update_recipe(self.current_recipe["id"], recipe_changes)
        _LOGGER.info(
            "Linked recipe #%s to product #%s",
            self.current_recipe["id"],
            self.current_product["id"],
        )
        self.current_recipe.update(recipe_changes)

    def _apply_recipe_product_defaults(self) -> None:
        """Apply default settings for recipe-produced products."""
        self.current_product.update({
            "location_id": 5,  # TODO: Configurable default Freezer location
            "default_consume_location_id": 2,  # TODO: Configurable default Fridge
            "default_best_before_days": 3,
            "default_best_before_days_after_open": 3,
            "default_best_before_days_after_freezing": 60,
            "default_best_before_days_after_thawing": 3,
        })
