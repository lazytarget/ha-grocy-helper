"""DataUpdateCoordinator for the Grocy-helper component."""

import logging
import traceback
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .grocyapi import GrocyAPI
from .barcodebuddyapi import BarcodeBuddyAPI
from .grocytypes import GrocyMasterData

_LOGGER = logging.getLogger(__name__)


class GrocyHelperCoordinator(DataUpdateCoordinator[GrocyMasterData]):
    """Coordinator for updating data from Grocy."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        grocy_api: GrocyAPI,
        barcodebuddy_api: BarcodeBuddyAPI,
        logger: logging.Logger,
        update_interval: timedelta,
    ) -> None:
        """Initialize the Grocy-helper coordinator."""
        super().__init__(
            hass, logger, name="Grocy-helper", update_interval=update_interval
        )
        self.SCAN_INTERVAL = update_interval
        self._config_entry = config_entry
        self._api_grocy: GrocyAPI = grocy_api
        self._api_bbuddy: BarcodeBuddyAPI = barcodebuddy_api
        self._hass = hass

    async def _async_setup(self) -> None:
        """Initialize coordinator."""
        _LOGGER.info("Init coordinator")

    async def _async_update_data(self) -> GrocyMasterData:
        """Fetch data from Grocy."""
        _LOGGER.info("Update data")
        data = await self.fetch_data()
        return data

    async def fetch_data(self) -> GrocyMasterData:
        """Fetch masterdata from Grocy."""
        try:
            locations = await self._api_grocy.get_locations()
            _LOGGER.debug("Loaded locations: %s", locations)

            shopping_locations = await self._api_grocy.get_shopping_locations()
            _LOGGER.debug("Loaded stores: %s", shopping_locations)

            quantity_units = await self._api_grocy.get_quantityunits()
            _LOGGER.debug("Loaded quantity_units: %s", quantity_units)

            products = await self._api_grocy.get_products()
            _LOGGER.debug("Loaded products: %s", len(products))

            masterdata: GrocyMasterData = {
                "locations": locations,
                "shopping_locations": shopping_locations,
                "quantity_units": quantity_units,
                "products": products,
            }
            return masterdata
        except Exception as err:
            _LOGGER.error("Exception when getting data. Err: %s", err)
            _LOGGER.error(traceback.format_exc())
            raise UpdateFailed(f"Error communicating with API: {err}") from err
