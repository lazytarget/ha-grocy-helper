"""The Grocy-helper integration."""

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .grocyapi import GrocyAPI
from .barcodebuddyapi import BarcodeBuddyAPI

from .const import (
    DOMAIN,
    CONF_GROCY_API_URL,
    CONF_GROCY_API_KEY,
    CONF_BBUDDY_API_URL,
    CONF_BBUDDY_API_KEY,
)

_LOGGER = logging.getLogger(__name__)

# PLATFORMS: list[Platform] = [Platform.TODO]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up ICA from a config entry."""
    _LOGGER.info(
        "Loaded grocy-helper config entry v%s.%s - Data: %s",
        entry.version,
        entry.minor_version,
        entry.data,
    )

    # host = entry.data[CONF_HOST]
    # port = entry.data[CONF_PORT]
    # api_key = entry.data[CONF_API_KEY]

    # coordinator = IcaCoordinator(
    #     hass,
    #     entry,
    #     _LOGGER,
    #     update_interval,
    #     api,
    # )
    # await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})

    # hass.data[DOMAIN][entry.entry_id] = coordinator
    # entry.coordinator = coordinator
    # base_url = f"http://{host}:{port}"
    # websession = async_get_clientsession(hass)
    def websession():
        s = async_get_clientsession(hass)
        return s

    # entry.runtime_data = GrocyAPI(base_url, api_key, websession)
    grocy = GrocyAPI(
        entry.data[CONF_GROCY_API_URL], 
        ["GROCY-API-KEY", entry.data[CONF_GROCY_API_KEY]], 
        websession
    )
    bbuddy = BarcodeBuddyAPI(
        entry.data[CONF_BBUDDY_API_URL], 
        ["BBUDDY-API-KEY", entry.data[CONF_BBUDDY_API_KEY]], 
        websession
    )
    entry.runtime_data = {
        "grocy": grocy,
        "bbuddy": bbuddy,
    }

    # await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
    #     hass.data[DOMAIN].pop(entry.entry_id)
    # return unload_ok
    return True
