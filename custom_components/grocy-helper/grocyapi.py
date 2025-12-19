"""API integration with Grocy."""

from aiohttp import ClientSession

from .const import API, ApiException
from .grocytypes import (
    GrocyLocation,
    GrocyProduct,
    ExtendedGrocyProductStockInfo,
    GrocyProductBarcode,
    GrocyQuantityUnit,
)
from .http_requests import async_get, async_post


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
            # create_headers(auth_key=api_key, headers=s.headers)
            return s

        self._session = wrap_websession
        self._base_url = base_url
        self._api_key = api_key

    def get_rest_url(self, endpoint: str):
        return "/".join([self._base_url, endpoint])

    async def get_locations(self) -> list[GrocyLocation]:
        url = self.get_rest_url(API.URLs.GET_LOCATIONS)
        return await async_get(self._session, url, self._api_key)

    async def get_quantityunits(self) -> list[GrocyQuantityUnit]:
        url = self.get_rest_url(API.URLs.GET_QUANTITYUNITS)
        return await async_get(self._session, url, self._api_key)

    async def get_products(self) -> list[GrocyProduct]:
        url = self.get_rest_url(API.URLs.GET_PRODUCTS)
        return await async_get(self._session, url, self._api_key)

    async def get_product_barcode_by_id(
        self, product_barcode_id: int
    ) -> ExtendedGrocyProductStockInfo | None:
        url = self.get_rest_url(API.URLs.GET_PRODUCT_BARCODE_BY_ID) % product_barcode_id
        return await async_get(
            self._session, url, self._api_key, return_none_when_404=True
        )

    async def get_product_by_id(
        self, product_id: int
    ) -> ExtendedGrocyProductStockInfo | None:
        url = self.get_rest_url(API.URLs.GET_PRODUCT_BY_ID) % product_id
        return await async_get(
            self._session, url, self._api_key, return_none_when_404=True
        )

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
        response = await async_post(self._session, url, self._api_key, data=data)
        obj_id = response["created_object_id"]
        if obj_id > 0:
            product = await self.get_product_by_id(obj_id)
            return product
        return response

    async def add_product_barcode(self, data: GrocyProductBarcode) -> GrocyProductBarcode:
        url = self.get_rest_url(API.URLs.ADD_PRODUCT_BARCODE)
        response = await async_post(self._session, url, self._api_key, data=data)
        obj_id = response["created_object_id"]
        if obj_id > 0:
            product = await self.get_product_barcode_by_id(obj_id)
            return product
        return response
