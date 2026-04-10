"""Constants for grocy-helper component."""

from enum import StrEnum
from typing import Final

DOMAIN: Final = "grocy_helper"
DEFAULT_SCAN_INTERVAL: Final = 5    # minutes

CONF_GROCY_API_URL: Final = "GROCY_API_URL"
CONF_GROCY_API_KEY: Final = "GROCY_API_KEY"

CONF_BBUDDY_API_URL: Final = "BBUDDY_API_URL"
CONF_BBUDDY_API_KEY: Final = "BBUDDY_API_KEY"

CONF_DEFAULT_LOCATION: Final = "DEFAULT_LOCATION"
CONF_DEFAULT_LOCATION_FRIDGE: Final = "DEFAULT_LOCATION_FRIDGE"
CONF_DEFAULT_LOCATION_FREEZER: Final = "DEFAULT_LOCATION_FREEZER"
CONF_DEFAULT_LOCATION_RECIPE_RESULT: Final = "DEFAULT_LOCATION_RECIPE_RESULT"
CONF_DEFAULT_PRODUCT_GROUP_FOR_RECIPE_RESULT: Final = "DEFAULT_PRODUCT_GROUP_FOR_RECIPE_RESULT"
CONF_ENABLE_PRINTING: Final = "ENABLE_PRINTING"
CONF_ENABLE_AUTO_PRINT: Final = "ENABLE_AUTO_PRINT"
CONF_ENABLE_PRICES: Final = "ENABLE_PRICES"

STORAGE_KEY_QUEUE: Final = "grocy_helper.queue"
STORAGE_VERSION_QUEUE: Final = 1

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

class ServiceCalls(StrEnum):
    """Services for the Grocy-helper integration"""
    RESOLVE_QUANTITY_UNIT_CONVERSION_FOR_PRODUCT = "resolve_quantity_unit_conversion_for_product"

class API:
    class URLs:
        """URLs and API Endpoints"""

        GET_LOCATIONS: Final = "api/objects/locations"
        GET_SHOPPING_LOCATIONS: Final = "api/objects/shopping_locations"
        GET_QUANTITYUNITS: Final = "api/objects/quantity_units"
        GET_QUANTITY_UNIT_CONVERSIONS_RESOLVED: Final = "api/objects/quantity_unit_conversions_resolved"
        GET_PRODUCTS: Final = "api/objects/products"
        GET_PRODUCT_BY_ID: Final = "api/objects/products/%s"
        GET_STOCK_PRODUCT_BY_ID: Final = "api/stock/products/%s"
        GET_STOCK_PRODUCT_BY_BARCODE: Final = "api/stock/products/by-barcode/%s"
        GET_STOCK_ENTRIES_BY_PRODUCT_ID: Final = "api/stock/products/%s/entries"
        GET_STOCK_ENTRY_BY_ID: Final = "api/objects/stock"
        GET_PRODUCT_BARCODE_BY_ID: Final = "api/objects/product_barcodes/%s"
        TRANSFER_STOCK_ENTRY: Final = "api/stock/products/%s/transfer"
        ADD_STOCK_PRODUCT: Final = "api/stock/products/%s/add"
        CONSUME_STOCK_PRODUCT: Final = "api/stock/products/%s/consume"
        ADD_PRODUCT: Final = "api/objects/products"
        ADD_PRODUCT_BARCODE: Final = "api/objects/product_barcodes"
        UPDATE_PRODUCT: Final = "api/objects/products/%s"
        ADD_PRODUCT_QUANTITY_UNIT_CONVERSION: Final = "api/objects/quantity_unit_conversions"
        GET_PRODUCT_GROUPS: Final = "api/objects/product_groups"
        GET_RECIPES: Final = "api/objects/recipes"
        CREATE_RECIPE: Final = "api/objects/recipes"
        UPDATE_RECIPE: Final = "api/objects/recipes/%s"

        GET_RECIPE_FULFILLMENT: Final = "api/recipes/%s/fulfillment"
        GET_RECIPES_POS_RESOLVED: Final = "api/objects/recipes_pos_resolved"
        CONSUME_RECIPE: Final = "api/recipes/%s/consume"

        PRINT_LABEL_FOR_PRODUCT: Final = "api/stock/products/%s/printlabel"
        PRINT_LABEL_FOR_STOCK_ENTRY: Final = "api/stock/entry/%s/printlabel"
        PRINT_LABEL_FOR_RECIPE: Final = "api/recipes/%s/printlabel"

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
        "generic_name",
        "product_type",
        "expiration_date",
        "categories_hierarchy",
        "nutriments",
        "nutriments_estimated",
    ]

# TODO: Conditional check if development env
DEV_CONST = {
    "default_scan_mode": SCAN_MODE.SCAN_BBUDDY,
    "default_barcode": "4011800420413",
}


# Fields whose suggested values should NOT be converted to ``str``
# (they are numeric / boolean and the UI must receive them as-is).
NUMERIC_FIELDS = frozenset(
    {
        "should_not_be_frozen",
        "calories_per_100",
        "default_best_before_days",
        "default_best_before_days_after_open",
        "default_best_before_days_after_freezing",
        "default_best_before_days_after_thawing",
    }
)
# TODO: This is a lazy hack. Improve!