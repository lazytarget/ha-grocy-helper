import voluptuous as vol
import logging

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
import homeassistant.helpers.config_validation as cv

from .coordinator import GrocyHelperCoordinator
from .const import DOMAIN, ServiceCalls
from .grocytypes import ServiceCallResponse, GrocyQuantityUnitConversionResult

_LOGGER = logging.getLogger(__name__)

RESOLVE_QUANTITY_UNIT_CONVERSION_FOR_PRODUCT_SCHEMA = vol.Schema(
    {
        vol.Optional("integration"): cv.string,  # TODO: to allow to choose from list?
        vol.Required("product_id"): int,  # TODO: to allow to choose from list?
        vol.Required("from_qu_id"): int,  # TODO: to allow to choose from list?
        vol.Required("to_qu_id"): int,  # TODO: to allow to choose from list?
        vol.Required("amount"): cv.Number,
    }
)


def _get_coordinator(
    hass: HomeAssistant, config_entry: ConfigEntry
) -> GrocyHelperCoordinator:
    """Return coordinator for a config entry, raising a clear error if missing."""
    # Prefer the coordinator stored directly on the config entry when available
    entry_coordinator = getattr(config_entry, "coordinator", None)
    if isinstance(entry_coordinator, GrocyHelperCoordinator):
        return entry_coordinator

    # Fallback to hass.data, but guard against missing or malformed data
    domain_data = hass.data.get(DOMAIN)
    if not isinstance(domain_data, dict):
        raise HomeAssistantError(
            f"No data found for domain '{DOMAIN}' while resolving coordinator for "
            f"config entry '{config_entry.title}'."
        )

    coordinator = domain_data.get(config_entry.entry_id)
    if not isinstance(coordinator, GrocyHelperCoordinator):
        raise HomeAssistantError(
            f"No coordinator found for config entry '{config_entry.title}' "
            f"({config_entry.entry_id})."
        )

    return coordinator


def setup_global_services(hass: HomeAssistant) -> None:
    """Registers any global service calls that can be made with this integration."""
    if not hass.services.has_service(
        DOMAIN, ServiceCalls.RESOLVE_QUANTITY_UNIT_CONVERSION_FOR_PRODUCT
    ):
        async def execute(
            call: ServiceCall,
        ) -> ServiceCallResponse[GrocyQuantityUnitConversionResult] | None:
            """Call will query ICA api after the user's favorite items"""
            config_entry: ConfigEntry | None
            if entry_id := call.data.get("integration"):
                config_entry: ConfigEntry = hass.config_entries.async_get_entry(
                    entry_id
                )
            else:
                entries = hass.config_entries.async_entries(DOMAIN)
                config_entry: ConfigEntry = (
                    entries[0] if entries and len(entries) > 0 else None
                )

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

            product_id = int(call.data["product_id"])
            from_qu_id = int(call.data["from_qu_id"])
            to_qu_id = int(call.data["to_qu_id"])
            amount = float(call.data["amount"])
            _LOGGER.info("Prod: %s", product_id)
            _LOGGER.info("QU_id: %s -> %s", from_qu_id, to_qu_id)
            _LOGGER.info("Amount: %s", amount)

            coordinator: GrocyHelperCoordinator = _get_coordinator(hass, config_entry)
            result = await coordinator.convert_quantity_for_product(
                product_id=product_id,
                from_qu_id=from_qu_id,
                to_qu_id=to_qu_id,
                amount=amount,
            )
            response = ServiceCallResponse[GrocyQuantityUnitConversionResult](
                success=bool(result),
                data=result,
            )
            if not response["success"]:
                response["message"] = (
                    "Could not convert quantity for specified product and units"
                )
            _LOGGER.info("Convert response: %s", response)
            return response

        hass.services.async_register(
            DOMAIN,
            ServiceCalls.RESOLVE_QUANTITY_UNIT_CONVERSION_FOR_PRODUCT,
            execute,
            schema=RESOLVE_QUANTITY_UNIT_CONVERSION_FOR_PRODUCT_SCHEMA,
            supports_response=SupportsResponse.OPTIONAL,
        )
