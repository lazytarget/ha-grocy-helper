import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
import homeassistant.helpers.config_validation as cv

from .coordinator import GrocyHelperCoordinator
from .const import DOMAIN, ServiceCalls
from .grocytypes import GrocyQuantityUnitConversionsResolved

import logging

_LOGGER = logging.getLogger(__name__)

RESOLVE_QUANTITY_UNIT_CONVERSION_FOR_PRODUCT_SCHEMA = vol.Schema(
    {
        vol.Optional("integration"): cv.string,  # todo: to allow to choose from list?
        vol.Required("product_id"): int,  # todo: to allow to choose from list?
        vol.Required("from_qu_id"): int,  # todo: to allow to choose from list?
        vol.Required("to_qu_id"): int,  # todo: to allow to choose from list?
        vol.Required("amount"): cv.Number,
    }
)


def setup_global_services(hass: HomeAssistant) -> None:
    if not hass.services.has_service(
        DOMAIN, ServiceCalls.RESOLVE_QUANTITY_UNIT_CONVERSION_FOR_PRODUCT
    ):
        async def execute(
            call: ServiceCall,
        ) -> dict:
            """Call will query ICA api after the user's favorite items"""
            config_entry: ConfigEntry | None
            if entry_id := call.data["integration"]:
                config_entry: ConfigEntry = hass.config_entries.async_get_entry(entry_id)
            else:
                config_entry: ConfigEntry = hass.config_entries.async_entries(DOMAIN)[0]

            if not config_entry:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="integration_not_found",
                    translation_placeholders={"target": DOMAIN},
                )
            if config_entry.state != ConfigEntryState.LOADED:
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="not_loaded",
                    translation_placeholders={"target": config_entry.title},
                )
            coordinator: GrocyHelperCoordinator = (
                config_entry.coordinator or hass.data[DOMAIN][entry_id]
            )

            product_id = int(call.data["product_id"])
            from_qu_id = int(call.data["from_qu_id"])
            to_qu_id = int(call.data["to_qu_id"])
            amount = call.data["amount"]
            _LOGGER.info("Prod: %s", product_id)
            _LOGGER.info("QU_id: %s -> %s", from_qu_id, to_qu_id)
            _LOGGER.info("Amount: %s", amount)
            result = await coordinator.convert_quantity_for_product(
                product_id=product_id,
                from_qu_id=from_qu_id,
                to_qu_id=to_qu_id,
                amount=amount,
            )
            _LOGGER.info("Convert result: %s", result)
            return result

        hass.services.async_register(
            DOMAIN,
            ServiceCalls.RESOLVE_QUANTITY_UNIT_CONVERSION_FOR_PRODUCT,
            execute,
            schema=RESOLVE_QUANTITY_UNIT_CONVERSION_FOR_PRODUCT_SCHEMA,
            supports_response=SupportsResponse.OPTIONAL,
        )

