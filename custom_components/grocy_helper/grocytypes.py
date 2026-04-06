from __future__ import annotations

from typing import TypedDict, Generic, TypeVar, Any

_DataT = TypeVar("_DataT", default=dict[str, Any])


class ServiceCallResponse(Generic[_DataT], TypedDict):
    success: bool
    message: str | None = None
    data: _DataT | None = None


class GrocyLocation(TypedDict):
    id: int
    name: str
    description: str | None
    row_created_timestamp: str
    is_freezer: int
    active: int
    userfields: dict[str, Any] | None


class GrocyShoppingLocation(TypedDict):
    id: int
    name: str
    description: str | None
    row_created_timestamp: str
    active: int
    userfields: dict[str, Any] | None


class GrocyQuantityUnit(TypedDict):
    id: int
    name: str
    description: str | None
    row_created_timestamp: str
    name_plural: str
    plural_forms: str | None
    active: int
    userfields: dict[str, Any] | None


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
    userfields: dict[str, Any] | None


class GrocyAddStockProduct(TypedDict):
    amount: float
    transaction_type: str
    price: float | None
    best_before_date: str | None
    shopping_location_id: int | None


class GrocyAddProductQuantityUnitConversion(TypedDict):
    from_qu_id: int
    to_qu_id: int
    factor: float
    product_id: int
    row_created_timestamp: str

class GrocyProductGroup(TypedDict):
    id: int
    name: str
    description: str | None
    row_created_timestamp: str
    active: int
    userfields: dict[str, Any] | None

class GrocyProduct(TypedDict):
    id: int
    # Required
    name: str
    location_id: int
    qu_id_stock: int
    qu_id_purchase: int
    qu_id_consume: int
    qu_id_price: int
    row_created_timestamp: str
    # Optional
    description: str | None
    product_group_id: int | None
    active: int
    shopping_location_id: int | None
    min_stock_amount: int
    default_best_before_days: int
    default_best_before_days_after_open: int
    default_best_before_days_after_freezing: int
    default_best_before_days_after_thawing: int
    picture_file_name: str | None
    enable_tare_weight_handling: int
    tare_weight: int
    not_check_stock_fulfillment_for_recipes: int
    parent_product_id: int | None
    calories: int | None
    cumulate_min_stock_amount_of_sub_products: int
    due_type: int
    quick_consume_amount: int
    hide_on_stock_overview: int
    default_stock_label_type: int
    should_not_be_frozen: int
    treat_opened_as_out_of_stock: int
    no_own_stock: int
    default_consume_location_id: int | None
    move_on_open: int
    auto_reprint_stock_label: int
    quick_open_amount: int
    disable_open: int
    default_purchase_price_type: int
    userfields: dict[str, Any] | None


class ExtendedGrocyProductStockInfo(TypedDict):
    stock_amount: int
    stock_value: int
    last_purchased: str
    last_used: str
    product: GrocyProduct
    product_barcodes: list[GrocyProductBarcode]
    default_shopping_location_id: int | None


class GrocyStockEntry(TypedDict):
    id: int
    stock_id: str
    product_id: int
    location_id: int
    shopping_location_id: int | None
    amount: float
    purchased_date: str | None
    best_before_date: str | None
    price: float | None
    open: int
    opened_date: str | None
    note: str | None
    row_created_timestamp: str


class GrocyQuantityUnitConversionResolved(TypedDict):
    id: int
    product_id: int
    from_qu_id: int
    from_qu_name: str
    from_qu_name_plural: str | None
    to_qu_id: int
    to_qu_name: str
    to_qu_name_plural: str | None
    factor: float
    path: str


class GrocyQuantityUnitConversionResult(GrocyQuantityUnitConversionResolved):
    from_amount: float
    to_amount: float


class GrocyRecipe(TypedDict):
    id: int
    name: str
    description: str | None
    row_created_timestamp: str
    picture_file_name: str | None
    base_servings: int
    desired_servings: int
    not_check_shoppinglist: int
    type: str
    product_id: int | None
    userfields: dict[str, Any] | None


class GrocyRecipeFulfillment(TypedDict):
    recipe_id: int
    need_fulfilled: bool
    need_fulfilled_with_shopping_list: bool
    missing_products_count: int
    costs: float
    costs_per_serving: float
    calories: float
    due_scope: int
    product_names_comma_separated: str | None
    prices_incomplete: int


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
    product_groups: list[GrocyProductGroup]
    recipes: list[GrocyRecipe]
    known_qu: dict[str, GrocyQuantityUnit | None]


class OpenFoodFactsProductNutriments(TypedDict):
    energy_kcal: float | None
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
    generic_name: str | None
    nutriments: OpenFoodFactsProductNutriments | None
    categories: list[str]
    categories_hierarchy: list[str]


class BarcodeLookup(TypedDict):
    barcode: str
    off: OpenFoodFactsProduct | None
    ica: dict | None
    lookup_output: str
    product_aliases: list[str]
