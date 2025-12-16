"""Config flow for ICA integration."""

import copy
from enum import StrEnum
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
from homeassistant.const import CONF_API_KEY, CONF_HOST, CONF_PORT
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .http_requests import async_get

from .grocyapi import GrocyAPI
from .grocytypes import GrocyProduct, BarcodeBuddyScanRequest, BarcodeBuddyScanResponse

from .const import (
    DOMAIN,
    CONF_GROCY_API_URL,
    CONF_GROCY_API_KEY,
    CONF_BBUDDY_API_URL,
    CONF_BBUDDY_API_KEY,
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
    SCAN = "scan"


MAIN_MENU = [
    Step.ADD_RECIPE,
    Step.ADD_PRODUCT,
    Step.SCAN,
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
            bbuddy_url = user_input[CONF_GROCY_API_URL]
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

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> OptionsFlow:
        """Create the options flow."""
        return GrocyOptionsFlowHandler(config_entry)


class GrocyOptionsFlowHandler(OptionsFlow):
    """Handle an options flow for grocy-helper."""

    shopping_lists = None

    SHOPPING_LIST_SELECTOR_SCHEMA = None

    current_product: GrocyProduct = None

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize Ica options flow"""
        # pylint: disable=W0613 unused-argument
        super().__init__()

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        _LOGGER.debug("Options flow - data: %s", self.config_entry.data)

        config_entry_data = self.config_entry.data.copy()

        # Handle input
        if user_input is not None:
            # host = config_entry_data[CONF_HOST]
            # port = config_entry_data[CONF_PORT]
            # api_key = config_entry_data[CONF_API_KEY]

            if form := user_input.get("choose-form"):
                self.chosen_form = form
                if form == "get_product":
                    websession = async_get_clientsession(self.hass)
                    # websession = requests.Session()

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
                if form == "scan":
                    return await self.async_step_scan()

            return self.async_abort(reason="No operation chosen")

        # # Build dynamic schemas
        # coordinator: IcaCoordinator = self.config_entry.coordinator
        # await self._ensure_dynamic_schemas_are_built(coordinator)

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

    async def async_step_scan(self, user_input: dict[str, Any] = None):
        """Handle the initial step."""
        errors: dict[str, str] = {}
        _LOGGER.debug("Options flow - scan: %s #%s", user_input, self.chosen_form)

        config_entry_data = self.config_entry.data.copy()

        # Handle input
        if user_input is not None:
            # host = config_entry_data[CONF_HOST]
            # port = config_entry_data[CONF_PORT]
            # api_key = config_entry_data[CONF_API_KEY]

            # websession = async_get_clientsession(self.hass)
            # # websession = requests.Session()

            barcodes_str = user_input["barcodes"]
            _LOGGER.info("SCAN: %s", barcodes_str)
            entity = barcodes_str

            api: GrocyAPI = self.config_entry.runtime_data
            barcodes = barcodes_str.split("\n")
            for barcode in barcodes:
                code = barcode.strip().strip(",").strip()
                product = await api.get_product_by_barcode(code)
                if product is None:
                    # Product not found, create it after looking up info
                    _LOGGER.info("PRODUCT not found: %s", code)
                else:
                    _LOGGER.info("PRODUCT: %s -> %s", code, product)
                    request = BarcodeBuddyScanRequest(
                        barcode=code, price=None, bestBeforeInDays=None
                    )
                    response = await api.bbuddy_scan(request)
                    _LOGGER.info("SCAN: %s", json.dumps(response))

                # try:
                #     barcode = barcode.strip()
                #     _LOGGER.info("SCAN Barcode: %s", barcode)
                #     product = await api.get_product_by_barcode(barcode)
                #     _LOGGER.info("PRODUCT: %s", product)

                #     # product = await api.add_product(data=entity)
                # except aiohttp.web_exceptions.HTTPBadRequest as br:
                #     if br.text.startswith("No product with barcode "):
                #         product = None
                #         _LOGGER.info("PRODUCT not found!")
                #     else:
                #         _LOGGER.info("br ex: %s", br.text)
                #         raise br
                # except aiohttp.web_exceptions.HTTPError as he:
                #     _LOGGER.info("Error when scanning he PRODUCT: %s", entity)
                #     _LOGGER.warning("Caught error: %s -> %s", he, he.status_code)
                #     return self.async_abort(reason=f"Error: {he}")
                # except aiohttp.client_exceptions.ClientResponseError as cre:
                #     _LOGGER.info("Error when scanning cre PRODUCT: %s", barcode)
                #     if cre.code == 400 and cre.message.startswith("No product with barcode "):
                #         product = None
                #         _LOGGER.info("PRODUCT not found!")
                #     else:
                #         _LOGGER.warning("Raised error: %s -> %s", cre.code, cre.message)
                #         raise cre
                # except BaseException as he:
                #     _LOGGER.info("Error when scanning be PRODUCT: %s", entity)
                #     _LOGGER.info("Caught exception: [%s] %s", he.__class__.__name__, he)
                #     return self.async_abort(reason=f"Error: {he}")
                # # finally:
                # #     _LOGGER.info("Finally Getting PRODUCTs")
                # #     products = await api.get_products()
                # #     _LOGGER.info("PRODUCTs2: %s", products)

            _LOGGER.info("TRY-loop exited with PRODUCTs: %s", entity)

            # if form := user_input.get("choose-form"):
            #     if form == "get-product":
            #         url = f"http://{host}:{port}/api/objects/quantity_units"
            #         resp = await async_get(websession, url, auth_key=api_key)
            #         _LOGGER.warning("RESP: %s", resp)
            #         return self.async_abort(reason="Operation completed")

            return self.async_abort(reason="Successfully scanned")

        # # Build dynamic schemas
        # coordinator: IcaCoordinator = self.config_entry.coordinator
        # await self._ensure_dynamic_schemas_are_built(coordinator)

        # Format form schema
        schema = vol.Schema(
            {
                # vol.Optional("barcodes", description={"suggested_value": current_data.get(CONF_STATS_TEMPLATE, "")}): TextSelector({"type": "text", "multiline": True}),
                vol.Required(
                    "barcodes", description={"suggested_value": "4011800420413"}
                ): selector.TextSelector({"type": "text", "multiline": True}),
            }
        )

        return self.async_show_form(
            step_id=Step.SCAN,
            data_schema=schema,
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
