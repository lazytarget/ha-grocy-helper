"""Constants for grocy-helper component."""

from typing import Final

DOMAIN: Final = "grocy-helper"

CONF_GROCY_API_URL: Final = "GROCY_API_URL"
CONF_GROCY_API_KEY: Final = "GROCY_API_KEY"

CONF_BBUDDY_API_URL: Final = "BBUDDY_API_URL"
CONF_BBUDDY_API_KEY: Final = "BBUDDY_API_KEY"


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

        GET_PRODUCTS: Final = "api/objects/products"
        GET_PRODUCT_BY_ID: Final = "api/objects/products/%s"
        GET_PRODUCT_BY_BARCODE: Final = "api/stock/products/by-barcode/%s"
        ADD_PRODUCT: Final = "api/objects/products"
        UPDATE_PRODUCT: Final = "api/objects/products"
        
        BBUDDY_SCAN: Final = "api/action/scan"
