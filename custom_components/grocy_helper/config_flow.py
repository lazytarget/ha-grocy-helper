"""Config flow for Grocy-helper integration.

This module is a *thin adapter* between Home Assistant's config/options
flow framework and the framework-agnostic ``ScanSession`` business logic
defined in ``scan_session.py``.

If you need to change barcode scanning behaviour, edit ``scan_session.py``
or ``scan_types.py`` – this file should only contain HA-specific glue.
"""

import copy
import logging
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

from .const import (
    DOMAIN,
    CONF_GROCY_API_URL,
    CONF_GROCY_API_KEY,
    CONF_BBUDDY_API_URL,
    CONF_BBUDDY_API_KEY,
)
from .scan_session import ScanSession
from .scan_types import (
    AbortResult,
    CompletedResult,
    FieldType,
    FormField,
    FormRequest,
    NumberMode,
    SelectMode,
    Step,
    StepResult,
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
    """Thin adapter between Home Assistant OptionsFlow and ScanSession.

    All business logic lives in ``ScanSession``.  This class only:
    1. Creates a ``ScanSession`` from the coordinator.
    2. Delegates each ``async_step_*`` call to ``session.handle_step()``.
    3. Converts the framework-agnostic ``StepResult`` into an HA
       ``FlowResult`` (``async_show_form`` / ``async_abort``).
    """

    def __init__(self, config_entry: ConfigEntry) -> None:
        super().__init__()
        coordinator = config_entry.coordinator
        self._session = ScanSession(
            api_grocy=coordinator._api_grocy,
            api_bbuddy=coordinator._api_bbuddy,
            masterdata=coordinator.data,
            lookup_barcode=coordinator.lookup_barcode,
            convert_quantity=coordinator.convert_quantity_for_product,
            # Inject coordinator CRUD methods
            create_product=coordinator.create_product,
            update_product=coordinator.update_product,
            create_barcode=coordinator.create_product_barcode,
            create_qu_conversion=coordinator.create_quantity_unit_conversion,
            transfer_stock=coordinator.transfer_stock_entry,
            add_stock=coordinator.add_stock,
            update_recipe=coordinator.update_recipe,
        )

    # ── HA entry point ──────────────────────────────────────────────

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step (menu or auto‑select)."""
        errors: dict[str, str] = {}
        _LOGGER.debug("Options flow - data: %s", self.config_entry.data)

        if user_input is None and len(MAIN_MENU) == 1:
            user_input = {"choose-form": MAIN_MENU[0]}

        if user_input is not None:
            if form := user_input.get("choose-form"):
                if form == "main_menu":
                    return await self.async_step_main_menu(user_input=user_input)
                if form == "scan_start":
                    return await self.async_step_scan_start()
            return self.async_abort(reason="No operation chosen")

        schema = vol.Schema(
            {
                vol.Required("choose-form"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=MAIN_MENU,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                        multiple=False,
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_main_menu(self, user_input: dict[str, Any]):
        """Handle the group choice step."""
        menu = MAIN_MENU.copy()
        return self.async_show_menu(step_id=Step.MAIN_MENU, menu_options=menu)

    # ── delegating step methods ─────────────────────────────────────
    #
    # Each ``async_step_<name>`` simply delegates to ``ScanSession``
    # and converts the result.

    async def async_step_scan_start(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        return self._to_flow_result(
            await self._session.handle_step(Step.SCAN_START, user_input)
        )

    async def async_step_scan_match_to_product(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        return self._to_flow_result(
            await self._session.handle_step(Step.SCAN_MATCH_PRODUCT, user_input)
        )

    async def async_step_scan_add_product(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        return self._to_flow_result(
            await self._session.handle_step(Step.SCAN_ADD_PRODUCT, user_input)
        )

    async def async_step_scan_add_product_parent(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        return self._to_flow_result(
            await self._session.handle_step(Step.SCAN_ADD_PRODUCT_PARENT, user_input)
        )

    async def async_step_scan_add_product_barcode(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        return self._to_flow_result(
            await self._session.handle_step(Step.SCAN_ADD_PRODUCT_BARCODE, user_input)
        )

    async def async_step_scan_update_product_details(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        return self._to_flow_result(
            await self._session.handle_step(Step.SCAN_UPDATE_PRODUCT_DETAILS, user_input)
        )

    async def async_step_scan_transfer_start(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        return self._to_flow_result(
            await self._session.handle_step(Step.SCAN_TRANSFER_START, user_input)
        )

    async def async_step_scan_transfer_input(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        return self._to_flow_result(
            await self._session.handle_step(Step.SCAN_TRANSFER_INPUT, user_input)
        )

    async def async_step_scan_process(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        return self._to_flow_result(
            await self._session.handle_step(Step.SCAN_PROCESS, user_input)
        )

    # ── result conversion ───────────────────────────────────────────

    def _to_flow_result(self, result: StepResult) -> FlowResult:
        """Convert a framework-agnostic ``StepResult`` into an HA ``FlowResult``."""

        if isinstance(result, FormRequest):
            schema = _form_request_to_schema(result)
            return self.async_show_form(
                step_id=result.step_id,
                data_schema=schema,
                description_placeholders=result.description_placeholders,
                errors=result.errors,
            )

        if isinstance(result, CompletedResult):
            return self.async_abort(reason=result.summary)

        if isinstance(result, AbortResult):
            return self.async_abort(reason=result.reason)

        return self.async_abort(reason="Unexpected result type")

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
                    new_key = vol.Optional(key.schema, default=options.get(key))
                elif "suggested_value" not in (new_key.description or {}):
                    new_key = copy.copy(key)
                    new_key.description = {"suggested_value": options.get(key)}
            schema[new_key] = val
        return vol.Schema(schema)


# =====================================================================
# FormRequest → vol.Schema conversion
# =====================================================================

_SELECT_MODE_MAP = {
    SelectMode.DROPDOWN: selector.SelectSelectorMode.DROPDOWN,
    SelectMode.LIST: selector.SelectSelectorMode.LIST,
}
_NUMBER_MODE_MAP = {
    NumberMode.BOX: selector.NumberSelectorMode.BOX,
    NumberMode.SLIDER: selector.NumberSelectorMode.SLIDER,
}


def _field_to_vol(field: FormField):
    """Convert a single ``FormField`` to a ``(vol.Marker, validator)`` pair."""

    # ── build validator ─────────────────────────────────────────────
    if field.field_type == FieldType.TEXT:
        validator = selector.TextSelector(
            {"type": "text", "multiline": field.multiline}
        )

    elif field.field_type == FieldType.NUMBER:
        cfg: dict[str, Any] = {
            "mode": _NUMBER_MODE_MAP.get(
                field.number_mode, selector.NumberSelectorMode.BOX
            ),
        }
        if field.step is not None:
            cfg["step"] = field.step
        if field.min_value is not None:
            cfg["min"] = field.min_value
        if field.max_value is not None:
            cfg["max"] = field.max_value
        validator = selector.NumberSelector(
            selector.NumberSelectorConfig(**cfg)
        )

    elif field.field_type == FieldType.SELECT:
        options = [
            selector.SelectOptionDict(value=o.value, label=o.label)
            for o in (field.options or [])
        ]
        validator = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=options,
                mode=_SELECT_MODE_MAP.get(
                    field.select_mode, selector.SelectSelectorMode.DROPDOWN
                ),
                multiple=field.multiple,
                custom_value=field.custom_value,
            )
        )

    elif field.field_type == FieldType.BOOLEAN:
        validator = selector.BooleanSelector()

    else:
        validator = cv.string

    # ── build vol key ───────────────────────────────────────────────
    desc: dict[str, Any] = {}
    if field.suggested_value is not None:
        desc["suggested_value"] = field.suggested_value
    if field.description:
        desc["description"] = field.description

    kwargs: dict[str, Any] = {}
    if desc:
        kwargs["description"] = desc
    if field.default is not None:
        kwargs["default"] = field.default

    if field.required:
        key = vol.Required(field.key, **kwargs)
    else:
        key = vol.Optional(field.key, **kwargs)

    return key, validator


def _form_request_to_schema(form: FormRequest) -> vol.Schema:
    """Convert a ``FormRequest`` into a Home Assistant ``vol.Schema``."""

    schema: dict = {}
    for f in form.fields:
        k, v = _field_to_vol(f)
        schema[k] = v
    return vol.Schema(schema)

