"""The Grocy-helper integration."""

import datetime
import logging

from aiohttp import web

from homeassistant.components import webhook as ha_webhook
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .services import setup_global_services
from .coordinator import GrocyHelperCoordinator
from .grocyapi import GrocyAPI
from .barcodebuddyapi import BarcodeBuddyAPI, BarcodeBuddyAPI_Fake
from .queue import ScanQueue
from .webhook import process_webhook_payload, WebhookError, WebhookResponse
from .auto_resolver import async_try_auto_resolve

from .const import (
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    CONF_GROCY_API_URL,
    CONF_GROCY_API_KEY,
    CONF_BBUDDY_API_URL,
    CONF_BBUDDY_API_KEY,
    STORAGE_KEY_QUEUE,
    STORAGE_VERSION_QUEUE,
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
    bbuddy_api_url = entry.data.get(CONF_BBUDDY_API_URL)
    if bbuddy_api_url and (bbuddy_api_key := entry.data.get(CONF_BBUDDY_API_KEY)):
        bbuddy = BarcodeBuddyAPI(
            bbuddy_api_url,
            ["BBUDDY-API-KEY", bbuddy_api_key],
            websession,
        )
    else:
        bbuddy = BarcodeBuddyAPI_Fake()

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

    # ── Persistent scan queue ──────────────────────────────────────
    store = Store(hass, STORAGE_VERSION_QUEUE, f"{STORAGE_KEY_QUEUE}.{entry.entry_id}")
    queue = ScanQueue(store=store)
    await queue.async_load()
    coordinator.queue = queue

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator
    entry.coordinator = coordinator

    # ── Webhook registration ─────────────────────────────────────
    webhook_id = entry.data.get("webhook_id")
    if not webhook_id:
        webhook_id = ha_webhook.async_generate_id()
        new_data = {**entry.data, "webhook_id": webhook_id}
        hass.config_entries.async_update_entry(entry, data=new_data)

    ha_webhook.async_register(
        hass,
        DOMAIN,
        "Grocy Helper Scan",
        webhook_id,
        _build_webhook_handler(coordinator),
    )
    _LOGGER.info(
        "Grocy Helper webhook registered. URL: /api/webhook/%s",
        webhook_id,
    )

    # await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


def _build_webhook_handler(coordinator: GrocyHelperCoordinator):
    """Build a webhook handler closure that captures the coordinator."""

    async def _handle_webhook(
        hass: HomeAssistant, webhook_id: str, request: web.Request
    ) -> web.Response:
        """Handle incoming webhook requests.

        Queues barcodes first, then attempts auto-resolve for each
        queued item.  Items that cannot be auto-resolved remain in the
        queue for manual processing via Handle Queue.
        """
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        try:
            results = await process_webhook_payload(coordinator.queue, data)
        except WebhookError as err:
            return web.json_response({"error": str(err)}, status=400)
        except Exception:
            _LOGGER.exception("Unexpected error processing webhook")
            return web.json_response({"error": "Internal error"}, status=500)

        # Attempt auto-resolve for each queued item
        for item_result in results:
            if item_result.status != "queued" or item_result.item_id is None:
                continue
            try:
                resolve_result = await async_try_auto_resolve(
                    coordinator=coordinator,
                    api_bbuddy=coordinator._api_bbuddy,
                    config_entry_data=coordinator._config_entry.data,
                    barcode=item_result.barcode,
                    mode=item_result.mode or coordinator.queue.current_mode.value,
                )
                if resolve_result.success:
                    await coordinator.queue.async_mark_resolved(
                        item_result.item_id,
                        resolve_result.result_text or "auto-resolved",
                    )
                    item_result.status = "auto_resolved"
                    _LOGGER.info("Auto-resolved barcode %s", item_result.barcode)
                elif resolve_result.needs_manual:
                    _LOGGER.info(
                        "Barcode %s needs manual processing: %s",
                        item_result.barcode,
                        resolve_result.error,
                    )
                else:
                    await coordinator.queue.async_mark_failed(
                        item_result.item_id,
                        resolve_result.error or "Auto-resolve failed",
                    )
                    item_result.status = "failed"
            except Exception:
                _LOGGER.exception(
                    "Auto-resolve error for barcode %s", item_result.barcode
                )

        response = WebhookResponse(status="ok", results=results)
        return web.json_response(response.to_dict())

    return _handle_webhook


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # Unregister webhook
    if webhook_id := entry.data.get("webhook_id"):
        ha_webhook.async_unregister(hass, webhook_id)

    # if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
    #     hass.data[DOMAIN].pop(entry.entry_id)
    # return unload_ok
    return True
