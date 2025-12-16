"""API integration with Grocy."""

from aiohttp import ClientSession

from .const import API, ApiException
from .grocytypes import GrocyProduct, ExtendedGrocyProductStockInfo
from .http_requests import async_get, async_post


class GrocyAPI:
    """Class to integrate with a Grocy API."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        websession: ClientSession,
    ) -> None:
        self._session = websession
        self._base_url = base_url
        self._api_key = api_key

    def get_rest_url(self, endpoint: str):
        return "/".join([self._base_url, endpoint])

    async def get_products(self) -> list[GrocyProduct]:
        url = self.get_rest_url(API.URLs.GET_PRODUCTS)
        return await async_get(self._session, url, self._api_key)

    async def get_product_by_barcode(self, barcode: str) -> ExtendedGrocyProductStockInfo | None:
        url = self.get_rest_url(API.URLs.GET_PRODUCT_BY_BARCODE) % barcode
        try:
            return await async_get(self._session, url, self._api_key)
        except ApiException as ae:
            if ae.status_code == 400 and ae.error_message.startswith("No product with barcode "):
                return None
            else:
                raise ae

    async def add_product(self, data: GrocyProduct) -> GrocyProduct:
        url = self.get_rest_url(API.URLs.ADD_PRODUCT)
        return await async_post(self._session, url, self._api_key, json_data=data)
