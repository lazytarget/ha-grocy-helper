from __future__ import annotations

from typing import List, TypedDict, Union

class GrocyLocation(TypedDict):
    id: int
    name: str
    description: str | None
    row_created_timestamp: str
    is_freezer: int
    active: int

class GrocyShoppingLocation(TypedDict):
    id: int
    name: str
    description: str | None
    row_created_timestamp: str
    active: int

class GrocyQuantityUnit(TypedDict):
    id: int
    name: str
    description: str | None
    row_created_timestamp: str
    name_plural: str
    plural_forms: str | None
    active: int

class GrocyProductBarcode(TypedDict):
    id: int
    product_id: int
    barcode: str
    note: str
    qu_id: int | None
    amount: float | None
    shopping_location_id: int | None
    last_price: float | None
    row_created_timestamp: str


class GrocyProduct(TypedDict):
    id: int
    # Required
    name: str
    location_id: int
    qu_id_purchase: int
    qu_id_stock: int
    qu_id_price: int
    qu_id_consume: int
    row_created_timestamp: str
    # Optional
    description: None
    product_group_id: Union[None, int]
    active: int
    shopping_location_id: Union[None, int]
    min_stock_amount: int
    default_best_before_days: int
    default_best_before_days_after_open: int
    default_best_before_days_after_freezing: int
    default_best_before_days_after_thawing: int
    picture_file_name: None
    enable_tare_weight_handling: int
    tare_weight: int
    not_check_stock_fulfillment_for_recipes: int
    parent_product_id: Union[None, int]
    calories: int
    cumulate_min_stock_amount_of_sub_products: int
    due_type: int
    quick_consume_amount: int
    hide_on_stock_overview: int
    default_stock_label_type: int
    should_not_be_frozen: int
    treat_opened_as_out_of_stock: int
    no_own_stock: int
    default_consume_location_id: Union[None, int]
    move_on_open: int
    auto_reprint_stock_label: int
    quick_open_amount: int
    disable_open: int
    default_purchase_price_type: int


class ExtendedGrocyProductStockInfo(TypedDict):
    stock_amount: int
    stock_value: int
    last_purchased: str
    last_used: str
    product: GrocyProduct
    product_barcodes: list[GrocyProduct]


class BarcodeBuddyScanRequest(TypedDict):
    barcode: str
    price: float | None
    bestBeforeInDays: int | None

class BarcodeBuddyScanResultResponse(TypedDict):
    result: str
    http_code: int


class BarcodeBuddyScanDataResponse(TypedDict):
    result: str


class BarcodeBuddyScanResponse(TypedDict):
    data: BarcodeBuddyScanDataResponse
    result: BarcodeBuddyScanResultResponse

class GrocyMasterData(TypedDict):
    locations: list[GrocyLocation]
    shopping_locations: list[GrocyShoppingLocation]
    quantity_units: list[GrocyQuantityUnit]
    products: list[GrocyProduct]

class OpenFoodFactsProductNutriments(TypedDict):
    energy_kcal_100g: float | None
    fat_100g: float | None
    saturated_fat_100g: float | None
    carbohydrates_100g: float | None
    sugars_100g: float | None
    proteins_100g: float | None
    salt_100g: float | None

class OpenFoodFactsProduct(TypedDict):
    brand_owner: str | None
    brands: str | None
    quantity: str | None
    product_quantity: float | None
    product_quantity_unit: str | None
    product_name: str | None
    product_type: str | None
    nutriments: OpenFoodFactsProductNutriments | None
    categories: list[str]
