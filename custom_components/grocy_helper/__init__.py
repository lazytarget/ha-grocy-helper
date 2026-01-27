"""The Grocy-helper integration."""

import datetime
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .services import setup_global_services
from .coordinator import GrocyHelperCoordinator
from .grocyapi import GrocyAPI
from .barcodebuddyapi import BarcodeBuddyAPI

from .const import (
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    CONF_GROCY_API_URL,
    CONF_GROCY_API_KEY,
    CONF_BBUDDY_API_URL,
    CONF_BBUDDY_API_KEY,
)

_LOGGER = logging.getLogger(__name__)

# PLATFORMS: list[Platform] = [Platform.TODO]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Grocy-helper from a config entry."""
    _LOGGER.info(
        "Setting up grocy-helper config entry v%s.%s - Data: %s",
        entry.version,
        entry.minor_version,
        entry.data,
    )
    update_interval = datetime.timedelta(
        minutes=entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    )
    # Setup global services, if not already setup
    # TODO: verify timings when multiple config_entries
    setup_global_services(hass)

    # websession = async_get_clientsession(hass)
    def websession():
        s = async_get_clientsession(hass)
        return s

    grocy = GrocyAPI(
        entry.data[CONF_GROCY_API_URL],
        ["GROCY-API-KEY", entry.data[CONF_GROCY_API_KEY]],
        websession,
    )
    bbuddy = BarcodeBuddyAPI(
        entry.data[CONF_BBUDDY_API_URL],
        ["BBUDDY-API-KEY", entry.data[CONF_BBUDDY_API_KEY]],
        websession,
    )

    coordinator = GrocyHelperCoordinator(
        hass,
        entry,
        grocy,
        bbuddy,
        _LOGGER,
        update_interval,
    )
    # Load master data
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator
    entry.coordinator = coordinator

    # await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
    #     hass.data[DOMAIN].pop(entry.entry_id)
    # return unload_ok
    return True
