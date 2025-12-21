"""Config flow for Grocy-helper integration."""

import copy
from enum import StrEnum
import datetime as dt
import logging
import json
import aiohttp
from typing import Any

import aiohttp.client_exceptions
import aiohttp.http_exceptions
import aiohttp.web_exceptions
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
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .http_requests import async_get

from .coordinator import GrocyHelperCoordinator
from .grocyapi import GrocyAPI
from .barcodebuddyapi import BarcodeBuddyAPI
from .grocytypes import (
    ExtendedGrocyProductStockInfo,
    GrocyProduct,
    GrocyProductBarcode,
    GrocyMasterData,
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
    ADD_PRODUCT = "add_product"

    SCAN_START = "scan_start"
    SCAN_QUEUE = "scan_queue"
    SCAN_ADD_PRODUCT = "scan_add_product"
    SCAN_ADD_PRODUCT_BARCODE = "scan_add_product_barcode"
    SCAN_PROCESS = "scan_process"


MAIN_MENU = [
    Step.ADD_RECIPE,
    Step.ADD_PRODUCT,
    Step.SCAN_START,
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
    barcode_scan_mode: str = None
    current_barcode_schema: vol.Schema = None
    barcode_queue: list[str] = []
    barcode_results: list[str] = []

    shopping_lists = None
    SHOPPING_LIST_SELECTOR_SCHEMA = None

    current_product: GrocyProduct = None

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

        config_entry_data = self.config_entry.data.copy()

        # Handle input
        if user_input is not None:
            if form := user_input.get("choose-form"):
                self.chosen_form = form
                if form == "get_product":
                    websession = async_get_clientsession(self.hass)
                    # url = f"http://{host}:{port}/api/objects/quantity_units"
                    url = f"{config_entry_data[CONF_GROCY_API_URL]}/api/objects/quantity_units"
                    resp = await async_get(
                        websession, url, auth_key=config_entry_data[CONF_GROCY_API_KEY]
                    )
                    _LOGGER.warning("RESP: %s", resp)
                    return self.async_abort(reason="Operation completed")
                if form == "add_product":
                    return await self.async_step_add_product(user_input=None)
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
        if user_input is not None:
            barcodes_str = user_input["barcodes"]
            self.barcode_scan_mode = user_input.get("mode")
            _LOGGER.info("SCAN: %s", barcodes_str)
            _LOGGER.info("SCAN-mode: %s", self.barcode_scan_mode)

            barcodes = barcodes_str.split("\n")
            self.barcode_queue = barcodes
            self.barcode_results = []
            return await self.async_step_scan_queue()

        return self.async_show_form(
            step_id=Step.SCAN_START,
            data_schema=STEP_SCAN_START,
            errors=errors,
        )

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
            msg = "\n".join(self.barcode_results)
            _LOGGER.info(
                "Options flow - process_scan Nothing more in scan queue!: %s",
                len(self.barcode_results),
            )
            return self.async_abort(reason="Nothing more in scan queue!" + "\n" + msg)

        code = current_barcode.strip().strip(",").strip()

        product: ExtendedGrocyProductStockInfo = None
        if "BBUDDY-" not in code:
            # Not a BarcodeBuddy code...
            # Lookup product in Grocy
            try:
                self.current_barcode = code
                product = await self._api_grocy.get_product_by_barcode(code)
                self.current_product = product

                if not product:
                    return await self.async_step_scan_add_product(user_input=None)
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
            self.barcode_results.append(f"{code} maps to {p["name"]}")
            return await self.async_step_scan_queue(user_input=None)

        return await self.async_step_scan_process(user_input=None)

    async def async_step_scan_add_product(self, user_input: dict[str, Any] = None):
        """Handle input for adding a new product."""
        errors: dict[str, str] = {}

        # code = current_barcode.strip().strip(",").strip()
        code = user_input["code"]

        new_product: GrocyProduct = {}

        # New barcode (Not provisioned in Grocy)
        # todo: Lookup in other providers...
        openfoodfacts_product: dict = {}
        ica_product: dict = None

        if openfoodfacts_product is None and ica_product is None:
            # todo: Not found in other providers, then show input's for manual registration?
            _LOGGER.error("No product info found!: %s", code)
            errors["NoProduct"] = "No product found!"
            return self.async_show_form(
                step_id=Step.SCAN_ADD_PRODUCT,
                data_schema=self.current_barcode_schema,
                errors=errors,
            )

        # Handle input, for required fields
        if user_input is None:
            schema = GENERATE_CREATE_PRODUCT_SCHEMA(
                self._coordinator.data, user_input or {}
            )
            _LOGGER.info("schem: %s", schema)
            self.add_suggested_values_to_schema(schema, user_input or {})
            _LOGGER.info("sugg schem: %s", schema)

            # ask for input...
            return self.async_show_form(
                step_id=Step.SCAN_ADD_PRODUCT,
                data_schema=schema,
                errors=errors,
            )

        # Input has been passed!
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

        # todo: create product
        _LOGGER.info("user_input: %s", user_input)
        _LOGGER.info("new_product: %s", new_product)
        product = await self._api_grocy.add_product(new_product)
        _LOGGER.info("created prod: %s", product)
        # todo: check for success!
        self.current_barcode = code
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
            schema = GENERATE_CREATE_PRODUCT_BARCODESCHEMA(
                self._coordinator.data,
                {
                    "note": user_input.get("note", new_product["name"]),
                    "qu_id": user_input.get("qu_id", new_product["qu_id_purchase"]),
                },
            )
            _LOGGER.info("schem: %s", schema)
            self.add_suggested_values_to_schema(schema, user_input or {})
            _LOGGER.info("sugg schem: %s", schema)

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
            "qu_id": user_input["qu_id_purchase"],
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
        schemas: vol.VolDictType = {}

        # code = current_barcode.strip().strip(",").strip()
        code = self.current_barcode
        product: ExtendedGrocyProductStockInfo = self.current_product

        # Handle input, for Price/BestBeforeInDays
        price = user_input.get("price") if user_input else None
        bestBeforeInDays = user_input.get("bestBeforeInDays") if user_input else None

        if self.barcode_scan_mode in [SCAN_MODE.PURCHASE]:
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
        if self.barcode_scan_mode in [SCAN_MODE.PURCHASE]:
            if price is not None and len(str(price)) > 0:
                request["price"] = float(price)
            if bestBeforeInDays is not None and len(str(bestBeforeInDays)) > 0:
                request["bestBeforeInDays"] = int(bestBeforeInDays)

        await self._api_bbuddy.set_mode()

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

    async def async_step_add_product(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        _LOGGER.debug(
            "Options flow - add_product: %s #%s", user_input, self.chosen_form
        )

        config_entry_data = self.config_entry.data.copy()

        # Handle input
        if user_input is not None:
            entity = (self.current_product or {}).copy()
            entity.update(user_input)

            # host = config_entry_data[CONF_HOST]
            # port = config_entry_data[CONF_PORT]
            # api_key = config_entry_data[CONF_API_KEY]

            # websession = async_get_clientsession(self.hass)
            # # websession = requests.Session()

            api: GrocyAPI = self.config_entry.runtime_data
            try:
                _LOGGER.info("Getting PRODUCTs")
                products = await api.get_products()
                _LOGGER.info("PRODUCTs1: %s", products)

                product = await api.add_product(data=entity)

            except aiohttp.web_exceptions.HTTPError as be:
                _LOGGER.info("Error when adding PRODUCT: %s", entity)
                _LOGGER.warning("Caught error: %s -> %s", be, be.status_code)
                return self.async_abort(reason=f"Error: {be}")
            except BaseException as be:
                _LOGGER.info("Error when adding PRODUCT: %s", entity)
                _LOGGER.info("Caught exception: %s", be)
                return self.async_abort(reason=f"Error: {be}")
            finally:
                _LOGGER.info("Finally Getting PRODUCTs")
                products = await api.get_products()
                _LOGGER.info("PRODUCTs2: %s", products)

            _LOGGER.info("TRY-loop exited with PRODUCTs: %s", product)

            # if form := user_input.get("choose-form"):
            #     if form == "get-product":
            #         url = f"http://{host}:{port}/api/objects/quantity_units"
            #         resp = await async_get(websession, url, auth_key=api_key)
            #         _LOGGER.warning("RESP: %s", resp)
            #         return self.async_abort(reason="Operation completed")

            return self.async_abort(reason="Successfully added product")

        # # Build dynamic schemas
        # coordinator: IcaCoordinator = self.config_entry.coordinator
        # await self._ensure_dynamic_schemas_are_built(coordinator)

        # Format form schema
        schema = vol.Schema(
            {
                vol.Required(
                    "name",
                ): cv.string,
            }
        )

        return self.async_show_form(
            step_id=Step.ADD_PRODUCT,
            data_schema=schema,
            errors=errors,
        )

    # async def _ensure_data_is_loaded_for_dynamic_schemas(
    #     self, coordinator: IcaCoordinator
    # ):
    #     if not self.shopping_lists:
    #         # Re-uses the coordinator on the config_entry for communicating with ICA api
    #         # Therefore no need to instantiate and authenticate a API new instance
    #         # Get shopping_lists directly from API as it will not limit the chosen shopping lists
    #         data = await coordinator.api.get_shopping_lists()
    #         if data and "shoppingLists" in data:
    #             y = data["shoppingLists"]
    #             lists = [z for z in y if z["offlineId"] and z["title"]]
    #             self.shopping_lists = lists

    # async def _ensure_dynamic_schemas_are_built(self, coordinator: IcaCoordinator):
    #     # Shopping list selector
    #     if not self.SHOPPING_LIST_SELECTOR_SCHEMA:
    #         await self._ensure_data_is_loaded_for_dynamic_schemas(coordinator)
    #         self.SHOPPING_LIST_SELECTOR_SCHEMA = (
    #             self._build_shopping_list_selector_schema(self.shopping_lists)
    #         )

    # def _build_shopping_list_selector_schema(self, lists):
    #     return {
    #         vol.Optional(
    #             CONF_SHOPPING_LISTS,
    #             description="The shopping lists to track",
    #             default=self.config_entry.data.get(CONF_SHOPPING_LISTS, []),
    #         ): selector.SelectSelector(
    #             selector.SelectSelectorConfig(
    #                 options=[
    #                     selector.SelectOptionDict(
    #                         label=list["title"], value=list["offlineId"]
    #                     )
    #                     for list in lists
    #                 ],
    #                 mode=selector.SelectSelectorMode.DROPDOWN,
    #                 multiple=True,
    #             )
    #         ),
    #     }

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


STEP_SCAN_START = vol.Schema(
    {
        vol.Optional(
            "mode",
            description={"suggested_value": SCAN_MODE.PURCHASE},  # During DEV....
        ): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[
                    selector.SelectOptionDict(value="", label="(Inherit)"),
                    selector.SelectOptionDict(value=SCAN_MODE.CONSUME, label="Consume"),
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
        # vol.Optional("barcodes", description={"suggested_value": current_data.get(CONF_STATS_TEMPLATE, "")}): TextSelector({"type": "text", "multiline": True}),
        vol.Required(
            "barcodes",
            description={"suggested_value": "4011800420413"},  # During DEV...
        ): selector.TextSelector({"type": "text", "multiline": True}),
    }
)


def GENERATE_CHOOSE_EXISTING_PRODUCT_SCHEMA(
    masterdata: GrocyMasterData, suggested_values: dict[str, str]
) -> vol.Schema:
    prods = [
        selector.SelectOptionDict(
            value=str(prod["id"]),
            label=prod["name"],
        )
        for prod in masterdata["products"]
    ]
    prods.insert(0, selector.SelectOptionDict(value="", label="--CREATE NEW--"))

    schemas: vol.VolDictType = {}
    schemas.update(
        {
            vol.Optional(
                "product_id",
                description={
                    "suggested_value": suggested_values.get("product_id"),
                },
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=prods,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                    multiple=False,
                )
            ),
        }
    )
    return vol.Schema(schemas)


def GENERATE_CREATE_PRODUCT_SCHEMA(
    masterdata: GrocyMasterData, suggested_values: dict[str, str]
) -> vol.Schema:
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

    schemas: vol.VolDictType = {}
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
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=qu,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                    multiple=False,
                )
            ),
        }
    )
    return vol.Schema(schemas)


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

    schemas: vol.VolDictType = {}
    schemas.update(
        {
            vol.Required(
                "note",
                description={
                    "suggested_value": suggested_values.get("note"),
                },
            ): selector.TextSelector({"type": "text"})
        }
    )
    schemas.update(
        {
            vol.Required(
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
            vol.Required(
                "qu_id",
                description={
                    "suggested_value": suggested_values.get("qu_id"),
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
                "amount",
            ): selector.TextSelector({"type": "text"})
        }
    )
    return vol.Schema(schemas)
