"""API integration with Grocy."""

from typing import Any

from aiohttp import ClientSession

from .const import API, ApiException
from .grocytypes import (
    GrocyAddProductQuantityUnitConversion,
    GrocyAddStockProduct,
    GrocyLocation,
    GrocyProductPresets,
    GrocyProduct,
    ExtendedGrocyProductStockInfo,
    GrocyProductBarcode,
    GrocyProductGroup,
    GrocyQuantityUnit,
    GrocyQuantityUnitConversionResolved,
    GrocyRecipe,
    GrocyShoppingLocation,
    GrocyStockEntry,
)
from .http_requests import async_get, async_post, async_put
from .utils import try_parse_int


def _parse_positive_int_or_none(value: Any) -> int | None:
    success, parsed = try_parse_int(value)
    if not success or parsed <= 0:
        return None
    return parsed


def _parse_due_days_or_none(value: Any) -> int | None:
    success, parsed = try_parse_int(value)
    if not success:
        return None
    if parsed == -1 or parsed > 0:
        return parsed
    return None


def _parse_bool_or_none(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off", ""}:
            return False
    return bool(value)


def parse_product_presets(settings: dict[str, Any]) -> GrocyProductPresets:
    """Extract product preset defaults from Grocy user settings."""
    return {
        "location_id": _parse_positive_int_or_none(
            settings.get("product_presets_location_id")
        ),
        "product_group_id": _parse_positive_int_or_none(
            settings.get("product_presets_product_group_id")
        ),
        "qu_id": _parse_positive_int_or_none(settings.get("product_presets_qu_id")),
        "default_best_before_days": _parse_due_days_or_none(
            settings.get("product_presets_default_due_days")
        ),
        "treat_opened_as_out_of_stock": _parse_bool_or_none(
            settings.get("product_presets_treat_opened_as_out_of_stock")
        ),
    }


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

    async def get_shopping_locations(self) -> list[GrocyShoppingLocation]:
        url = self.get_rest_url(API.URLs.GET_SHOPPING_LOCATIONS)
        return await async_get(self._session, url, self._api_key)

    async def get_quantityunits(self) -> list[GrocyQuantityUnit]:
        url = self.get_rest_url(API.URLs.GET_QUANTITYUNITS)
        return await async_get(self._session, url, self._api_key)

    async def get_products(self) -> list[GrocyProduct]:
        url = self.get_rest_url(API.URLs.GET_PRODUCTS)
        return await async_get(self._session, url, self._api_key)

    async def get_product_barcode_by_id(
        self, product_barcode_id: int
    ) -> GrocyProductBarcode | None:
        url = self.get_rest_url(API.URLs.GET_PRODUCT_BARCODE_BY_ID) % product_barcode_id
        return await async_get(
            self._session, url, self._api_key, return_none_when_404=True
        )

    async def get_stock_entries_by_product_id(
        self, product_id: int
    ) -> list[GrocyStockEntry]:
        url = self.get_rest_url(API.URLs.GET_STOCK_ENTRIES_BY_PRODUCT_ID) % product_id
        return await async_get(self._session, url, self._api_key)

    async def transfer_stock_entry(
        self, product_id: int, data: dict
    ) -> list[GrocyStockEntry]:
        url = self.get_rest_url(API.URLs.TRANSFER_STOCK_ENTRY) % product_id
        return await async_post(self._session, url, self._api_key, json_data=data)

    async def get_product_by_id(self, product_id: int) -> GrocyProduct | None:
        url = self.get_rest_url(API.URLs.GET_PRODUCT_BY_ID) % product_id
        return await async_get(
            self._session, url, self._api_key, return_none_when_404=True
        )

    async def get_stock_by_stock_id(self, stock_id: str) -> dict[str, Any] | None:
        url = self.get_rest_url(API.URLs.GET_STOCK_ENTRY_BY_ID)
        params = [("query[]", f"stock_id={stock_id}")]
        response = await async_get(self._session, url, self._api_key, params=params)
        if response and isinstance(response, list) and len(response) > 0:
            if len(response) > 1:
                raise ApiException(400, f"Multiple stock entries found for stock_id {stock_id}: {response}")
            return response[0]
        return None

    async def get_stock_product_by_id(
        self, product_id: int
    ) -> ExtendedGrocyProductStockInfo | None:
        url = self.get_rest_url(API.URLs.GET_STOCK_PRODUCT_BY_ID) % product_id
        try:
            return await async_get(self._session, url, self._api_key)
        except ApiException as ae:
            if ae.status_code == 400 and ae.error_message.startswith(
                "No product with barcode "
            ):
                return None
            else:
                raise ae

    async def get_stock_product_by_barcode(
        self, barcode: str
    ) -> ExtendedGrocyProductStockInfo | None:
        url = self.get_rest_url(API.URLs.GET_STOCK_PRODUCT_BY_BARCODE) % barcode
        try:
            return await async_get(self._session, url, self._api_key)
        except ApiException as ae:
            if ae.status_code == 400 and ae.error_message.startswith(
                "No product with barcode "
            ):
                return None
            else:
                raise ae

    async def add_stock_product(
        self, product_id: int, data: GrocyAddStockProduct
    ) -> list[dict]:
        url = self.get_rest_url(API.URLs.ADD_STOCK_PRODUCT) % product_id
        response = await async_post(self._session, url, self._api_key, json_data=data)
        return response

    async def consume_stock_product(
        self, product_id: int, amount: float, **kwargs
    ) -> list[dict]:
        """Consume a product from stock."""
        url = self.get_rest_url(API.URLs.CONSUME_STOCK_PRODUCT) % product_id
        data: dict[str, Any] = {"amount": amount, **kwargs}
        return await async_post(self._session, url, self._api_key, json_data=data)

    async def add_product(self, data: GrocyProduct) -> GrocyProduct:
        url = self.get_rest_url(API.URLs.ADD_PRODUCT)
        response = await async_post(self._session, url, self._api_key, json_data=data)
        obj_id = int(response["created_object_id"])
        if obj_id > 0:
            product = await self.get_product_by_id(obj_id)
            return product
        return response

    async def update_product(self, product_id: int, data: GrocyProduct):
        url = self.get_rest_url(API.URLs.UPDATE_PRODUCT) % product_id
        response = await async_put(self._session, url, self._api_key, json_data=data)
        return response

    async def add_product_barcode(
        self, data: GrocyProductBarcode
    ) -> GrocyProductBarcode:
        url = self.get_rest_url(API.URLs.ADD_PRODUCT_BARCODE)
        response = await async_post(self._session, url, self._api_key, json_data=data)
        obj_id = int(response["created_object_id"])
        if obj_id > 0:
            obj = await self.get_product_barcode_by_id(obj_id)
            return obj
        return response

    async def add_product_quantity_unit_conversion(
        self, data: GrocyAddProductQuantityUnitConversion
    ) -> dict:
        url = self.get_rest_url(API.URLs.ADD_PRODUCT_QUANTITY_UNIT_CONVERSION)
        response = await async_post(self._session, url, self._api_key, json_data=data)
        return response

    async def resolve_quantity_unit_conversions_for_product_id(
        self, product_id: int
    ) -> list[GrocyQuantityUnitConversionResolved]:
        url = self.get_rest_url(API.URLs.GET_QUANTITY_UNIT_CONVERSIONS_RESOLVED)
        params = [("query[]", f"product_id={product_id}")]
        return await async_get(self._session, url, self._api_key, params=params)

    async def get_product_groups(self) -> list[GrocyProductGroup]:
        url = self.get_rest_url(API.URLs.GET_PRODUCT_GROUPS)
        return await async_get(self._session, url, self._api_key)

    async def get_recipes(self) -> list[GrocyRecipe]:
        url = self.get_rest_url(API.URLs.GET_RECIPES)
        return await async_get(self._session, url, self._api_key)

    async def get_user_settings(self) -> dict[str, Any]:
        url = self.get_rest_url(API.URLs.GET_USER_SETTINGS)
        return await async_get(self._session, url, self._api_key)

    async def create_recipe(self, data: GrocyRecipe):
        url = self.get_rest_url(API.URLs.CREATE_RECIPE)
        response = await async_post(self._session, url, self._api_key, json_data=data)
        return response

    async def update_recipe(self, recipe_id: int, data: GrocyRecipe):
        url = self.get_rest_url(API.URLs.UPDATE_RECIPE) % recipe_id
        response = await async_put(self._session, url, self._api_key, json_data=data)
        return response

    async def get_recipe_fulfillment(self, recipe_id: int) -> dict:
        url = self.get_rest_url(API.URLs.GET_RECIPE_FULFILLMENT) % recipe_id
        return await async_get(self._session, url, self._api_key)

    async def get_recipes_pos_resolved(self, recipe_id: int) -> list[dict]:
        url = self.get_rest_url(API.URLs.GET_RECIPES_POS_RESOLVED)
        params = [("query[]", f"recipe_id={recipe_id}")]
        return await async_get(self._session, url, self._api_key, params=params)

    async def consume_recipe(self, recipe_id: int) -> None:
        url = self.get_rest_url(API.URLs.CONSUME_RECIPE) % recipe_id
        await async_post(self._session, url, self._api_key, json_data={})

    async def print_label_for_product(self, product_id: int) -> dict:
        url = self.get_rest_url(API.URLs.PRINT_LABEL_FOR_PRODUCT) % product_id
        return await async_get(self._session, url, self._api_key)

    async def print_label_for_stock_entry(self, stock_entry_id: int) -> dict:
        url = self.get_rest_url(API.URLs.PRINT_LABEL_FOR_STOCK_ENTRY) % stock_entry_id
        return await async_get(self._session, url, self._api_key)

    async def print_label_for_recipe(self, recipe_id: int) -> dict:
        url = self.get_rest_url(API.URLs.PRINT_LABEL_FOR_RECIPE) % recipe_id
        return await async_get(self._session, url, self._api_key)
