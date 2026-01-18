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
    ExtendedGrocyProductStockInfo,
    GrocyAddProductQuantityUnitConversion,
    GrocyProduct,
    GrocyProductBarcode,
    GrocyMasterData,
    GrocyQuantityUnit,
    GrocyQuantityUnitConversionResult,
    GrocyStockEntry,
    OpenFoodFactsProduct,
)

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
    }
    current_bb_mode: int = -1
    barcode_scan_mode: str = None
    barcode_queue: list[str] = []
    barcode_results: list[str] = []

    current_barcode: str = None
    current_barcode_schema: vol.Schema = None
    current_product_stock_info: ExtendedGrocyProductStockInfo | None = None
    current_product_openfoodfacts: OpenFoodFactsProduct | None = None
    matching_products: list[GrocyProduct] = []
    current_stock_entries: list[GrocyStockEntry] = []

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
                # data_schema=STEP_SCAN_START,
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
            # todo: Add result info to message...
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

        code = current_barcode.strip().strip(",").strip()
        self.current_barcode = code
        self.current_product_stock_info = None

        if self.barcode_scan_mode == SCAN_MODE.SCAN_BBUDDY:
            bb_mode = await self._api_bbuddy.get_mode()
            if bb_mode is not None and bb_mode >= 0:
                _LOGGER.info("BBuddy mode is: %s (%s)", bb_mode, self.barcode_scan_mode)
                self.current_bb_mode = bb_mode
        else:
            self.current_bb_mode = None

        product: ExtendedGrocyProductStockInfo = None
        if self.barcode_scan_mode == SCAN_MODE.PROVISION or (
            self.barcode_scan_mode != SCAN_MODE.INVENTORY
            and self.barcode_scan_mode != SCAN_MODE.QUANTITY
        ):
            if "BBUDDY-" not in code:
                # Not a BarcodeBuddy code...
                # Lookup product in Grocy
                try:
                    product_stock_info = (
                        await self._api_grocy.get_stock_product_by_barcode(code)
                    )
                    product: GrocyProduct = (product_stock_info or {}).get("product")
                    self.current_product_stock_info = product_stock_info
                    self.matching_products: list[GrocyProduct] = []
                    _LOGGER.info(
                        "GrocyProduct lookup: %s",
                        self.current_product_stock_info,
                    )

                    if (
                        product
                        and product.get("id")
                        and self.barcode_scan_mode == SCAN_MODE.TRANSFER
                    ):
                        stock_entries = (
                            await self._api_grocy.get_stock_entries_by_product_id(
                                product["id"]
                            )
                        )
                        self.current_stock_entries = stock_entries
                        return await self.async_step_scan_transfer_start(
                            user_input=None
                        )

                    if not product:
                        # New barcode (Not provisioned in Grocy)
                        _LOGGER.info(
                            "New product, do a lookup against OpenFoodFacts: %s", code
                        )
                        # todo: Lookup in other providers...
                        self.current_product_openfoodfacts: (
                            OpenFoodFactsProduct | None
                        ) = await self._coordinator.get_product_from_open_food_facts(
                            code
                        )
                        _LOGGER.info(
                            "OpenFoodFacts product: %s",
                            self.current_product_openfoodfacts,
                        )
                        self.current_product_ica: dict = {}
                        # _LOGGER.info("ICA product: %s", self.current_product_ica)

                        masterdata: GrocyMasterData = self._coordinator.data
                        for matching_product in filter(
                            lambda p: (
                                (
                                    self.current_product_openfoodfacts is not None
                                    and p["name"].casefold()
                                    == self.current_product_openfoodfacts.get(
                                        "product_name", ""
                                    ).casefold()
                                )
                                or (
                                    self.current_product_ica is not None
                                    and p["name"].casefold()
                                    == self.current_product_ica.get(
                                        "name", ""
                                    ).casefold()
                                )
                            ),
                            masterdata["products"],
                        ):
                            # todo: also loop through ProductBarcode notes
                            _LOGGER.info("Match: %s", matching_product)
                            self.matching_products.append(matching_product)

                        # always give option to map to an existing product...
                        return await self.async_step_scan_match_to_product(
                            user_input=None
                        )
                except BaseException as be:
                    _LOGGER.error("Get product excep: %s", be)
                    errors["Exception"] = be
                    raise be

        if self.barcode_scan_mode == SCAN_MODE.PROVISION:
            # Mode is to simply ensure product/barcode exists
            # remove from queue, and then restart the queue...
            self.barcode_queue.pop(0)

            p = (product or {}).get("product") or (
                self.current_product_stock_info or {}
            ).get("product")
            _LOGGER.info("Provisioned: %s", p)
            self.barcode_results.append(f"{code} maps to {p['name']}")
            return await self.async_step_scan_queue(user_input=None)

        # Proceed with BarcodeBuddy processing
        return await self.async_step_scan_process(user_input=None)

    async def async_step_scan_match_to_product(self, user_input: dict[str, Any] = None):
        """Handle input for adding barcode to a product."""
        errors: dict[str, str] = {}
        _LOGGER.info("match-product: %s", user_input)
        _LOGGER.info("matches: %s", self.matching_products)

        code = self.current_barcode

        if (
            self.current_product_openfoodfacts is None
            and self.current_product_ica is None
        ):
            # todo: Not found in other providers, then show input's for manual registration?
            _LOGGER.error("No product info found!: %s", code)
            # errors["NoProduct"] = "No product found!"
            # return self.async_show_form(
            #     step_id=Step.SCAN_ADD_PRODUCT,
            #     data_schema=self.current_barcode_schema,
            #     errors=errors,
            # )
            return self.async_step_scan_add_product(user_input=None)

        # Handle input, for required fields
        if user_input is None:
            # Has matching product, display as a suggestion
            _LOGGER.warning("Matching products: %s", self.matching_products)
            schema = GENERATE_CHOOSE_EXISTING_PRODUCT_SCHEMA(
                self._coordinator.data,
                self.matching_products,
            )
            _LOGGER.info("schema: %s", schema)

            schema = vol.Schema(schema)

            # ask for input...
            return self.async_show_form(
                step_id=Step.SCAN_MATCH_PRODUCT,
                data_schema=schema,
                errors=errors,
            )

        # Input has been passed!
        if user_input.get("product_id") and user_input["product_id"] != "-1":
            # A specific product was chosen, use that instead of creation...
            _LOGGER.info("exist_products: %s", self.matching_products)
            self.current_product_stock_info = (
                await self._api_grocy.get_stock_product_by_id(
                    int(user_input["product_id"])
                )
            )
        else:
            # Create a new product
            _LOGGER.info("no-product-id: %s", user_input)
            return await self.async_step_scan_add_product(user_input=None)

        return await self.async_step_scan_add_product_barcode(user_input=None)

    async def async_step_scan_add_product(self, user_input: dict[str, Any] = None):
        """Handle input for adding a new product."""
        errors: dict[str, str] = {}
        masterdata: GrocyMasterData = self._coordinator.data
        _LOGGER.info("add-product: %s", user_input)

        # code = current_barcode.strip().strip(",").strip()
        # code = user_input["code"]
        code = self.current_barcode

        new_product: GrocyProduct = {}

        show_form = user_input is None
        if user_input is None:
            user_input = user_input or {}

        # # # New barcode (Not provisioned in Grocy)
        # # # todo: Lookup in other providers...
        # # self.current_product_openfoodfacts = (
        # #     await self._coordinator.get_product_from_open_food_facts(code)
        # # )
        # # _LOGGER.info("OpenFoodFacts product: %s", self.current_product_openfoodfacts)
        # # self.current_product_ica: dict = {}
        # # # _LOGGER.info("OpenFoodFacts product: %s", self.current_product_ica)

        if (
            self.current_product_openfoodfacts is None
            and self.current_product_ica is None
        ):
            # Not found in other providers, then will show empty input for manual registration
            _LOGGER.warning("No product info found from providers, code: %s", code)
            # errors["NoProduct"] = "No product found!"
            # return self.async_show_form(
            #     step_id=Step.SCAN_ADD_PRODUCT,
            #     data_schema=self.current_barcode_schema,
            #     errors=errors,
            # )

        user_input = user_input or {}
        # self.matching_product = None

        def format_off_name(off_product: OpenFoodFactsProduct) -> str:
            brand = off_product.get("brand_owner") or (
                (off_product.get("brands") or "").split(",")[0].strip()
            )
            product_name = (off_product.get("product_name") or "").strip()
            quantity = (off_product.get("quantity") or "").strip()

            off_fullname_parts: list[str] = [
                part for part in (brand, product_name, quantity) if part
            ]
            off_fullname = " - ".join(off_fullname_parts)
            _LOGGER.debug("Parsed product name: %s from: %s", off_fullname, off_product)
            return off_fullname

        off_fullname = (
            format_off_name(self.current_product_openfoodfacts)
            if self.current_product_openfoodfacts is not None
            else None
        )

        if self.current_product_openfoodfacts is not None:
            # Fill in from OpenFoodFacts
            user_input["name"] = (
                user_input.get("name")
                or off_fullname
                or self.current_product_openfoodfacts["product_name"]
            )
            unit = self.current_product_openfoodfacts.get("product_quantity_unit")
            if unit:
                for qq in filter(
                    lambda qu: qu.get("name") == unit,
                    masterdata["quantity_units"],
                ):
                    # todo: replace this ´product_quantity_unit ´suggestion, with Pack/Piece suggestion
                    # user_input["qu_id"] = str(qq["id"])
                    _LOGGER.warning("Unit: %s, QQ: %s", unit, qq)
                if not user_input.get("qu_id"):
                    # todo: find closest similiar Unit (example: g -> kg)
                    pass
            # todo: fill in guess of QuantityUnit...

            if self.current_product_ica is not None:
                # todo: fill in info from ICA...
                pass

        if show_form:
            schema: VolDictType = None
            schema = GENERATE_CREATE_PRODUCT_SCHEMA(masterdata, user_input)

            _LOGGER.info("schema: %s", schema)
            _LOGGER.info("form 'add_product' user_input: %s", user_input)

            schema = vol.Schema(schema)
            self.add_suggested_values_to_schema(schema, user_input)

            # ask for input...
            return self.async_show_form(
                step_id=Step.SCAN_ADD_PRODUCT,
                data_schema=schema,
                errors=errors,
            )

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
            new_product["description"] = user_input.get(
                "description",
                # fallback to a formatted name from OpenFoodFacts
                off_fullname,
            )
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

            if val := user_input.get("default_best_before_days"):
                new_product["default_best_before_days"] = int(val)
            if val := user_input.get("default_best_before_days_after_open"):
                new_product["default_best_before_days_after_open"] = int(val)
            new_product["qu_id_purchase"] = user_input.get(
                "qu_id_purchase", user_input.get("qu_id")
            )
            new_product["qu_id_stock"] = user_input.get(
                "qu_id_stock", user_input.get("qu_id")
            )
            new_product["qu_id_price"] = user_input.get(
                "qu_id_price", user_input.get("qu_id")
            )
            new_product["qu_id_consume"] = user_input.get(
                "qu_id_consume", user_input.get("qu_id")
            )
            new_product["row_created_timestamp"] = dt.datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            )

            # create product
            _LOGGER.info("user_input: %s", user_input)
            _LOGGER.info("new_product: %s", new_product)
            if errors:
                schema: VolDictType = None
                schema = GENERATE_CREATE_PRODUCT_SCHEMA(masterdata, user_input)
                schema = vol.Schema(schema)
                self.add_suggested_values_to_schema(schema, user_input)
                _LOGGER.warning("Input errors: %s", errors)
                return self.async_show_form(
                    step_id=Step.SCAN_ADD_PRODUCT,
                    data_schema=schema,
                    errors=errors,
                )

            product = await self._api_grocy.add_product(new_product)
            _LOGGER.info("created prod: %s", product)
            # todo: check for success!
            self.current_product_stock_info = (
                await self._api_grocy.get_stock_product_by_id(product["id"])
            )

        return await self.async_step_scan_add_product_barcode(user_input=None)

    async def async_step_scan_add_product_barcode(
        self, user_input: dict[str, Any] = None
    ):
        """Handle input for adding product barcode."""
        errors: dict[str, str] = {}

        # code = current_barcode.strip().strip(",").strip()
        # code = user_input["code"]
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
            #         # todo: compare qu, against the defaulted "qu_id_purchase" or "qui_id_stock"
            #         # todo: make conversion, if necessary...
            #         user_input["amount"] = q

            schema = GENERATE_CREATE_PRODUCT_BARCODESCHEMA(
                self._coordinator.data, user_input
            )
            self.add_suggested_values_to_schema(schema, user_input)
            _LOGGER.info("form 'add_barcode' user_input: %s", user_input)

            # ask for input...
            return self.async_show_form(
                step_id=Step.SCAN_ADD_PRODUCT_BARCODE,
                data_schema=schema,
                errors=errors,
            )

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
        # todo: append barcode to product

        if self.scan_options.get("input_product_details_during_provision"):
            # Add more product info...
            return await self.async_step_scan_update_product_details(user_input=None)

        # created product, now re-run process for same barcode
        # todo: in-future this could be merged to same process-work (avoid extra form)
        return await self.async_step_scan_queue(user_input=None)

    async def async_step_scan_update_product_details(
        self, user_input: dict[str, Any] = None
    ):
        errors: dict[str, str] = {}
        _LOGGER.info("update-product: %s", user_input)
        masterdata: GrocyMasterData = self._coordinator.data
        product_stock_info: ExtendedGrocyProductStockInfo = (
            self.current_product_stock_info
        )
        product = product_stock_info["product"]

        show_form = user_input is None
        if user_input is None:
            user_input = user_input or {}

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
                    # todo: find closest similiar Unit (example: g -> kg)
                    # product_quantity_unit =
                    pass

        if self.current_product_ica is not None:
            # todo: fill in info from ICA...
            pass

        # todo: fill in guess of QuantityUnit...

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
                # todo: check if there already is a resolved conversion for those qu_id
                # todo: if already exists then set ´skip_add_qu_conversions = True´

        if show_form:
            user_input["qu_id_product"] = str(
                user_input.get("qu_id_product", qu_id_product)
            )
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

            return self.async_show_form(
                step_id=Step.SCAN_UPDATE_PRODUCT_DETAILS,
                data_schema=schema,
                errors=errors,
            )

        _LOGGER.info(
            "About to add conv: %s %s %s",
            product["qu_id_stock"],
            qu_id_product,
            product_quantity,
        )
        product_updates = {}
        if not skip_add_qu_conversions and qu_id_product and product_quantity:
            # todo: create explicit product quantity unit conversion
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

        # Done with product, now continue with the Process work for the current barcode
        return await self.async_step_scan_queue(user_input=None)

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

        # todo: check for success!

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
        product = self.current_product_stock_info.get("product", {})

        # Handle input, for Price/BestBeforeInDays
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

            # Input for price
            if price is None and self.scan_options.get("input_price"):
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
            if shopping_location_id is None and self.scan_options.get(
                "input_shoppingLocationId"
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
                                    # todo: append a blank value too?
                                ],
                                mode=selector.SelectSelectorMode.DROPDOWN,
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
        # todo: ignore BBuddy call if scan-mode is "lookup-barcode" or "provision-barcode"
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
                    1  # todo: check barcode buddy current quantity context
                )
                product_id = self.current_product_stock_info["product"]["id"]
                request.pop("barcode", None)  # Instead go by ´product_id´
                response = await self._api_grocy.add_stock_product(product_id, request)
                # response = ""   # todo: set based on response from Grocy
            else:
                # Call Barcode Buddy scan
                response = await self._api_bbuddy.post_scan(request)
                # todo: handle responses with HTML-tags (warning/error messages)
            _LOGGER.info("SCAN-RESP: %s", response)

            # if success, then remove from queue, and re-run this method again
            self.barcode_queue.pop(0)

            # todo: handle responses with HTML-tags (warning/error messages)
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
                            value=SCAN_MODE.PURCHASE, label="Purchase"
                        ),
                        selector.SelectOptionDict(
                            value=SCAN_MODE.TRANSFER, label="Transfer"
                        ),  # todo: only add option if has more than 1 locations setup
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
) -> VolDictType:
    suggested_product_ids = [prod["id"] for prod in suggested_products]
    non_suggested_prods = [
        prod
        for prod in masterdata["products"]
        if prod["id"] not in suggested_product_ids
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
    ]
    prods.insert(
        len(suggested_products),
        selector.SelectOptionDict(value="-1", label="--CREATE NEW--"),
    )

    selected_product_id = "-1"
    if len(suggested_products) > 0:
        selected_product_id = suggested_values.get(
            "product_id", suggested_products[0]["id"]
        )
    selected_product_id = str(selected_product_id)

    schemas: VolDictType = {}
    schemas.update(
        {
            vol.Optional(
                "product_id",
                description={
                    "suggested_value": selected_product_id,
                },
                default=selected_product_id,
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=prods,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                    multiple=False,
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
            # todo: append current location name
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
    masterdata: GrocyMasterData, suggested_values: dict[str, str]
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
                },
            ): selector.TextSelector({"type": "text"})
        }
    )
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
                    "suggested_value": suggested_values.get("default_best_before_days"),
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

    schemas: VolDictType = {}
    schemas.update(
        {
            vol.Optional(
                "default_consume_location_id",
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
