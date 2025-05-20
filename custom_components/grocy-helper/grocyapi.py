"""API integration with Grocy."""

from aiohttp import ClientSession

from .const import API
from .grocytypes import GrocyProduct
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

    async def add_product(self) -> GrocyProduct:
        url = self.get_rest_url(API.URLs.ADD_PRODUCT)
        return await async_post(self._session, url, self._api_key)
