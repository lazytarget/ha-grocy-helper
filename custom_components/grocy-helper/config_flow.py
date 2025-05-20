"""Config flow for ICA integration."""

import copy
from enum import StrEnum
import logging
from typing import Any

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
from .grocytypes import GrocyProduct

from .const import (
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST, description="Host", default="localhost"): cv.string,
        vol.Required(CONF_PORT, description="Port", default=4010): cv.port,
        vol.Required(CONF_API_KEY): cv.string,
    }
)


class Step(StrEnum):
    MAIN_MENU = "main_menu"
    ADD_RECIPE = "add_recipe"
    ADD_PRODUCT = "add_product"

MAIN_MENU = [
    Step.ADD_RECIPE,
    Step.ADD_PRODUCT,
]

class GrocyConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for ICA."""

    VERSION = 1
    MINOR_VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST]
            port = user_input[CONF_PORT]
            api_key = user_input[CONF_API_KEY]

            # Assign unique id based on Host/Port
            await self.async_set_unique_id(f"{DOMAIN}__{host}_{port}")
            # Abort flow if a config entry with same Host and Port exists
            self._abort_if_unique_id_configured()

            config_entry_data = {
                CONF_HOST: host,
                CONF_PORT: port,
                CONF_API_KEY: api_key,
            }
            return self.async_create_entry(
                title=f"{host}:{port}",
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
            host = config_entry_data[CONF_HOST]
            port = config_entry_data[CONF_PORT]
            api_key = config_entry_data[CONF_API_KEY]

            websession = async_get_clientsession(self.hass)
            # websession = requests.Session()

            if form := user_input.get("choose-form"):
                self.chosen_form = form
                if form == "get_product":
                    url = f"http://{host}:{port}/api/objects/quantity_units"
                    resp = await async_get(websession, url, auth_key=api_key)
                    _LOGGER.warning("RESP: %s", resp)
                    return self.async_abort(reason="Operation completed")
                if form == "add_product":
                    return await self.async_step_add_product(user_input=None)
                if form == "main_menu":
                    return await self.async_step_main_menu(user_input=user_input)

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
                        options=["get_product", "add_product", "main_menu"],
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

    async def async_step_add_product(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        _LOGGER.debug("Options flow - add_product: %s #%s", user_input, self.chosen_form)

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
                product = await api.add_product(data=entity)
            except BaseException as be:
                _LOGGER.info("Error when adding PRODUCT: %s", entity)
                return self.async_abort(reason=f"Error: {be}")
            _LOGGER.info("ADDED PRODUCT: %s", product)

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
                if isinstance(key, vol.Optional) and callable(key.default) and key.default():
                    new_key = vol.Optional(key.schema, default=options.get(key))  # type: ignore
                elif "suggested_value" not in (new_key.description or {}):
                    new_key = copy.copy(key)
                    new_key.description = {"suggested_value": options.get(key)}  # type: ignore
            schema[new_key] = val
        return vol.Schema(schema)
