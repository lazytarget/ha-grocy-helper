"""API integration with Grocy."""

from aiohttp import ClientSession

from .const import API, ApiException
from .grocytypes import (
    BarcodeBuddyScanRequest,
    BarcodeBuddyScanResponse,
    GrocyProduct,
    ExtendedGrocyProductStockInfo,
)
from .http_requests import async_get, async_post, create_headers


class GrocyAPI:
    """Class to integrate with a Grocy API."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        websession: ClientSession,
    ) -> None:
        def wrap_websession():
            s: ClientSession = None
            if isinstance(websession, ClientSession):
                # Passed a ClientSession instance
                s = websession
            else:
                # Might have passed func, invoke factory method
                s = websession()
            # append with default headers
            create_headers(auth_key=api_key, headers=s.headers)
            return s

        self._session = wrap_websession
        if isinstance(websession, ClientSession):
            # Passed session instance, wrap with default headers
            self._session = wrap_websession
        else:
            # Might have passed func, invoke and append default headers
            self._session = websession()
        self._session = wrap_websession
        self._base_url = base_url
        self._api_key = api_key

    def get_rest_url(self, endpoint: str):
        return "/".join([self._base_url, endpoint])

    async def get_products(self) -> list[GrocyProduct]:
        url = self.get_rest_url(API.URLs.GET_PRODUCTS)
        return await async_get(self._session, url, self._api_key)

    async def get_product_by_barcode(
        self, barcode: str
    ) -> ExtendedGrocyProductStockInfo | None:
        url = self.get_rest_url(API.URLs.GET_PRODUCT_BY_BARCODE) % barcode
        try:
            return await async_get(self._session, url, self._api_key)
        except ApiException as ae:
            if ae.status_code == 400 and ae.error_message.startswith(
                "No product with barcode "
            ):
                return None
            else:
                raise ae

    async def add_product(self, data: GrocyProduct) -> GrocyProduct:
        url = self.get_rest_url(API.URLs.ADD_PRODUCT)
        return await async_post(self._session, url, self._api_key, json_data=data)

    async def bbuddy_scan(
        self, request: BarcodeBuddyScanRequest
    ) -> BarcodeBuddyScanResponse:
        url = self.get_rest_url(API.URLs.BBUDDY_SCAN)
        return await async_post(self._session, url, self._api_key, json_data=request)
