"""Config flow for Grocy-helper integration."""

import copy
from enum import StrEnum
import datetime as dt
import logging
import json
from typing import Any
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import (
    ConfigEntry,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.typing import VolDictType

from .coordinator import GrocyHelperCoordinator
from .grocyapi import GrocyAPI
from .barcodebuddyapi import BarcodeBuddyAPI
from .grocytypes import (
    BarcodeLookup,
    ExtendedGrocyProductStockInfo,
    GrocyAddProductQuantityUnitConversion,
    GrocyProduct,
    GrocyProductBarcode,
    GrocyMasterData,
    GrocyQuantityUnit,
    GrocyQuantityUnitConversionResult,
    GrocyRecipe,
    GrocyStockEntry,
    OpenFoodFactsProduct,
)
from .utils import try_parse_int

from .const import (
    DOMAIN,
    CONF_GROCY_API_URL,
    CONF_GROCY_API_KEY,
    CONF_BBUDDY_API_URL,
    CONF_BBUDDY_API_KEY,
    SCAN_MODE,
)

_LOGGER = logging.getLogger(__name__)


STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(
            CONF_GROCY_API_URL,
            description="Grocy API url",
            default="http://localhost:4010",
        ): cv.string,
        vol.Required(CONF_GROCY_API_KEY, description="Grocy API Key"): cv.string,
        vol.Required(
            CONF_BBUDDY_API_URL,
            description="Barcode Buddy API url",
            default="http://localhost:4011",
        ): cv.string,
        vol.Required(
            CONF_BBUDDY_API_KEY, description="Barcode Buddy API Key"
        ): cv.string,
    }
)


class Step(StrEnum):
    MAIN_MENU = "main_menu"
    ADD_RECIPE = "add_recipe"

    SCAN_START = "scan_start"
    SCAN_QUEUE = "scan_queue"
    SCAN_MATCH_PRODUCT = "scan_match_to_product"
    SCAN_ADD_PRODUCT = "scan_add_product"
    SCAN_ADD_PRODUCT_PARENT = "scan_add_product_parent"
    SCAN_ADD_PRODUCT_BARCODE = "scan_add_product_barcode"
    SCAN_UPDATE_PRODUCT_DETAILS = "scan_update_product_details"
    SCAN_TRANSFER_START = "scan_transfer_start"
    SCAN_TRANSFER_INPUT = "scan_transfer_input"
    SCAN_PROCESS = "scan_process"


MAIN_MENU = [
    Step.SCAN_START,
    # Step.ADD_RECIPE,
]


class GrocyConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for ICA."""

    VERSION = 1
    MINOR_VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        # if self._async_current_entries():
        #     return self.async_abort(reason="single_instance_allowed")

        errors: dict[str, str] = {}
        if user_input is not None:
            grocy_url = user_input[CONF_GROCY_API_URL]
            grocy_api_key = user_input[CONF_GROCY_API_KEY]
            bbuddy_url = user_input[CONF_BBUDDY_API_URL]
            bbuddy_api_key = user_input[CONF_BBUDDY_API_KEY]

            # Assign unique id based on Host/Port
            # WIP: use api_key to indicate uniqueness, as host might change during dev, in future should make this non-reversable
            await self.async_set_unique_id(f"{DOMAIN}__{grocy_api_key}")
            # Abort flow if a config entry with same Host and Port exists
            self._abort_if_unique_id_configured()

            config_entry_data = {
                CONF_GROCY_API_URL: grocy_url,
                CONF_GROCY_API_KEY: grocy_api_key,
                CONF_BBUDDY_API_URL: bbuddy_url,
                CONF_BBUDDY_API_KEY: bbuddy_api_key,
            }
            return self.async_create_entry(
                # title=f"{host}:{port}",
                title=grocy_url,
                data=config_entry_data,
            )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        # return await self.async_step_user(user_input=user_input)
        """Handle the reconfigure step."""
        # if self._async_current_entries():
        #     return self.async_abort(reason="single_instance_allowed")

        errors: dict[str, str] = {}
        if user_input is not None:
            grocy_url = user_input[CONF_GROCY_API_URL]
            grocy_api_key = user_input[CONF_GROCY_API_KEY]
            bbuddy_url = user_input[CONF_BBUDDY_API_URL]
            bbuddy_api_key = user_input[CONF_BBUDDY_API_KEY]

            # Assign unique id based on Host/Port
            # WIP: use api_key to indicate uniqueness, as host might change during dev, in future should make this non-reversable
            await self.async_set_unique_id(f"{DOMAIN}__{grocy_api_key}")

            # # Abort flow if a config entry with same Host and Port exists
            # self._abort_if_unique_id_configured()

            self._abort_if_unique_id_mismatch()

            config_entry_data = {
                CONF_GROCY_API_URL: grocy_url,
                CONF_GROCY_API_KEY: grocy_api_key,
                CONF_BBUDDY_API_URL: bbuddy_url,
                CONF_BBUDDY_API_KEY: bbuddy_api_key,
            }
            return self.async_update_reload_and_abort(
                self._get_reconfigure_entry(),
                data_updates=config_entry_data,
            )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> OptionsFlow:
        """Create the options flow."""
        return GrocyOptionsFlowHandler(config_entry)


class GrocyOptionsFlowHandler(OptionsFlow):
    """Handle an options flow for grocy-helper."""

    _coordinator: GrocyHelperCoordinator = None
    _api_grocy: GrocyAPI = None
    _api_bbuddy: BarcodeBuddyAPI = None

    scan_options: dict[str, str] = {
        "input_price": True,
        "input_bestBeforeInDays": True,
        "input_shoppingLocationId": True,
        "input_product_details_during_provision": True,
        # TODO: Enable detailed Barcode details; defaults for: [shopping_location_id, qu_id, amount] for specific Barcode
    }
    current_bb_mode: int = -1
    barcode_scan_mode: str = None
    barcode_queue: list[str] = []
    barcode_results: list[str] = []

    current_barcode: str = None
    current_barcode_schema: vol.Schema = None
    current_product_stock_info: ExtendedGrocyProductStockInfo | None = None
    current_product_openfoodfacts: OpenFoodFactsProduct | None = None
    current_product_ica: dict | None = None
    current_lookup: BarcodeLookup | None = None

    matching_products: list[GrocyProduct] = []
    current_stock_entries: list[GrocyStockEntry] = []

    current_product: GrocyProduct | None = None
    current_parent: GrocyProduct | None = None
    current_recipe: GrocyRecipe | None = None
    current_recipe_id: int | None = None

    # Cache of the form schema, to easily return errors (must be set to null in forms that support it)
    current_form_args: VolDictType | None = None

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize Grocy-helper options flow"""
        # pylint: disable=W0613 unused-argument
        super().__init__()

        self._coordinator = config_entry.coordinator
        self._api_grocy = self._coordinator._api_grocy
        self._api_bbuddy = self._coordinator._api_bbuddy

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        _LOGGER.debug("Options flow - data: %s", self.config_entry.data)

        # Handle input
        if user_input is None and len(MAIN_MENU) == 1:
            user_input = {"choose-form": MAIN_MENU[0]}

        if user_input is not None:
            if form := user_input.get("choose-form"):
                self.chosen_form = form
                if form == "main_menu":
                    return await self.async_step_main_menu(user_input=user_input)
                if form == "scan_start":
                    return await self.async_step_scan_start()

            return self.async_abort(reason="No operation chosen")

        # Format form schema
        schema = vol.Schema(
            {
                vol.Required(
                    "choose-form",
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        # options=["get_product", "add_product", "main_menu"],
                        options=MAIN_MENU,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                        multiple=False,
                    )
                ),
            }
        )
        # ).extend(self.SHOPPING_LIST_SELECTOR_SCHEMA or {})

        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            errors=errors,
        )
        # menu_options = MAIN_MENU.copy()
        # return self.async_show_menu(step_id=Step.MAIN_MENU, menu_options=menu_options)

    async def async_step_main_menu(self, user_input: dict[str, Any]):
        """Handle the group choice step."""
        _LOGGER.debug("Options flow - Main_menu: %s #%s", user_input, self.chosen_form)
        menu = MAIN_MENU.copy()
        return self.async_show_menu(step_id=Step.MAIN_MENU, menu_options=menu)

    async def async_step_scan_start(self, user_input: dict[str, Any] = None):
        """Handle the initial step."""
        errors: dict[str, str] = {}
        _LOGGER.debug("Options flow - scan: %s #%s", user_input, self.chosen_form)

        # Handle input
        if user_input is None:
            bb_mode = await self._api_bbuddy.get_mode()
            if bb_mode is not None and bb_mode >= 0:
                self.current_bb_mode = bb_mode
            scan_mode_from_bbuddy = self._api_bbuddy.convert_bbuddy_mode_to_scan_mode(
                self.current_bb_mode
            )
            _LOGGER.info("BBuddy mode is: %s (%s)", bb_mode, scan_mode_from_bbuddy)
            return self.async_show_form(
                step_id=Step.SCAN_START,
                data_schema=GENERATE_STEP_SCAN_START_SCHEMA(scan_mode_from_bbuddy),
                errors=errors,
            )

        barcodes_input = user_input["barcodes"]
        self.barcode_scan_mode = user_input.get("mode")
        _LOGGER.info("SCAN: %s", barcodes_input)
        _LOGGER.info("SCAN-mode: %s", self.barcode_scan_mode)

        self.barcode_queue = []
        self.barcode_results = []

        # Parse barcodes from input (split by new-line and spaces)
        self.barcode_queue = [part for part in barcodes_input.split() if part]

        return await self.async_step_scan_queue()

    async def async_step_scan_queue(self, user_input: dict[str, Any] = None):
        """Handle the scan-queue."""
        errors: dict[str, str] = {}
        config_entry_data = self.config_entry.data.copy()
        _LOGGER.debug(
            "Options flow - process_scan inpt: %s #%s", user_input, self.chosen_form
        )
        _LOGGER.debug("Options flow - process_scan ced: %s", config_entry_data)
        _LOGGER.debug("Options flow - process_scan queue: %s", self.barcode_queue)

        self.current_product_stock_info = None
        current_barcode = self.barcode_queue[0] if len(self.barcode_queue) > 0 else None

        if not current_barcode:
            # Nothing in queue, show summary
            # TODO: Add result info to message...
            msg = (
                "\r\n".join(self.barcode_results)
                if self.barcode_results
                else "No barcodes were processed"
            )
            _LOGGER.info(
                "Options flow - process_scan Nothing more in scan queue!: %s",
                len(self.barcode_results),
            )
            return self.async_abort(reason=msg)

        code = current_barcode.strip().strip(",").strip().lstrip("0")
        if self.current_barcode != code:
            # Different barcode since last time. Clear all info
            self.current_product_stock_info = None
            self.current_product_openfoodfacts = None
            self.current_product_ica = None
            self.current_product = None
            self.current_parent = None
            self.current_recipe = None
            self.current_recipe_id = None
            self.current_lookup = None
            self.matching_products: list[GrocyProduct] = []
        self.current_barcode = code

        if self.barcode_scan_mode == SCAN_MODE.SCAN_BBUDDY:
            bb_mode = await self._api_bbuddy.get_mode()
            if bb_mode is not None and bb_mode >= 0:
                _LOGGER.info("BBuddy mode is: %s (%s)", bb_mode, self.barcode_scan_mode)
                self.current_bb_mode = bb_mode
        else:
            self.current_bb_mode = None

        masterdata: GrocyMasterData = self._coordinator.data
        if self.barcode_scan_mode == SCAN_MODE.PROVISION or (
            self.barcode_scan_mode != SCAN_MODE.INVENTORY
            and self.barcode_scan_mode != SCAN_MODE.QUANTITY
        ):
            if "grcy:r:" in code:
                # TODO: Handle "Purchase"/Consume/Inventory/Provision, /Transfer etc.
                (r, i) = try_parse_int(code.replace("grcy:r:", ""))
                if r and i > 0:
                    # Passed a barcode/reference to an Grocy recipe
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
                        # TODO: Flow for creating new recipe??
                        return self.async_abort(reason=f"Recipe with id '{i}' was not found")
                    
                    _LOGGER.debug('Found recipe: %s', self.current_recipe)
                    if product_id := self.current_recipe["product_id"]:
                        # Recipe has a producing product, then fetch info and continue flow with product state
                        self.current_product_stock_info = await self._api_grocy.get_stock_product_by_id(product_id)
                        self.current_product = (self.current_product_stock_info or {}).get("product")
                        _LOGGER.info("Recipe '%s' produces product: %s", self.current_recipe["id"], self.current_product)

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
                else:
                    return self.async_abort(reason=f"Could not parse recipe barcode: {code}")

            # Check for BarcodeBuddy code (those should be passed directly to BBuddy for updating context)
            if "BBUDDY-" not in code:
                # Not a BarcodeBuddy code

                # Lookup product in Grocy (if not already loaded...)
                if not self.current_product and not self.current_recipe:
                    try:
                        self.current_product_stock_info = await self._api_grocy.get_stock_product_by_barcode(code)
                        self.current_product = (self.current_product_stock_info or {}).get("product")
                        _LOGGER.info(
                            "GrocyProduct lookup: %s",
                            self.current_product_stock_info,
                        )
                    except BaseException as be:
                        _LOGGER.error("Get product excep: %s", be)
                        errors["Exception"] = be
                        raise be

                # Init Transfer-mode
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
                    return await self.async_step_scan_transfer_start(
                        user_input=None
                    )

                # If product doesn't exist, then enter flow to create it
                if not self.current_product:
                    # New product (Not provisioned in Grocy)
                    _LOGGER.info(
                        "New product, doing lookup against barcode providers: %s",
                        code,
                    )

                    if (
                        not self.current_recipe
                        and (
                            not self.current_lookup
                            or self.current_lookup["barcode"] != code
                        )
                    ):
                        # Refresh lookup info, if needed
                        self.current_lookup = (
                            await self._coordinator.lookup_barcode(code)
                        )
                        self.current_product_openfoodfacts = (
                            self.current_lookup.get("off")
                        )
                        self.current_product_ica = self.current_lookup.get("ica")

                    for matching_product in filter(
                        lambda p: (
                            # # OFF.product_name
                            # (
                            #     self.current_product_openfoodfacts is not None
                            #     and (
                            #         p["name"].casefold()
                            #         == self.current_product_openfoodfacts.get(
                            #             "product_name", ""
                            #         ).casefold()
                            #     )
                            # )
                            # # OFF.genric_name
                            # or (
                            #     self.current_product_openfoodfacts is not None
                            #     and (
                            #         p["name"].casefold()
                            #         == (
                            #             self.current_product_openfoodfacts.get(
                            #                 "generic_name", ""
                            #             )
                            #             or ""
                            #         ).casefold()
                            #     )
                            # )
                            # # ICA.ean_name
                            # or (
                            #     self.current_product_ica is not None
                            #     and (
                            #         p["name"].casefold()
                            #         == (
                            #             self.current_product_ica.get("ean_name", "")
                            #             or ""
                            #         ).casefold()
                            #     )
                            # )
                            # # ICA.article.name
                            # or (
                            #     self.current_product_ica is not None
                            #     and (
                            #         p["name"].casefold()
                            #         == (
                            #             self.current_product_ica.get(
                            #                 "article", {}
                            #             ).get("name", "")
                            #             or ""
                            #         ).casefold()
                            #     )
                            # )
                            # or
                            # Match against collected aliases
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
                            or
                            # Match against recipe name
                            (
                                self.current_recipe and (
                                    p["name"] in [
                                        self.current_recipe["name"],
                                        f"Matlåda: {self.current_recipe["name"]}"
                                    ]
                                )
                            )
                            # TODO: ICA offer name
                        ),
                        masterdata["products"],
                    ):
                        # TODO: also loop through ProductBarcode notes
                        # TODO: skip Active==0 products
                        _LOGGER.info("Match: %s", matching_product)
                        self.matching_products.append(matching_product)

                    # always give option to map to an existing product...
                    return await self.async_step_scan_match_to_product(
                        user_input=None
                    )

        if self.barcode_scan_mode == SCAN_MODE.PROVISION:
            # Mode is to simply ensure product/barcode exists
            # remove from queue, and then restart the queue...
            self.barcode_queue.pop(0)

            _LOGGER.info("Provisioned: %s", self.current_product)
            self.barcode_results.append(f"{code} maps to {self.current_product['name']}")
            return await self.async_step_scan_queue(user_input=None)

        if self.barcode_scan_mode == SCAN_MODE.INVENTORY:
            if not self.current_product_stock_info:
                # Load stock info if not already loaded...
                self.current_product_stock_info = (
                    await self._api_grocy.get_stock_product_by_barcode(code)
                )
                self.current_product = (self.current_product_stock_info or {}).get(
                    "product"
                )

        # Proceed with BarcodeBuddy processing
        return await self.async_step_scan_process(user_input=None)

    async def async_step_scan_match_to_product(self, user_input: dict[str, Any] = None):
        """Handle input for adding barcode to a product."""
        errors: dict[str, str] = {}
        # self.current_form_args = None
        if self.current_form_args:
            self.current_form_args["errors"] = errors
        _LOGGER.info("match-product: %s", user_input)
        _LOGGER.info("matches: %s", self.matching_products)

        masterdata: GrocyMasterData = self._coordinator.data
        code = self.current_barcode

        # Handle input, for required fields
        if user_input is None:
            # Has matching product, display as a suggestion
            _LOGGER.warning("Matching products: %s", self.matching_products)
            aliases = (
                (self.current_lookup.get("product_aliases") or [])
                if self.current_lookup
                else (
                    [f"Matlåda: {self.current_recipe["name"]}"]
                    if self.current_recipe
                    else []
                )
            )
            allow_parent = not self.current_recipe
            schema = GENERATE_CHOOSE_EXISTING_PRODUCT_SCHEMA(
                masterdata=masterdata,
                suggested_products=self.matching_products,
                lookup=self.current_lookup,
                aliases=aliases,
                allow_parent=allow_parent,
            )
            schema = vol.Schema(schema)

            plc = {
                "barcode": code,
                "recipe_info": f"## Recipe\n\#{self.current_recipe["id"]} {self.current_recipe["name"]}" if self.current_recipe else None,
                "product_aliases": "\n".join(
                    [f"- {a.strip()}" for a in aliases if a]
                ),  # Format aliases as a Markdown-list
                "lookup_output": (self.current_lookup or {}).get("lookup_output"),
                "product_matches": "\n".join(
                    f"{p['name']}" for p in self.matching_products
                ),
            }
            self.current_form_args = {
                "step_id": Step.SCAN_MATCH_PRODUCT,
                "data_schema": schema,
                "description_placeholders": plc,
                "errors": errors,
            }
            _LOGGER.debug("FORM-ARGS: %s", self.current_form_args)
            return self.async_show_form(**self.current_form_args)

        # Form has been submitted!
        # TODO: Validate

        self.current_product = None
        self.current_parent = None

        # Parent product (if specified)
        if p := user_input.get("parent_product"):
            if self.current_recipe:
                errors["parent_product"] = "Not allowed when creating a recipe product"
                self.current_form_args["errors"] = errors
                _LOGGER.warning("Passing new form args: %s", self.current_form_args)
                # Use a cached version of 'schema'...
                # TODO: Verify
                return self.async_show_form(**self.current_form_args)

            (r, i) = try_parse_int(p)
            if r and i > 0:

                # TODO: Remove this WIP testing code
                if i == 1337:
                    user_input["product_id"] = None

                # Has chosen existing Parent product
                self.current_parent = await self._api_grocy.get_product_by_id(i)
            if self.current_parent is None:
                self.current_parent = {"name": p if p != "-1" else None}
        else:
            _LOGGER.debug("No parent was input")

        # Product
        if p := user_input.get("product_id"):
            (r, i) = try_parse_int(p)
            if r and i > 0:
                # Has chosen existing Product
                self.current_product_stock_info = (
                    await self._api_grocy.get_stock_product_by_id(i)
                )
                self.current_product = (self.current_product_stock_info or {}).get(
                    "product"
                )

                if self.current_product and self.current_recipe:
                    # Selected an existing product for the recipe. Connect them together!
                    recipe_changes = {
                        "product_id": self.current_product["id"]
                    }
                    await self._api_grocy.update_recipe(self.current_recipe["id"], recipe_changes)
                    # TODO: Check for success?
                    _LOGGER.info("Updated recipe '%s' with consuming product '%s'", self.current_recipe["id"], self.current_product["id"])
                    # update local cache with assumed changes
                    self.current_recipe.update(recipe_changes)
                    if r := next(
                        (
                            recipe
                            for recipe in masterdata["recipes"]
                            if str(recipe["id"]) == str(self.current_recipe["id"])
                        ),
                        None,
                    ):
                        r.update(recipe_changes)

            # TODO: Validate that the product doesn't already belong to a (different) parent!!
            # TODO: Validate that the product doesn't already have a different barcode. Which could cause differences in quantities. (Submit again to add anyway?)
            # Allow for "" or "id" value of the actual parent

            if self.current_product is None:
                # Has not chosen product (or was not found)
                # Set to create a new product (by omitting the 'id' field)
                # Currently the only thing we know is the name (this will be filled in on the next form page)
                self.current_product = {
                    "name": p if p != "-1" else None,
                    "parent_product_id": self.current_parent.get("id", None)
                    if self.current_parent
                    else None,
                }
                if self.current_recipe:
                    # Creating product for recipe... fill in some defaults...
                    self.current_product["location_id"] = 5 # TODO: "default" Freezer
                    self.current_product["default_consume_location_id"] = 2 # TODO: "default" Fridge
                    self.current_product["default_best_before_days"] = 3    # default to 3 days
                    self.current_product["default_best_before_days_after_open"] = 3
                    self.current_product["default_best_before_days_after_freezing"] = 60
                    self.current_product["default_best_before_days_after_thawing"] = 3
                    # self.current_product["calories"] = 1 # TODO: Calculate total calories of all ingredients / serving
                    # self.current_product["product_group_id"] = 1 # TODO: Assign an appropriate product group?
        else:
            # Invalid value in 'product_id' field which is required
            errors["product_id"] = "Missing value"
            self.current_form_args["errors"] = errors
            _LOGGER.warning("Passing new form args: %s", self.current_form_args)
            # Use a cached version of 'schema'...
            # TODO: Verify
            return self.async_show_form(**self.current_form_args)

        # If Product is new, then proceed to generation
        if not self.current_product.get("id"):
            return await self.async_step_scan_add_product(user_input=None)

        # else if Parent is new, then proceed to generation
        return await self.async_step_scan_add_product_parent(user_input=None)

    async def async_step_scan_add_product(self, user_input: dict[str, Any] = None):
        """Handle input for adding a new product."""
        errors: dict[str, str] = {}
        self.current_form_args = None
        masterdata: GrocyMasterData = self._coordinator.data
        _LOGGER.info("form 'add_product' user_input: %s", user_input)

        new_product: GrocyProduct = (self.current_product or {}).copy()
        if self.current_product.get("id"):
            # Create new product
            _LOGGER.warning("Product already has an id: %s", self.current_product)
            return self.async_abort(reason="Should not render Create product form, since a product already exists")

        code = self.current_barcode

        first_render = user_input is None
        if user_input is None:
            user_input = user_input or {}

        # Generate form schema
        schema: VolDictType = None
        schema = GENERATE_CREATE_PRODUCT_SCHEMA(
            masterdata, user_input, creating_parent=False
        )
        schema = vol.Schema(schema)

        _LOGGER.info("Original input: %s", user_input)
        data_schema: vol.Schema = (
            self.current_form_args["data_schema"] if self.current_form_args else schema
        )
        for k in data_schema.schema.keys():
            # Fill user_input with current state of ´new_product´
            val = user_input.get(k, new_product.get(k))
            if k not in [
                "should_not_be_frozen",
                "calories_per_100",
                "default_best_before_days",
                "default_best_before_days_after_open",
                "default_best_before_days_after_freezing",
                "default_best_before_days_after_thawing",
            ]:
                # if not part of exceptions, then set value in ´str´
                # Exceptions are most likley in ´int´
                val = str(val) if val is not None else None
            user_input[k] = val
        _LOGGER.info("Updated input: %s", user_input)

        schema = self.add_suggested_values_to_schema(schema, user_input)

        aliases = (
            (self.current_lookup.get("product_aliases") or [])
            if self.current_lookup
            else (
                [f"Matlåda: {self.current_recipe["name"]}"]
                if self.current_recipe
                else []
            )
        )
        plc = {
            "name": new_product.get("name"),
            "barcode": code,
            # "lookup_name": ica_fullname or off_fullname,
            "product_aliases": "\n".join(
                [f"- {a.strip()}" for a in aliases if a]
            ),  # Format aliases as a Markdown-list
            "lookup_output": (self.current_lookup or {}).get("lookup_output"),
            # "product_matches": "\n".join(
            #     f"{p['name']}" for p in self.matching_products
            # ),
        }
        if first_render:
            self.current_form_args = {
                "step_id": Step.SCAN_ADD_PRODUCT,
                "data_schema": schema,
                "description_placeholders": plc,
                "errors": errors,
            }
            _LOGGER.debug("FORM-ARGS: %s", self.current_form_args)
            return self.async_show_form(**self.current_form_args)

        # Input has been passed!
        if user_input.get("product_id") and user_input["product_id"] != "-1":
            # A specific product was chosen, use that instead of creation...
            _LOGGER.info("exist_products: %s", self.matching_products)
            _LOGGER.info("usr_inp: %s", user_input)
            self.current_product_stock_info = (
                await self._api_grocy.get_stock_product_by_id(
                    int(user_input["product_id"])
                )
            )
        else:
            # Create a new product
            # more friendly name (barcode has specific name/"note")
            new_product["name"] = user_input["name"]
            # new_product["description"] = user_input.get(
            #     "description",
            #     # fallback to a formatted name from OpenFoodFacts
            #     off_fullname,
            # )
            new_product["location_id"] = user_input["location_id"]
            new_product["should_not_be_frozen"] = (
                1 if user_input.get("should_not_be_frozen", False) else 0
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

            if val := user_input.get("default_consume_location_id"):
                new_product["default_consume_location_id"] = int(val)

            if val := user_input.get("default_best_before_days"):
                new_product["default_best_before_days"] = int(val)
            if val := user_input.get("default_best_before_days_after_open"):
                new_product["default_best_before_days_after_open"] = int(val)
            if val := user_input.get("default_best_before_days_after_freezing"):
                new_product["default_best_before_days_after_freezing"] = int(val)
            if val := user_input.get("default_best_before_days_after_thawing"):
                new_product["default_best_before_days_after_thawing"] = int(val)
            new_product["qu_id_stock"] = user_input.get(
                "qu_id_stock", user_input.get("qu_id")
            )
            new_product["qu_id_purchase"] = user_input.get(
                "qu_id_purchase", user_input.get("qu_id")
            )
            new_product["qu_id_consume"] = user_input.get(
                "qu_id_consume", user_input.get("qu_id")
            )
            new_product["qu_id_price"] = user_input.get(
                "qu_id_price", user_input.get("qu_id")
            )
            new_product["row_created_timestamp"] = dt.datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            new_product["description"] = user_input.get(
                "description",
                new_product.get(
                    "description",
                    # (
                    #     # fallback to the lookup results
                    #     self.current_lookup.get("lookup_output")
                    # ),
                ),
            )
            new_product["parent_product_id"] = user_input.get(
                "parent_product_id", new_product.get("parent_product_id")
            )

            _LOGGER.info("user_input: %s", user_input)
            _LOGGER.info("new_product: %s", new_product)
            if errors:
                # schema = self.add_suggested_values_to_schema(schema, user_input)
                _LOGGER.warning("Input errors: %s", errors)
                self.current_form_args = {
                    "step_id": Step.SCAN_ADD_PRODUCT,
                    "data_schema": schema,
                    "description_placeholders": plc,
                    "errors": errors,
                }
                _LOGGER.debug("FORM-ARGS: %s", self.current_form_args)
                return self.async_show_form(**self.current_form_args)

            # Create product
            product = await self._api_grocy.add_product(new_product)
            # TODO: check for success!
            _LOGGER.info("created prod: %s", product)
            # Product has been successfully created
            self.current_product_stock_info = (
                await self._api_grocy.get_stock_product_by_id(product["id"])
            )
            self.current_product = (self.current_product_stock_info or {}).get("product") or product

        return await self.async_step_scan_add_product_barcode(user_input=None)

    async def async_step_scan_add_product_parent(
        self, user_input: dict[str, Any] = None
    ):
        """Handle input for adding a new product parent."""
        errors: dict[str, str] = {}
        self.current_form_args = None
        masterdata: GrocyMasterData = self._coordinator.data
        _LOGGER.info("form 'add_product_parent' user_input: %s", user_input)

        _LOGGER.info("Create parent: %s", self.current_parent)
        new_product: GrocyProduct = (self.current_parent or {}).copy()
        creating_parent = True
        if self.current_parent is None:
            # Should not link to a parent, continue to next step...
            _LOGGER.debug("Product will not be linked to a parent, continue to next step...")
            return await self.async_step_scan_process(user_input=None)
        elif self.current_parent.get("id"):
            # Parent already exists, continue to next step...
            _LOGGER.debug("Product parent already exists, continue to next step...")
            return await self.async_step_scan_process(user_input=None)

        code = self.current_barcode

        first_render = user_input is None
        if user_input is None:
            user_input = user_input or {}

        # Generate form schema
        schema: VolDictType = None
        schema = GENERATE_CREATE_PRODUCT_SCHEMA(
            masterdata, user_input, creating_parent=creating_parent
        )
        schema = vol.Schema(schema)

        _LOGGER.info("Original input: %s", user_input)
        data_schema: vol.Schema = (
            self.current_form_args["data_schema"] if self.current_form_args else schema
        )
        for k in data_schema.schema.keys():
            # Fill user_input with current state of ´new_product´
            val = user_input.get(k, new_product.get(k))
            if not val and creating_parent:
                # Copy values from Product when creating a Parent
                if k in ["id", "name", "description"]:
                    # Exclude copying these props
                    continue
                _LOGGER.warning(
                    "COPY prop to parent: %s=%s", k, self.current_product[k]
                )
                val = self.current_product[k]

            if k not in [
                "should_not_be_frozen",
                "calories_per_100",
                "default_best_before_days",
                "default_best_before_days_after_open",
                "default_best_before_days_after_freezing",
                "default_best_before_days_after_thawing",
            ]:
                # if not part of exceptions, then set value in ´str´
                # Exceptions are most likley in ´int´
                val = str(val) if val is not None else None
            user_input[k] = val
        _LOGGER.info("Updated input: %s", user_input)

        # Set kg/L when appropriate
        _LOGGER.info("First render? %s", first_render)
        if first_render:
            piece_qu = masterdata["known_qu"].get("Piece")
            pack_qu = masterdata["known_qu"].get("Pack")
            piece_id = piece_qu.get("id") if isinstance(piece_qu, dict) else getattr(piece_qu, "id", None)
            pack_id = pack_qu.get("id") if isinstance(pack_qu, dict) else getattr(pack_qu, "id", None)
            if (int(user_input.get("qu_id_stock") or -99) in [piece_id, pack_id]) and (
                int(user_input.get("qu_id_price") or -99) not in [piece_id, pack_id]
            ):
                # Instead of Piece/Pack, copy from ´qu_id_price´ if not Piece/Pack (example: "KG" / "L")
                _LOGGER.warning(
                    "Copying ´qu_id_price´ into ´qu_id_stock´: %s. Known: %s", user_input, masterdata["known_qu"]
                )
                user_input["qu_id_stock"] = user_input["qu_id_price"]

        schema = self.add_suggested_values_to_schema(schema, user_input)

        # aliases = self.current_lookup.get("product_aliases") or []
        aliases = (
            (self.current_lookup.get("product_aliases") or [])
            if self.current_lookup
            else (
                [f"Matlåda: {self.current_recipe["name"]}"]
                if self.current_recipe
                else []
            )
        )
        plc = {
            "name": new_product.get("name"),
            "barcode": code,
            # "lookup_name": ica_fullname or off_fullname,
            "product_aliases": "\n".join(
                [f"- {a.strip()}" for a in aliases if a]
            ),  # Format aliases as a Markdown-list
            "lookup_output": (self.current_lookup or {}).get("lookup_output"),
            # "product_matches": "\n".join(
            #     f"{p['name']}" for p in self.matching_products
            # ),
        }
        if first_render:
            self.current_form_args = {
                "step_id": Step.SCAN_ADD_PRODUCT_PARENT,
                "data_schema": schema,
                "description_placeholders": plc,
                "errors": errors,
            }
            _LOGGER.debug("FORM-ARGS: %s", self.current_form_args)
            return self.async_show_form(**self.current_form_args)

        # Input has been passed!
        if user_input.get("product_id") and user_input["product_id"] != "-1":
            # A specific product was chosen, use that instead of creation...
            _LOGGER.info("exist_products: %s", self.matching_products)
            _LOGGER.info("usr_inp: %s", user_input)
            self.current_parent = await self._api_grocy.get_product_by_id(
                int(user_input["product_id"])
            )
        else:
            # Create a new product
            # more friendly name (barcode has specific name/"note")
            new_product["name"] = user_input["name"]
            # new_product["description"] = user_input.get(
            #     "description",
            #     # fallback to a formatted name from OpenFoodFacts
            #     off_fullname,
            # )
            new_product["location_id"] = user_input.get(
                "location_id", self.current_product["location_id"]
            )
            new_product["should_not_be_frozen"] = (
                1
                if user_input.get(
                    "should_not_be_frozen",
                    self.current_product.get("should_not_be_frozen", False),
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

            # TODO: Location not super relevant for Parent products, perhaps set value as per child. But don't render field for it?
            if not loc:
                errors["location_id"] = "invalid_location"
            elif new_product["should_not_be_frozen"] == 1 and loc["is_freezer"] == 1:
                errors["location_id"] = "location_is_freezer"

            # TODO: Since not handling any physical products with the Parent product, perhaps the due date-fields are irrelevant? (Set value as per child). Don't render field for it, to simplify?
            if val := user_input.get("default_best_before_days"):
                new_product["default_best_before_days"] = int(val)
            if val := user_input.get("default_best_before_days_after_open"):
                new_product["default_best_before_days_after_open"] = int(val)

            new_product["qu_id_stock"] = user_input.get(
                "qu_id_stock", user_input.get("qu_id")
            )
            new_product["qu_id_purchase"] = user_input.get(
                "qu_id_purchase",
                new_product["qu_id_stock"],
                # ...this unit is not really for parents, but will set as field is required
            )
            new_product["qu_id_consume"] = user_input.get(
                "qu_id_consume",
                new_product["qu_id_stock"],
                # ...this unit is not really for parents, but will set as field is required
            )
            new_product["qu_id_price"] = user_input.get(
                "qu_id_price",
                user_input.get("qu_id"),
                # TODO: clear this value if is Piece/Pack, since it is best with a unit for Liquid / Weight
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
                    "description",
                    new_product.get(
                        "description",
                        # (
                        #     # fallback to the lookup results
                        #     self.current_lookup.get("lookup_output")
                        # ),
                    ),
                )
                new_product["parent_product_id"] = user_input.get(
                    "parent_product_id", new_product["parent_product_id"]
                )

            # create product
            _LOGGER.info("user_input: %s", user_input)
            _LOGGER.info("new_product: %s", new_product)
            if errors:
                # schema = self.add_suggested_values_to_schema(schema, user_input)
                _LOGGER.warning("Input errors: %s", errors)
                self.current_form_args = {
                    "step_id": Step.SCAN_ADD_PRODUCT_PARENT,
                    "data_schema": schema,
                    "description_placeholders": plc,
                    "errors": errors,
                }
                _LOGGER.debug("FORM-ARGS: %s", self.current_form_args)
                return self.async_show_form(**self.current_form_args)

            # Create product
            product = await self._api_grocy.add_product(new_product)
            # TODO: check for success!
            _LOGGER.info("created prod: %s", product)
            self.current_parent = product

            if not self.current_product.get("parent_product_id"):
                # Update Product with the new Parent mapping
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
                # TODO: Check for success

                # update local cache with assumed changes
                self.current_product.update(product_updates)
        # Done with product, now continue with the Process work for the current barcode
        return await self.async_step_scan_queue(user_input=None)

    async def async_step_scan_add_product_barcode(
        self, user_input: dict[str, Any] = None
    ):
        """Handle input for adding product barcode."""
        errors: dict[str, str] = {}
        self.current_form_args = None

        code = self.current_barcode

        new_product: GrocyProduct = (self.current_product_stock_info or {}).get(
            "product"
        )

        # Handle input, for required fields
        if user_input is None:
            user_input = user_input or {}
            user_input["note"] = user_input.get("note", new_product["name"])
            # user_input["qu_id"] = str(
            #     user_input.get("qu_id", new_product["qu_id_purchase"])
            # )

            # if self.current_product_openfoodfacts is not None:
            #     q = self.current_product_openfoodfacts.get("product_quantity")
            #     qu = self.current_product_openfoodfacts.get("product_quantity_unit")
            #     if q and qu:
            #         # TODO: compare qu, against the defaulted "qu_id_purchase" or "qui_id_stock"
            #         # TODO: make conversion, if necessary...
            #         user_input["amount"] = q

            schema = GENERATE_CREATE_PRODUCT_BARCODESCHEMA(
                self._coordinator.data, user_input
            )
            self.add_suggested_values_to_schema(schema, user_input)
            _LOGGER.info("form 'add_barcode' user_input: %s", user_input)

            # ask for input...

            # aliases = self.current_lookup.get("product_aliases") or []
            aliases = (
                (self.current_lookup.get("product_aliases") or [])
                if self.current_lookup
                else (
                    [f"Matlåda: {self.current_recipe["name"]}"]
                    if self.current_recipe
                    else []
                )
            )
            plc = {
                "name": new_product.get("name"),
                "barcode": code,
                # "lookup_name": ica_fullname or off_fullname,
                "product_aliases": "\n".join(
                    [f"- {a.strip()}" for a in aliases if a]
                ),  # Format aliases as a Markdown-list
                "lookup_output": (self.current_lookup or {}).get("lookup_output"),
                # "product_matches": "\n".join(
                #     f"{p['name']}" for p in self.matching_products
                # ),
            }
            self.current_form_args = {
                "step_id": Step.SCAN_ADD_PRODUCT_BARCODE,
                "data_schema": schema,
                "description_placeholders": plc,
                "errors": errors,
            }
            _LOGGER.debug("FORM-ARGS: %s", self.current_form_args)
            return self.async_show_form(**self.current_form_args)

        # Input has been passed!
        br: GrocyProductBarcode = {
            "barcode": code,
            "note": user_input["note"],
            "product_id": new_product["id"],
            "qu_id": user_input.get("qu_id"),
            "shopping_location_id": user_input.get("shopping_location_id"),
            "amount": user_input.get("amount"),
            "row_created_timestamp": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        pcode = await self._api_grocy.add_product_barcode(br)
        _LOGGER.info("created prod_barcode: %s", pcode)
        # TODO: append barcode to product

        if self.scan_options.get("input_product_details_during_provision"):
            # Add more product info...
            return await self.async_step_scan_update_product_details(user_input=None)

        # Created product, check if create parent was requested...
        return await self.async_step_scan_add_product_parent(user_input=None)

    async def async_step_scan_update_product_details(
        self, user_input: dict[str, Any] = None
    ):
        errors: dict[str, str] = {}
        _LOGGER.info("form update-product: %s", user_input)
        masterdata: GrocyMasterData = self._coordinator.data
        product_stock_info: ExtendedGrocyProductStockInfo = (
            self.current_product_stock_info
        )
        product = product_stock_info["product"]

        show_form = user_input is None
        if user_input is None:
            user_input = user_input or {}


        def appendDefault(ui: dict[str, Any], key: str, suggestions: dict[str, Any]):
            val = ui.get(key, suggestions.get(key))
            if key not in [
                "should_not_be_frozen",
                "calories_per_100",
                "default_best_before_days",
                "default_best_before_days_after_open",
                "default_best_before_days_after_freezing",
                "default_best_before_days_after_thawing",
            ]:
                # if not part of exceptions, then set value in ´str´
                # Exceptions are most likley in ´int´
                val = str(val) if val is not None else None
            ui[key] = val

        _LOGGER.info("Original input: %s", user_input)
        appendDefault(user_input, "should_not_be_frozen", self.current_product)
        appendDefault(user_input, "default_consume_location_id", self.current_product)
        appendDefault(user_input, "default_best_before_days_after_freezing", self.current_product)
        appendDefault(user_input, "default_best_before_days_after_thawing", self.current_product)
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
            # Fill in from OpenFoodFacts
            unit = self.current_product_openfoodfacts.get("product_quantity_unit")
            if unit:
                for qq in filter(
                    lambda qu: qu.get("name") == unit,
                    masterdata["quantity_units"],
                ):
                    product_quantity_unit = qq["id"]
                    _LOGGER.warning("Unit: %s, QQ: %s", unit, qq)
                    product_quantity_unit_as_liquid = qq["name"] in [
                        "ml",
                        "cl",
                        "dl",
                        "l",
                        "L",
                    ]
                    product_quantity_unit_as_weight = qq["name"] in ["g", "hg", "kg"]
                if not user_input.get("qu_id"):
                    # TODO: find closest similiar Unit (example: g -> kg)
                    # product_quantity_unit =
                    pass

        if self.current_product_ica is not None:
            # TODO: fill in info from ICA...
            pass

        # TODO: fill in guess of QuantityUnit...

        kcal = user_input["calories_per_100"] = user_input.get("calories_per_100") or (
            self.current_product_openfoodfacts or {}
        ).get("nutriments", {}).get("energy_kcal_100g")
        if kcal:
            kcal = float(kcal)

        qu_id_product = user_input.get("qu_id_product", product_quantity_unit)
        skip_add_qu_conversions = False
        if user_input.get("qu_id_product"):
            qu_id_product = int(user_input.get("qu_id_product"))
        elif product_quantity_unit:
            qu_id_product = product_quantity_unit
        else:
            # Don't lookup resolved conversions
            skip_add_qu_conversions = True

        # Lookup Unit charasteristics
        if qu_id_product:
            for qq in filter(
                lambda qu: qu.get("id") == qu_id_product,
                masterdata["quantity_units"],
            ):
                _LOGGER.warning("Chosen unit: %s", qq)
                product_quantity_unit_as_liquid = qq["name"] in [
                    "ml",
                    "cl",
                    "dl",
                    "l",
                    "L",
                ]
                product_quantity_unit_as_weight = qq["name"] in ["g", "hg", "kg"]
                break

        if not skip_add_qu_conversions:
            # The looked up product quantity unit (of the Pack/Piece)
            if qu_id_product in [
                product.get("qu_id_stock"),
                product.get("qu_id_consume"),
                product.get("qu_id_purchase"),
                product.get("qu_id_price"),
            ]:
                # The product already references the ´product_quantity_unit´
                skip_add_qu_conversions = True
            else:
                conversions = await self._api_grocy.resolve_quantity_unit_conversions_for_product_id(
                    product["id"]
                )
                _LOGGER.warning("Convers: %s", conversions)
                # TODO: check if there already is a resolved conversion for those qu_id
                # TODO: if already exists then set ´skip_add_qu_conversions = True´

        if show_form:
            qu_id_product = user_input.get("qu_id_product", qu_id_product)
            if qu_id_product:
                user_input["qu_id_product"] = str(qu_id_product)
            user_input["product_quantity"] = user_input.get(
                "product_quantity", product_quantity
            )
            user_input["calories_per_100"] = user_input.get("calories_per_100", kcal)

            schema = GENERATE_UPDATE_PRODUCT_DETAILS_SCHEMA(
                self._coordinator.data, user_input, product
            )
            _LOGGER.info("schema: %s", schema)
            _LOGGER.info("form 'update_product' user_input: %s", user_input)

            schema = vol.Schema(schema)
            self.add_suggested_values_to_schema(schema, user_input)

            # aliases = self.current_lookup.get("product_aliases") or []
            aliases = (
                (self.current_lookup.get("product_aliases") or [])
                if self.current_lookup
                else (
                    [f"Matlåda: {self.current_recipe["name"]}"]
                    if self.current_recipe
                    else []
                )
            )
            plc = {
                "name": product.get("name"),
                "barcode": self.current_barcode,
                "product_aliases": "\n".join(
                    [f"- {a.strip()}" for a in aliases if a]
                ),  # Format aliases as a Markdown-list
                "lookup_output": (self.current_lookup or {}).get("lookup_output"),
                # "product_matches": "\n".join(
                #     f"{p['name']}" for p in self.matching_products
                # ),
            }
            self.current_form_args = {
                "step_id": Step.SCAN_UPDATE_PRODUCT_DETAILS,
                "data_schema": schema,
                "description_placeholders": plc,
                "errors": errors,
            }
            _LOGGER.debug("FORM-ARGS: %s", self.current_form_args)
            return self.async_show_form(**self.current_form_args)

        _LOGGER.info(
            "About to add conv: %s %s %s",
            product["qu_id_stock"],
            qu_id_product,
            product_quantity,
        )
        product_updates = {}
        if not skip_add_qu_conversions and qu_id_product and product_quantity:
            # TODO: create explicit product quantity unit conversion
            # Example Pack -> g
            conv: GrocyAddProductQuantityUnitConversion = {
                "from_qu_id": product["qu_id_stock"],  # Pack/Piece
                "to_qu_id": int(qu_id_product),
                "product_id": product["id"],
                "row_created_timestamp": dt.datetime.now().strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
                "factor": float(product_quantity),
            }
            await self._api_grocy.add_product_quantity_unit_conversion(conv)

            # Set price reference unit to kg/L
            if product_quantity_unit_as_liquid:
                product_updates["qu_id_price"] = (
                    masterdata["known_qu"].get("L", {}).get("id")
                )
            elif product_quantity_unit_as_weight:
                product_updates["qu_id_price"] = (
                    masterdata["known_qu"].get("kg", {}).get("id")
                )
            else:
                _LOGGER.warning("Unknown quantity unit type: %s", product_quantity_unit)

        if val := user_input.get("default_consume_location_id"):
            product_updates["default_consume_location_id"] = val
        if val := user_input.get("default_best_before_days_after_freezing"):
            product_updates["default_best_before_days_after_freezing"] = int(val)
        if val := user_input.get("default_best_before_days_after_thawing"):
            product_updates["default_best_before_days_after_thawing"] = int(val)

        # Since qu_id_stock is the whole container. Recalculate into the standard per100g
        # OpenFoodFacts stores the calories per 100g/100ml, calc into what the while container has
        gram_unit = masterdata["known_qu"].get("g")
        if product_quantity_unit_as_liquid:
            gram_unit = masterdata["known_qu"].get("ml")

        if kcal and gram_unit:
            kcal_per_gram = float(kcal) / 100
            # Convert Pack -> grams (since the ´product_quantity´ might be of a different unit)
            c: GrocyQuantityUnitConversionResult = (
                await self._coordinator.convert_quantity_for_product(
                    product["id"],
                    amount=1,  # 1 Pack
                    from_qu_id=int(product["qu_id_stock"]),  # Pack
                    to_qu_id=gram_unit["id"],  # grams or millilitres
                )
            )
            # TODO: handle `c is None`
            _LOGGER.warning(
                "Converted: %s %s -> %s %s",
                c["from_amount"],
                c["from_qu_name"],
                c["to_amount"],
                c["to_qu_name"],
            )
            grams_per_pack = c["to_amount"]
            kcal_per_pack = kcal_per_gram * grams_per_pack
            product_updates["calories"] = kcal_per_pack  # kcal/´qu_id_stock´

        if product_updates:
            # Update product with changed values
            _LOGGER.info("Will update product: #%s %s", product["id"], product_updates)
            await self._api_grocy.update_product(product["id"], product_updates)

            # update local cache with assumed changes
            self.current_product.update(product_updates)

        # Done with product, check if create parent was requested...
        return await self.async_step_scan_add_product_parent(user_input=None)

    async def async_step_scan_transfer_start(self, user_input: dict[str, Any] = None):
        """Handle input for choosing what Stock entry to transfer."""
        errors: dict[str, str] = {}
        _LOGGER.info("transfer-start: %s", user_input)
        _LOGGER.info("stock_entries: %s", self.current_stock_entries)

        if not self.current_product_stock_info:
            return self.async_abort(reason="No product info found during transfer!")
        if len(self.current_stock_entries) < 1:
            return self.async_abort(reason="No stock entries to transfer")

        if user_input is None and len(self.current_stock_entries) > 1:
            # Has matching product, display as a suggestion
            _LOGGER.warning("Existing stock entries: %s", self.current_stock_entries)
            schema = GENERATE_CHOOSE_EXISTING_STOCK_ENTRY(
                self._coordinator.data,
                self.current_product_stock_info["product"],
                self.current_stock_entries,
            )
            _LOGGER.info("schema: %s", schema)
            schema = vol.Schema(schema)

            # ask for input...
            return self.async_show_form(
                step_id=Step.SCAN_TRANSFER_START,
                data_schema=schema,
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
            # Select a single stock entry
            self.current_stock_entries = [stock_entry]
            _LOGGER.warning("CURRENT se: %s", self.current_stock_entries)
        return await self.async_step_scan_transfer_input(user_input=None)

    async def async_step_scan_transfer_input(self, user_input: dict[str, Any] = None):
        """Handle input for choosing how to transfer the Stock entry."""
        errors: dict[str, str] = {}
        _LOGGER.info("transfer-input: %s", user_input)
        _LOGGER.info("stock_entries: %s", self.current_stock_entries)

        if not self.current_product_stock_info:
            return self.async_abort(reason="No product info found during transfer!")
        if len(self.current_stock_entries) != 1:
            return self.async_abort(
                reason="Should only have one chosen stock entry to transfer"
            )

        # Handle input, for required fields
        if user_input is None:
            # Has matching product, display as a suggestion
            schema = GENERATE_TRANSFER_STOCK_ENTRY(
                self._coordinator.data,
                self.current_product_stock_info["product"],
                self.current_stock_entries[0],
            )
            _LOGGER.info("schema: %s", schema)
            schema = vol.Schema(schema)
            # ask for input...
            return self.async_show_form(
                step_id=Step.SCAN_TRANSFER_INPUT,
                data_schema=schema,
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

        # TODO: check for success!

        # Transfer has been complete...
        # remove it from queue, and then restart the queue...
        self.barcode_queue.pop(0)

        self.barcode_results.append(
            f"{product['name']} transferred to loc #{location_to_id}"
        )
        return await self.async_step_scan_queue(user_input=None)

    async def async_step_scan_process(self, user_input: dict[str, Any] = None):
        """Handle the scanned barcode (product exists)."""
        errors: dict[str, str] = {}
        schemas: VolDictType = {}

        code = self.current_barcode

        # Make sure that stock info is loaded...
        if self.current_product and not self.current_product_stock_info:
            _LOGGER.warning("Product stock was not loaded, loading it now!")
            self.current_product_stock_info = await self._api_grocy.get_stock_product_by_id(self.current_product["id"])
            self.current_product = (self.current_product_stock_info or {}).get("product")
        product = self.current_product or self.current_product_stock_info.get("product", {})

        # Handle input, for Price/BestBeforeInDays/shopping_location_id
        # TODO: Input default price from Recipe (cost of ingredients)
        price = user_input.get("price") if user_input else None
        bestBeforeInDays = (
            user_input.get("bestBeforeInDays", product.get("default_best_before_days"))
            if user_input
            else product.get("default_best_before_days")
        )
        shopping_location_id = (
            user_input.get("shopping_location_id") if user_input else None
        )

        in_purchase_mode = self.barcode_scan_mode in [SCAN_MODE.PURCHASE] or (
            self.barcode_scan_mode == SCAN_MODE.SCAN_BBUDDY
            and self.current_bb_mode
            == self._api_bbuddy.convert_scan_mode_to_bbuddy_mode(SCAN_MODE.PURCHASE)
        )
        if user_input is None and in_purchase_mode:
            # If is in a "Purchase"-context
            
            # TODO: If in Purchase context AND self.current_recipe, then add field for target to place "Matlådor", and how many portions that where produced...

            # Input for price
            if (price is None 
                and self.scan_options.get("input_price")
                and not self.current_recipe
            ):
                _LOGGER.info(
                    "Price input enabled: append schema field, value: %s", price
                )
                schemas.update(
                    {
                        vol.Optional(
                            "price", description={"suggested_value": price}
                        ): selector.TextSelector({"type": "text"})
                    }
                )
            # Input for bestBeforeInDays
            if self.scan_options.get("input_bestBeforeInDays"):
                _LOGGER.info(
                    "BestBeforeInDays input enabled: append schema field, value: %s",
                    bestBeforeInDays,
                )
                schemas.update(
                    {
                        vol.Optional(
                            "bestBeforeInDays",
                            description={"suggested_value": str(bestBeforeInDays)},
                        ): selector.TextSelector({"type": "text"})
                    }
                )
            # Input for shopping_location_id
            if (shopping_location_id is None
                and self.scan_options.get("input_shoppingLocationId")
                and not self.current_recipe
            ):
                _LOGGER.info(
                    "shoppingLocationId input enabled: append schema field, value: %s",
                    shopping_location_id,
                )
                masterdata: GrocyMasterData = self._coordinator.data
                shopping_locations = masterdata.get("shopping_locations")
                shopping_locations = sorted(
                    shopping_locations, key=lambda loc: loc["name"]
                )

                if self.current_product_stock_info.get("product_barcodes"):
                    # Check default store on Product barcode
                    for barcode in self.current_product_stock_info["product_barcodes"]:
                        _LOGGER.warning("Loop bc: %s", barcode)
                        if (
                            barcode.get("barcode", "").casefold()
                            == self.current_barcode.casefold()
                        ):
                            shopping_location_id = barcode.get("shopping_location_id")
                            if shopping_location_id:
                                break

                if self.current_product_stock_info and not shopping_location_id:
                    # Check default store on Product
                    shopping_location_id = self.current_product_stock_info.get(
                        "default_shopping_location_id",
                        self.current_product_stock_info["product"].get(
                            "default_shopping_location_id"
                        ),
                    )

                schemas.update(
                    {
                        vol.Optional(
                            "shopping_location_id",
                            description={
                                "suggested_value": str(shopping_location_id)
                                if shopping_location_id
                                else None
                            },
                        ): selector.SelectSelector(
                            selector.SelectSelectorConfig(
                                options=[
                                    selector.SelectOptionDict(
                                        value=str(loc["id"]),
                                        label=loc["name"],
                                    )
                                    for loc in shopping_locations
                                    # TODO: append a blank value too?
                                ],
                                mode=selector.SelectSelectorMode.DROPDOWN,
                                # TODO: Able to create new store? via ´custom_value=True,´
                            )
                        ),
                    }
                )

        if len(schemas) > 0:
            self.current_barcode_schema = vol.Schema(schemas)
            return self.async_show_form(
                step_id=Step.SCAN_PROCESS,
                data_schema=self.current_barcode_schema,
                errors=errors,
            )

        # Once product has been ensured to exist in Grocy, we can continue with BBuddy call
        # TODO: ignore BBuddy call if scan-mode is "lookup-barcode" or "provision-barcode"
        request = {
            "barcode": str(code),
        }
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
                "Setting BBuddy mode to: %s (%s)", bb_mode, self.barcode_scan_mode
            )
            await self._api_bbuddy.set_mode(bb_mode)
            self.current_bb_mode = bb_mode

        try:
            _LOGGER.info("SCAN-REQ: %s", json.dumps(request))
            if in_purchase_mode and request.get("shopping_location_id"):
                # Workaround for being able to persist with Store, call Grocy directly
                if days := request.get("bestBeforeInDays"):
                    d = dt.datetime.now() + dt.timedelta(days=days)
                    request["best_before_date"] = d.strftime("%Y-%m-%d")
                    del request["bestBeforeInDays"]
                request["transaction_type"] = "purchase"
                request["amount"] = (
                    1  # TODO: check barcode buddy current quantity context
                    # TODO: introduce a field for manual input during scan (default to Barcode amount, then to 1). If not able to fetch override from BBuddy
                )
                product_id = self.current_product_stock_info["product"]["id"]
                request.pop("barcode", None)  # Instead go by ´product_id´
                response = await self._api_grocy.add_stock_product(product_id, request)
                # response = ""   # TODO: set based on response from Grocy
            else:
                # Call Barcode Buddy scan
                # TODO: make Barcode Buddy obsolete? Instead do everything via Grocy API?. Gives more control, and cuts of middlehand. But looses the BBuddy UI and it's contextual settings.
                response = await self._api_bbuddy.post_scan(request)
                # TODO: handle responses with HTML-tags (warning/error messages)
            _LOGGER.info("SCAN-RESP: %s", response)

            # if success, then remove from queue, and re-run this method again
            self.barcode_queue.pop(0)

            # TODO: handle responses with HTML-tags (warning/error messages)
            self.barcode_results.append(str(response))

            # Re-run process method until queue is empty...
            return await self.async_step_scan_queue(user_input=None)
        except BaseException as be:
            # if error, then display error and give chance to edit or retry, or even skip?
            _LOGGER.error("BB-Scan excpt: %s", be)
            errors["Exception"] = be
            return self.async_show_form(
                step_id=Step.SCAN_PROCESS,
                data_schema=self.current_barcode_schema,
                errors=errors,
            )

    @staticmethod
    def fill_schema_defaults(
        data_schema: vol.Schema,
        options: dict[str, Any],
    ) -> vol.Schema:
        """Make a copy of the schema with suggested values set to saved options."""
        schema = {}
        for key, val in data_schema.schema.items():
            new_key = key
            if key in options and isinstance(key, vol.Marker):
                if (
                    isinstance(key, vol.Optional)
                    and callable(key.default)
                    and key.default()
                ):
                    new_key = vol.Optional(key.schema, default=options.get(key))  # type: ignore
                elif "suggested_value" not in (new_key.description or {}):
                    new_key = copy.copy(key)
                    new_key.description = {"suggested_value": options.get(key)}  # type: ignore
            schema[new_key] = val
        return vol.Schema(schema)


def GENERATE_STEP_SCAN_START_SCHEMA(scan_mode: SCAN_MODE) -> vol.Schema:
    bbuddy_mode_str = scan_mode.name if scan_mode is not None else "Unknown"
    return vol.Schema(
        {
            vol.Optional(
                "mode",
                description={
                    "suggested_value": SCAN_MODE.SCAN_BBUDDY
                },  # During DEV....
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        selector.SelectOptionDict(
                            value=SCAN_MODE.SCAN_BBUDDY,
                            label=f"Barcode Buddy ({bbuddy_mode_str})",
                        ),
                        selector.SelectOptionDict(
                            value=SCAN_MODE.CONSUME, label="Consume"
                        ),
                        selector.SelectOptionDict(
                            value=SCAN_MODE.CONSUME_SPOILED, label="Consume (Spoiled)"
                        ),
                        selector.SelectOptionDict(
                            value=SCAN_MODE.CONSUME_ALL, label="Consume (All)"
                        ),
                        selector.SelectOptionDict(
                            value=SCAN_MODE.PURCHASE, label="Purchase / Produce"
                        ),
                        selector.SelectOptionDict(
                            value=SCAN_MODE.TRANSFER, label="Transfer"
                        ),  # TODO: only add option if has more than 1 locations setup
                        selector.SelectOptionDict(value=SCAN_MODE.OPEN, label="Open"),
                        selector.SelectOptionDict(
                            value=SCAN_MODE.INVENTORY, label="Inventory"
                        ),
                        selector.SelectOptionDict(
                            value=SCAN_MODE.ADD_TO_SHOPPING_LIST,
                            label="Add to Shopping list",
                        ),
                        # selector.SelectOptionDict(    # merge with Inventory-action
                        #     value="lookup-barcode",
                        #     label="Lookup"
                        # ),
                        selector.SelectOptionDict(
                            value=SCAN_MODE.PROVISION, label="Provision barcode"
                        ),
                    ],
                    # translation_key="scan_mode",
                    mode=selector.SelectSelectorMode.LIST,
                    multiple=False,
                )
            ),
            vol.Required(
                "barcodes",
                description={"suggested_value": "4011800420413"},  # During DEV...
            ): selector.TextSelector({"type": "text", "multiline": True}),
        }
    )


def GENERATE_CHOOSE_EXISTING_PRODUCT_SCHEMA(
    masterdata: GrocyMasterData,
    suggested_products: list[GrocyProduct],
    suggested_values: dict[str, str] = {},
    lookup: BarcodeLookup | None = None,
    aliases: list[str] | None = None,
    allow_parent: bool = False,
) -> VolDictType:
    child_products = [
        prod for prod in masterdata["products"] if prod["parent_product_id"]
    ]
    # child_product_ids = [prod["id"] for prod in child_products]
    parent_product_ids = [prod["parent_product_id"] for prod in child_products]
    # TODO: NOTE CURRENT FLAW/FEATURE: If a product is not already a Parent, then cannot be chosen to be come a parent (actually logical to prevent children from becoming ones). Not an issue if ALL parents are provisioned via this flow...
    parent_products = [
        prod for prod in masterdata["products"] if prod["id"] in parent_product_ids
    ]

    suggested_product_ids = [prod["id"] for prod in suggested_products]
    non_suggested_prods = [
        prod
        for prod in masterdata["products"]
        if prod["id"] not in suggested_product_ids
        and prod["id"] not in parent_product_ids
    ]
    non_suggested_prods.sort(key=lambda product: product["name"])

    product_options: list[GrocyProduct] = []
    product_options = product_options + suggested_products
    product_options = product_options + non_suggested_prods

    prods = [
        selector.SelectOptionDict(
            value=str(prod["id"]),
            label=prod["name"],
        )
        for prod in product_options
        if prod["active"] == 1
    ]

    selected_product_id = ""
    aliases = aliases or (lookup or {}).get("product_aliases", [])
    if len(suggested_products) == 0 and len(aliases) > 0:
        # No suggested existing products
        # Check if has a name suggestion...
        selected_product_id = aliases[0]
    elif len(suggested_products) > 0:
        prods.insert(
            len(suggested_products),
            selector.SelectOptionDict(
                # value="-1", label="[CHOOSE FROM SUGGESTIONS / ENTER NAME]"
                value="-1",
                label=f"\t[{len(suggested_products)} SUGGESTIONS ABOVE]",
            ),
        )
        if len(suggested_products) == 1:
            # Only has a single suggestion, then pre-select it
            selected_product_id = str(
                suggested_values.get("product_id", suggested_products[0]["id"])
            )
        else:
            # If has more suggestions, then pre-select "CREATE-NEW"
            selected_product_id = "-1"

    # TODO: rewrite this Schema to have radio button for Create / Create child? / Map existing
    # -> barcode

    # Form1:
    # dropdown: choose matching product / or create new
    # radio: "choose existing above" / "create new"  / "create new with parent"
    # dropdown: "new parent" / or choose from existing "parent-node"

    schemas: VolDictType = {}
    schemas.update(
        {
            vol.Required(
                "product_id",
                description={
                    "suggested_value": selected_product_id,
                },
                # default=selected_product_id,
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=prods,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                    multiple=False,
                    custom_value=True,
                )
            ),
        }
    )
    if allow_parent:
        schemas.update(
            {
                vol.Optional(
                    "parent_product",
                    description={
                        "suggested_value": suggested_values.get("parent_product"),
                        # TODO: ...or if product_alias matches another product WHICH has a parent, then suggest that parent
                    },
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        # List existing Parent products
                        options=[
                            selector.SelectOptionDict(
                                value=str(prod["id"]), label=prod["name"]
                            )
                            for prod in parent_products
                            if prod["active"] == 1
                        ],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                        multiple=False,
                        custom_value=True,  # allows for creating a new parent
                    )
                ),
            }
        )
    return schemas


def GENERATE_CHOOSE_EXISTING_STOCK_ENTRY(
    masterdata: GrocyMasterData,
    product: GrocyProduct,
    suggested_stockentries: list[GrocyStockEntry],
    suggested_values: dict[str, str] = {},
) -> VolDictType:
    suggested_stockentry_ids = [e["id"] for e in suggested_stockentries]

    qu: GrocyQuantityUnit = None
    for qq in filter(
        lambda p: p["id"] == product["qu_id_stock"],
        masterdata["quantity_units"],
    ):
        qu = qq
        break

    options = [
        selector.SelectOptionDict(
            value=str(e["id"]),
            label=f"{product['name']} {e['amount']} {qu['name_plural'] if e['amount'] > 1 else qu['name']}, due: {e['best_before_date']}",
            # TODO: append current location name
        )
        for e in suggested_stockentries
    ]

    selected_stock_entry_id: str = None
    if len(suggested_stockentry_ids) > 0:
        selected_stock_entry_id = suggested_values.get(
            "stock_entry_id", suggested_stockentry_ids[0]
        )
    selected_stock_entry_id = str(selected_stock_entry_id)

    schemas: VolDictType = {}
    schemas.update(
        {
            vol.Required(
                "stock_entry_id",
                description={
                    "suggested_value": selected_stock_entry_id,
                },
                default=selected_stock_entry_id,
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=options,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                    multiple=False,
                )
            ),
        }
    )
    return schemas


def GENERATE_TRANSFER_STOCK_ENTRY(
    masterdata: GrocyMasterData,
    product: GrocyProduct,
    suggested_stockentry: GrocyStockEntry,
    suggested_values: dict[str, str] = None,
) -> VolDictType:
    locations = [
        loc
        for loc in masterdata["locations"]
        # can't transfer to same target
        if loc["id"] != suggested_stockentry["location_id"]
        # ...adhere to `should_not_be_frozen`-attribute
        and (product["should_not_be_frozen"] == 0 or loc["is_freezer"] == 0)
    ]
    locations.sort(key=lambda loc: loc["name"])

    suggested_values = suggested_values or {
        "amount": suggested_stockentry["amount"],  # default to move all
        "location_to_id": str(locations[0]["id"]) if len(locations) > 0 else None,
    }

    schemas: VolDictType = {}
    if suggested_stockentry["amount"] > 1:
        # Only if has a choice, whether to split the stock entry
        schemas.update(
            {
                vol.Required(
                    "amount",
                    description={
                        "suggested_value": suggested_values.get("amount"),
                    },
                    default=suggested_values.get("amount"),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        mode=selector.NumberSelectorMode.SLIDER,
                        step=product.get("quick_consume_amount", 1)
                        or 1,  # follow consume amount for how many quantities can be transfered
                        min=product.get("quick_consume_amount", 1)
                        or 1,  # transfer at least 1
                        max=suggested_stockentry[
                            "amount"
                        ],  # maxium allowed to move all
                    )
                ),
            }
        )
    schemas.update(
        {
            vol.Required(
                "location_to_id",
                description={
                    "suggested_value": suggested_values.get("location_to_id"),
                },
                default=suggested_values.get("location_to_id"),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        selector.SelectOptionDict(
                            value=str(loc["id"]),
                            label=loc["name"],
                        )
                        for loc in locations
                    ],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                    multiple=False,
                )
            ),
        }
    )
    return schemas


def GENERATE_CREATE_PRODUCT_SCHEMA(
    masterdata: GrocyMasterData,
    suggested_values: dict[str, str],
    creating_parent: bool = False,
) -> VolDictType:
    locs = [
        selector.SelectOptionDict(
            value=str(loc["id"]),
            label=loc["name"],
        )
        for loc in masterdata["locations"]
    ]
    qu = [
        selector.SelectOptionDict(
            value=str(qu["id"]),
            label=qu["name"],
        )
        for qu in masterdata["quantity_units"]
    ]

    schemas: VolDictType = {}
    schemas.update(
        {
            vol.Required(
                "name",
                description={
                    "suggested_value": suggested_values.get("name"),
                    # TODO: render as listbox with suggested values, but allow for custom text?
                    # Example: Mango / Mango Fryst 250g ICA / Fryst mango
                },
            ): selector.TextSelector({"type": "text"})
        }
    )
    if not creating_parent:
        schemas.update(
            {
                vol.Required(
                    "location_id",
                    description={
                        "suggested_value": suggested_values.get("location_id"),
                    },
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=locs,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                        multiple=False,
                    )
                ),
            }
        )
        schemas.update(
            {
                vol.Required(
                    "should_not_be_frozen",
                    default=suggested_values.get("should_not_be_frozen", False),
                ): selector.BooleanSelector()
            }
        )
        schemas.update(
            {
                vol.Optional(
                    "default_best_before_days",
                    description={
                        "suggested_value": suggested_values.get(
                            "default_best_before_days"
                        ),
                    },
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        mode=selector.NumberSelectorMode.BOX, step=1
                    )
                ),
            }
        )
        schemas.update(
            {
                vol.Optional(
                    "default_best_before_days_after_open",
                    description={
                        "suggested_value": suggested_values.get(
                            "default_best_before_days_after_open"
                        ),
                    },
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        mode=selector.NumberSelectorMode.BOX, step=1
                    )
                ),
            }
        )
    schemas.update(
        {
            vol.Required(
                "qu_id_stock",
                description={
                    "suggested_value": suggested_values.get(
                        "qu_id_stock", suggested_values.get("qu_id")
                    ),
                },
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=qu,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                    multiple=False,
                )
            ),
        }
    )
    if not creating_parent:
        schemas.update(
            {
                vol.Required(
                    "qu_id_purchase",
                    description={
                        "suggested_value": suggested_values.get(
                            "qu_id_purchase", suggested_values.get("qu_id")
                        ),
                    },
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=qu,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                        multiple=False,
                    )
                ),
            }
        )
        schemas.update(
            {
                vol.Required(
                    "qu_id_consume",
                    description={
                        "suggested_value": suggested_values.get(
                            "qu_id_consume", suggested_values.get("qu_id")
                        ),
                    },
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=qu,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                        multiple=False,
                    )
                ),
            }
        )
    schemas.update(
        {
            vol.Required(
                "qu_id_price",
                description={
                    "suggested_value": suggested_values.get(
                        "qu_id_price", suggested_values.get("qu_id")
                    ),
                },
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=qu,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                    multiple=False,
                )
            ),
        }
    )
    return schemas


def GENERATE_UPDATE_PRODUCT_DETAILS_SCHEMA(
    masterdata: GrocyMasterData, suggested_values: dict[str, str], product: GrocyProduct
) -> VolDictType:
    locations = [
        loc
        for loc in masterdata["locations"]
        # adhere to `should_not_be_frozen`-attribute
        if product["should_not_be_frozen"] == 0 or loc["is_freezer"] == 0
    ]
    locations.sort(key=lambda loc: loc["name"])
    locs = [
        selector.SelectOptionDict(
            value=str(loc["id"]),
            label=loc["name"],
        )
        for loc in locations
    ]

    qu = [
        selector.SelectOptionDict(
            value=str(qu["id"]),
            label=qu["name"],
        )
        for qu in masterdata["quantity_units"]
    ]
    qu.insert(0, selector.SelectOptionDict(
        value="",
        label=""
    ))

    schemas: VolDictType = {}
    schemas.update(
        {
            vol.Optional(
                "default_consume_location_id",
                description={
                    "suggested_value": suggested_values.get("default_consume_location_id"),
                },
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=locs,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                    multiple=False,
                )
            ),
        }
    )
    schemas.update(
        {
            vol.Optional(
                "product_quantity",
                description={
                    "suggested_value": suggested_values.get("product_quantity"),
                },
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    mode=selector.NumberSelectorMode.BOX, step=1
                )
            ),
        }
    )
    schemas.update(
        {
            vol.Optional(
                "qu_id_product",
                description={
                    "description": "What quantity unit does the product package have?",
                    "suggested_value": suggested_values.get("qu_id_product"),
                },
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=qu,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                    multiple=False,
                )
            ),
        }
    )
    schemas.update(
        {
            vol.Optional(
                "calories_per_100",
                description={
                    "suggested_value": suggested_values.get("calories_per_100"),
                },
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    mode=selector.NumberSelectorMode.BOX, step=1
                )
            ),
        }
    )
    if not product.get("should_not_be_frozen", 0):
        schemas.update(
            {
                vol.Optional(
                    "default_best_before_days_after_freezing",
                    description={
                        "suggested_value": suggested_values.get(
                            "default_best_before_days_after_freezing"
                        ),
                    },
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        mode=selector.NumberSelectorMode.BOX, step=1
                    )
                ),
            }
        )
        schemas.update(
            {
                vol.Optional(
                    "default_best_before_days_after_thawing",
                    description={
                        "suggested_value": suggested_values.get(
                            "default_best_before_days_after_thawing"
                        ),
                    },
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        mode=selector.NumberSelectorMode.BOX, step=1
                    )
                ),
            }
        )
    return schemas


def GENERATE_CREATE_PRODUCT_BARCODESCHEMA(
    masterdata: GrocyMasterData, suggested_values: dict[str, str]
) -> vol.Schema:
    shopping_locations = [
        selector.SelectOptionDict(
            value=str(store["id"]),
            label=store["name"],
        )
        for store in sorted(
            masterdata["shopping_locations"], key=lambda loc: loc["name"]
        )
    ]
    qu = [
        selector.SelectOptionDict(
            value=str(qu["id"]),
            label=qu["name"],
        )
        for qu in masterdata["quantity_units"]
    ]

    schemas: VolDictType = {}
    schemas.update(
        {
            vol.Optional(
                "note",
                description={
                    "suggested_value": suggested_values.get("note"),
                },
            ): selector.TextSelector({"type": "text"})
        }
    )
    schemas.update(
        {
            vol.Optional(
                "shopping_location_id",
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=shopping_locations,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                    multiple=False,
                )
            ),
        }
    )
    schemas.update(
        {
            vol.Optional(
                "qu_id",
                description={
                    "suggested_value": suggested_values.get(
                        "qu_id", suggested_values.get("qu_id_purchase")
                    ),
                },
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=qu,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                    multiple=False,
                )
            ),
        }
    )
    schemas.update(
        {
            vol.Optional(
                "amount",
                description={
                    "suggested_value": suggested_values.get("amount"),
                },
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(mode=selector.NumberSelectorMode.BOX)
            )
        }
    )
    return vol.Schema(schemas)
