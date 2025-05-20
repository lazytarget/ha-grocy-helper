from __future__ import annotations

from typing import List, TypedDict, Union


class GrocyProduct(TypedDict):
    id: int
    name: str
    description: None
    product_group_id: Union[None, int]
    active: int
    location_id: int
    shopping_location_id: Union[None, int]
    qu_id_purchase: int
    qu_id_stock: int
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
    row_created_timestamp: str
    qu_id_consume: int
    auto_reprint_stock_label: int
    quick_open_amount: int
    qu_id_price: int
    disable_open: int
    default_purchase_price_type: int
