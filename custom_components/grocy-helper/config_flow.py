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
    GrocyProduct,
    GrocyProductBarcode,
    GrocyMasterData,
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
    }
    current_bb_mode: int = -1
    barcode_scan_mode: str = None
    barcode_queue: list[str] = []
    barcode_results: list[str] = []

    current_barcode: str = None
    current_barcode_schema: vol.Schema = None
    current_product: GrocyProduct | None = None
    current_product_openfoodfacts: OpenFoodFactsProduct | None = None
    matching_products: list[GrocyProduct] = []

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

        barcodes_str = user_input["barcodes"]
        self.barcode_scan_mode = user_input.get("mode")
        _LOGGER.info("SCAN: %s", barcodes_str)
        _LOGGER.info("SCAN-mode: %s", self.barcode_scan_mode)

        barcodes = barcodes_str.split("\n")
        self.barcode_queue = barcodes
        self.barcode_results = []
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

        self.current_product = None
        current_barcode = self.barcode_queue[0] if len(self.barcode_queue) > 0 else None

        if not current_barcode:
            # Nothing in queue, show summary
            # todo: Add result info to message...
            msg = "\r\n".join(self.barcode_results)
            _LOGGER.info(
                "Options flow - process_scan Nothing more in scan queue!: %s",
                len(self.barcode_results),
            )
            return self.async_abort(reason=msg)

        code = current_barcode.strip().strip(",").strip()
        self.current_barcode = code
        self.current_product = None

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
                    product = await self._api_grocy.get_product_by_barcode(code)
                    self.current_product = product
                    self.matching_products: list[GrocyProduct] = []

                    if not product:
                        # New barcode (Not provisioned in Grocy)
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
                            self._coordinator.data["products"],
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

            p = (product or {}).get("product") or self.current_product
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
            self.current_product = await self._api_grocy.get_product_by_id(
                int(user_input["product_id"])
            )
        else:
            # Create a new product
            _LOGGER.info("no-product-id: %s", user_input)
            return await self.async_step_scan_add_product(user_input=None)

        return await self.async_step_scan_add_product_barcode(user_input=None)

    async def async_step_scan_add_product(self, user_input: dict[str, Any] = None):
        """Handle input for adding a new product."""
        errors: dict[str, str] = {}
        _LOGGER.info("add-product: %s", user_input)

        # code = current_barcode.strip().strip(",").strip()
        # code = user_input["code"]
        code = self.current_barcode

        new_product: GrocyProduct = {}

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

        # Handle input, for required fields
        if user_input is None:
            user_input = user_input or {}
            # self.matching_product = None

            if self.current_product_openfoodfacts is not None:
                # Fill in from OpenFoodFacts
                user_input["name"] = (
                    user_input.get("name")
                    or self.current_product_openfoodfacts["product_name"]
                )
                unit = self.current_product_openfoodfacts.get("product_quantity_unit")
                if unit:
                    for qq in filter(
                        lambda qu: qu.get("name") == unit,
                        self._coordinator.data["quantity_units"],
                    ):
                        user_input["qu_id"] = str(qq["id"])
                        _LOGGER.warning("Unit: %s, QQ: %s", unit, qq)
                # todo: fill in guess of QuantityUnit...
            
            if self.current_product_ica is not None:
                # todo: fill in info from ICA...
                pass

            schema: VolDictType = None
            schema = GENERATE_CREATE_PRODUCT_SCHEMA(self._coordinator.data, user_input)

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
            self.current_product = await self._api_grocy.get_product_by_id(
                int(user_input["product_id"])
            )
        else:
            # Create a new product

            # more friendly name (barcode has specific name/"note")
            new_product["name"] = user_input["name"]
            new_product["location_id"] = user_input["location_id"]
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
            product = await self._api_grocy.add_product(new_product)
            _LOGGER.info("created prod: %s", product)
            # todo: check for success!
            self.current_product = product

        return await self.async_step_scan_add_product_barcode(user_input=None)

    async def async_step_scan_add_product_barcode(
        self, user_input: dict[str, Any] = None
    ):
        """Handle input for adding product barcode."""
        errors: dict[str, str] = {}

        # code = current_barcode.strip().strip(",").strip()
        # code = user_input["code"]
        code = self.current_barcode

        new_product: GrocyProduct = self.current_product

        # Handle input, for required fields
        if user_input is None:
            user_input = user_input or {}
            user_input["note"] = user_input.get("note", new_product["name"])
            user_input["qu_id"] = str(
                user_input.get("qu_id", new_product["qu_id_purchase"])
            )

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
            "qu_id": user_input["qu_id"],
            "shopping_location_id": user_input.get("shopping_location_id"),
            "amount": user_input.get("amount"),
            "row_created_timestamp": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        pcode = await self._api_grocy.add_product_barcode(br)
        _LOGGER.info("created prod_barcode: %s", pcode)
        # todo: append barcode to product

        # created product, now re-run process for same barcode
        # todo: in-future this could be merged to same process-work (avoid extra form)
        return await self.async_step_scan_queue(user_input=None)

    async def async_step_scan_process(self, user_input: dict[str, Any] = None):
        """Handle the scanned barcode (product exists)."""
        errors: dict[str, str] = {}
        schemas: VolDictType = {}

        code = self.current_barcode

        # Handle input, for Price/BestBeforeInDays
        price = user_input.get("price") if user_input else None
        bestBeforeInDays = user_input.get("bestBeforeInDays") if user_input else None

        in_purchase_mode = self.barcode_scan_mode in [SCAN_MODE.PURCHASE] or (
            self.barcode_scan_mode == SCAN_MODE.SCAN_BBUDDY
            and self.current_bb_mode
            == self._api_bbuddy.convert_scan_mode_to_bbuddy_mode(SCAN_MODE.PURCHASE)
        )
        if in_purchase_mode:
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
            if bestBeforeInDays is None and self.scan_options.get(
                "input_bestBeforeInDays"
            ):
                _LOGGER.info(
                    "BestBeforeInDays input enabled: append schema field, value: %s",
                    bestBeforeInDays,
                )
                schemas.update(
                    {
                        vol.Optional(
                            "bestBeforeInDays",
                            description={"suggested_value": bestBeforeInDays},
                        ): selector.TextSelector({"type": "text"})
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
                        # selector.SelectOptionDict(
                        #     value=SCAN_MODE.CONSUME_SPOILED,
                        #     label="Consume (Spoiled)"
                        # ),
                        # selector.SelectOptionDict(
                        #     value=SCAN_MODE.CONSUME_ALL,
                        #     label="Consume (All)"
                        # ),
                        selector.SelectOptionDict(
                            value=SCAN_MODE.PURCHASE, label="Purchase"
                        ),
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
                default="7311070347326",
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
    sorted(non_suggested_prods, key=lambda p: p["name"])

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


def GENERATE_CREATE_PRODUCT_BARCODESCHEMA(
    masterdata: GrocyMasterData, suggested_values: dict[str, str]
) -> vol.Schema:
    shopping_locations = [
        selector.SelectOptionDict(
            value=str(store["id"]),
            label=store["name"],
        )
        for store in masterdata["shopping_locations"]
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
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(mode=selector.NumberSelectorMode.BOX)
            )
        }
    )
    return vol.Schema(schemas)
