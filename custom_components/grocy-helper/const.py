"""Constants for grocy-helper component."""

from enum import StrEnum
from typing import Final

DOMAIN: Final = "grocy-helper"
DEFAULT_SCAN_INTERVAL: Final = 5    # minutes

CONF_GROCY_API_URL: Final = "GROCY_API_URL"
CONF_GROCY_API_KEY: Final = "GROCY_API_KEY"

CONF_BBUDDY_API_URL: Final = "BBUDDY_API_URL"
CONF_BBUDDY_API_KEY: Final = "BBUDDY_API_KEY"

class SCAN_MODE(StrEnum):
    # BBuddy
    CONSUME = "BBUDDY-C"
    CONSUME_SPOILED = "BBUDDY-CS"
    CONSUME_ALL = "BBUDDY-CA"
    PURCHASE = "BBUDDY-P"
    OPEN = "BBUDDY-O"
    INVENTORY = "BBUDDY-I"
    ADD_TO_SHOPPING_LIST = "BBUDDY-AS"
    QUANTITY = "BBUDDY-Q-"
    # Custom
    SCAN_BBUDDY = "SCAN-BBUDDY"
    TRANSFER = "TRANSFER"
    PROVISION = "PROVISION-BARCODE"

class ApiException(Exception):
    def __init__(self, status_code, error_message):
        # message = f"{status_code}: {error_message}"
        # message = error_message
        self.status_code = status_code
        self.error_message = error_message
        super().__init__()

    status_code: int
    error_message: str


class API:
    class URLs:
        """URLs and API Endpoints"""

        GET_LOCATIONS: Final = "api/objects/locations"
        GET_SHOPPING_LOCATIONS: Final = "api/objects/shopping_locations"
        GET_QUANTITYUNITS: Final = "api/objects/quantity_units"
        GET_PRODUCTS: Final = "api/objects/products"
        GET_PRODUCT_BY_ID: Final = "api/objects/products/%s"
        GET_PRODUCT_BY_BARCODE: Final = "api/stock/products/by-barcode/%s"
        GET_PRODUCT_BARCODE_BY_ID: Final = "api/objects/product_barcodes/%s"
        GET_STOCK_ENTRIES_BY_PRODUCT_ID: Final = "api/stock/products/%s/entries"
        ADD_PRODUCT: Final = "api/objects/products"
        ADD_PRODUCT_BARCODE: Final = "api/objects/product_barcodes"
        UPDATE_PRODUCT: Final = "api/objects/products"

        BBUDDY_SCAN: Final = "api/action/scan"
        BBUDDY_GET_MODE: Final = "api/state/getmode"
        BBUDDY_SET_MODE: Final = "api/state/setmode"

class OpenFoodFacts:
    APIv2 = "https://world.openfoodfacts.org/api/v2/product/{}.json"
    DEFAULT_FIELDS: Final = [
        "brand_owner",
        "brands",
        "quantity",
        "product_quantity",
        "product_quantity_unit",
        "serving_quantity",
        "serving_quantity_unit",
        "product_name",
        "product_type",
        "expiration_date",
        "categories_hierarchy",
        "nutriments",
        "nutriments_estimated",
    ]
