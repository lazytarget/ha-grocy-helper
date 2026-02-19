"""Form field builders for barcode scanning workflow.

This module contains all the form field construction logic for the
ScanSession workflow. It's separated from ScanSession to reduce file
size and improve maintainability.
"""

from __future__ import annotations

from typing import Any

from .const import SCAN_MODE
from .grocytypes import GrocyMasterData, GrocyProduct
from .scan_types import FieldType, FormField, NumberMode, SelectMode, SelectOption


class ScanFormBuilder:
    """Build form fields for the scanning workflow."""

    def __init__(self, masterdata: GrocyMasterData):
        """Initialize the form builder.
        
        Parameters
        ----------
        masterdata:
            A ``GrocyMasterData`` dict with locations, products, etc.
        """
        self._masterdata = masterdata

    def build_scan_start_fields(
        self, scan_mode: SCAN_MODE | None
    ) -> list[FormField]:
        """Build fields for the scan-start form."""

        bbuddy_mode_str = scan_mode.name if scan_mode is not None else "Unknown"

        return [
            FormField(
                key="mode",
                field_type=FieldType.SELECT,
                required=False,
                suggested_value=SCAN_MODE.SCAN_BBUDDY,
                select_mode=SelectMode.LIST,
                options=[
                    SelectOption(
                        value=SCAN_MODE.SCAN_BBUDDY,
                        label=f"Barcode Buddy ({bbuddy_mode_str})",
                    ),
                    SelectOption(value=SCAN_MODE.CONSUME, label="Consume"),
                    SelectOption(
                        value=SCAN_MODE.CONSUME_SPOILED,
                        label="Consume (Spoiled)",
                    ),
                    SelectOption(
                        value=SCAN_MODE.CONSUME_ALL,
                        label="Consume (All)",
                    ),
                    SelectOption(
                        value=SCAN_MODE.PURCHASE,
                        label="Purchase / Produce",
                    ),
                    SelectOption(value=SCAN_MODE.TRANSFER, label="Transfer"),
                    SelectOption(value=SCAN_MODE.OPEN, label="Open"),
                    SelectOption(value=SCAN_MODE.INVENTORY, label="Inventory"),
                    SelectOption(
                        value=SCAN_MODE.ADD_TO_SHOPPING_LIST,
                        label="Add to Shopping list",
                    ),
                    SelectOption(
                        value=SCAN_MODE.PROVISION,
                        label="Provision barcode",
                    ),
                ],
            ),
            FormField(
                key="barcodes",
                field_type=FieldType.TEXT,
                required=True,
                multiline=True,
                suggested_value="4011800420413",  # DEV default
            ),
        ]

    def build_match_product_fields(
        self,
        suggested_products: list[GrocyProduct],
        aliases: list[str],
        allow_parent: bool,
        current_lookup: dict | None = None,
        suggested_values: dict[str, str] | None = None,
    ) -> list[FormField]:
        """Build fields for the match-to-product form."""

        masterdata = self._masterdata
        suggested_values = suggested_values or {}
        lookup = current_lookup

        child_products = [
            p for p in masterdata["products"] if p["parent_product_id"]
        ]
        parent_product_ids = [
            p["parent_product_id"] for p in child_products
        ]
        parent_products = [
            p for p in masterdata["products"] if p["id"] in parent_product_ids
        ]

        suggested_product_ids = [p["id"] for p in suggested_products]
        non_suggested = [
            p
            for p in masterdata["products"]
            if p["id"] not in suggested_product_ids
            and p["id"] not in parent_product_ids
        ]
        non_suggested.sort(key=lambda p: p["name"])

        product_options = list(suggested_products) + non_suggested
        prods = [
            SelectOption(value=str(p["id"]), label=p["name"])
            for p in product_options
            if p["active"] == 1
        ]

        selected_product_id = ""
        resolved_aliases = aliases or (lookup or {}).get("product_aliases", [])
        if len(suggested_products) == 0 and len(resolved_aliases) > 0:
            selected_product_id = resolved_aliases[0]
        elif len(suggested_products) > 0:
            prods.insert(
                len(suggested_products),
                SelectOption(
                    value="-1",
                    label=f"\t[{len(suggested_products)} SUGGESTIONS ABOVE]",
                ),
            )
            if len(suggested_products) == 1:
                selected_product_id = str(
                    suggested_values.get(
                        "product_id", suggested_products[0]["id"]
                    )
                )
            else:
                selected_product_id = "-1"

        fields: list[FormField] = [
            FormField(
                key="product_id",
                field_type=FieldType.SELECT,
                required=True,
                suggested_value=selected_product_id,
                options=prods,
                custom_value=True,
                select_mode=SelectMode.DROPDOWN,
            ),
        ]

        if allow_parent:
            fields.append(
                FormField(
                    key="parent_product",
                    field_type=FieldType.SELECT,
                    required=False,
                    suggested_value=suggested_values.get("parent_product"),
                    options=[
                        SelectOption(value=str(p["id"]), label=p["name"])
                        for p in parent_products
                        if p["active"] == 1
                    ],
                    custom_value=True,
                    select_mode=SelectMode.DROPDOWN,
                ),
            )

        return fields

    def build_create_product_fields(
        self,
        suggested: dict[str, Any],
        creating_parent: bool = False,
    ) -> list[FormField]:
        """Build fields for the create-product form."""

        loc_options = self._location_options()
        qu_options = self._qu_options()

        fields: list[FormField] = [
            FormField(
                key="name",
                field_type=FieldType.TEXT,
                required=True,
                suggested_value=suggested.get("name"),
            ),
        ]

        if not creating_parent:
            fields.extend([
                FormField(
                    key="location_id",
                    field_type=FieldType.SELECT,
                    required=True,
                    suggested_value=self._str_val(suggested.get("location_id")),
                    options=loc_options,
                    select_mode=SelectMode.DROPDOWN,
                ),
                FormField(
                    key="should_not_be_frozen",
                    field_type=FieldType.BOOLEAN,
                    required=True,
                    default=suggested.get("should_not_be_frozen", False),
                ),
                FormField(
                    key="default_best_before_days",
                    field_type=FieldType.NUMBER,
                    required=False,
                    suggested_value=suggested.get("default_best_before_days"),
                    step=1,
                ),
                FormField(
                    key="default_best_before_days_after_open",
                    field_type=FieldType.NUMBER,
                    required=False,
                    suggested_value=suggested.get(
                        "default_best_before_days_after_open"
                    ),
                    step=1,
                ),
            ])

        fields.append(
            FormField(
                key="qu_id_stock",
                field_type=FieldType.SELECT,
                required=True,
                suggested_value=self._str_val(
                    suggested.get("qu_id_stock", suggested.get("qu_id"))
                ),
                options=qu_options,
                select_mode=SelectMode.DROPDOWN,
            ),
        )

        if not creating_parent:
            fields.extend([
                FormField(
                    key="qu_id_purchase",
                    field_type=FieldType.SELECT,
                    required=True,
                    suggested_value=self._str_val(
                        suggested.get(
                            "qu_id_purchase", suggested.get("qu_id")
                        )
                    ),
                    options=qu_options,
                    select_mode=SelectMode.DROPDOWN,
                ),
                FormField(
                    key="qu_id_consume",
                    field_type=FieldType.SELECT,
                    required=True,
                    suggested_value=self._str_val(
                        suggested.get(
                            "qu_id_consume", suggested.get("qu_id")
                        )
                    ),
                    options=qu_options,
                    select_mode=SelectMode.DROPDOWN,
                ),
            ])

        fields.append(
            FormField(
                key="qu_id_price",
                field_type=FieldType.SELECT,
                required=True,
                suggested_value=self._str_val(
                    suggested.get("qu_id_price", suggested.get("qu_id"))
                ),
                options=qu_options,
                select_mode=SelectMode.DROPDOWN,
            ),
        )

        return fields

    def build_create_barcode_fields(
        self, suggested: dict[str, Any]
    ) -> list[FormField]:
        """Build fields for the create-barcode form."""

        masterdata = self._masterdata
        shopping_locations = sorted(
            masterdata["shopping_locations"], key=lambda loc: loc["name"]
        )
        shop_options = [
            SelectOption(value=str(s["id"]), label=s["name"])
            for s in shopping_locations
        ]
        qu_options = self._qu_options()

        return [
            FormField(
                key="note",
                field_type=FieldType.TEXT,
                required=False,
                suggested_value=suggested.get("note"),
            ),
            FormField(
                key="shopping_location_id",
                field_type=FieldType.SELECT,
                required=False,
                options=shop_options,
                select_mode=SelectMode.DROPDOWN,
            ),
            FormField(
                key="qu_id",
                field_type=FieldType.SELECT,
                required=False,
                suggested_value=self._str_val(
                    suggested.get("qu_id", suggested.get("qu_id_purchase"))
                ),
                options=qu_options,
                select_mode=SelectMode.DROPDOWN,
            ),
            FormField(
                key="amount",
                field_type=FieldType.NUMBER,
                required=False,
                suggested_value=suggested.get("amount"),
            ),
        ]

    def build_update_product_details_fields(
        self,
        suggested: dict[str, Any],
        product: GrocyProduct,
    ) -> list[FormField]:
        """Build fields for the update-product-details form."""

        masterdata = self._masterdata
        locations = [
            loc
            for loc in masterdata["locations"]
            if product["should_not_be_frozen"] == 0 or loc["is_freezer"] == 0
        ]
        locations.sort(key=lambda loc: loc["name"])
        loc_options = [
            SelectOption(value=str(loc["id"]), label=loc["name"])
            for loc in locations
        ]

        qu_options = self._qu_options(include_blank=True)

        fields: list[FormField] = [
            FormField(
                key="default_consume_location_id",
                field_type=FieldType.SELECT,
                required=False,
                suggested_value=self._str_val(
                    suggested.get("default_consume_location_id")
                ),
                options=loc_options,
                select_mode=SelectMode.DROPDOWN,
            ),
            FormField(
                key="product_quantity",
                field_type=FieldType.NUMBER,
                required=False,
                suggested_value=suggested.get("product_quantity"),
                step=1,
            ),
            FormField(
                key="qu_id_product",
                field_type=FieldType.SELECT,
                required=False,
                suggested_value=self._str_val(
                    suggested.get("qu_id_product")
                ),
                description="What quantity unit does the product package have?",
                options=qu_options,
                select_mode=SelectMode.DROPDOWN,
            ),
            FormField(
                key="calories_per_100",
                field_type=FieldType.NUMBER,
                required=False,
                suggested_value=suggested.get("calories_per_100"),
                step=1,
            ),
        ]

        if not product.get("should_not_be_frozen", 0):
            fields.extend([
                FormField(
                    key="default_best_before_days_after_freezing",
                    field_type=FieldType.NUMBER,
                    required=False,
                    suggested_value=suggested.get(
                        "default_best_before_days_after_freezing"
                    ),
                    step=1,
                ),
                FormField(
                    key="default_best_before_days_after_thawing",
                    field_type=FieldType.NUMBER,
                    required=False,
                    suggested_value=suggested.get(
                        "default_best_before_days_after_thawing"
                    ),
                    step=1,
                ),
            ])

        return fields

    def build_choose_stock_entry_fields(
        self,
        product: GrocyProduct,
        stock_entries: list[dict],
    ) -> list[FormField]:
        """Build fields for choosing a stock entry to transfer."""

        masterdata = self._masterdata

        qu = None
        for qq in filter(
            lambda p: p["id"] == product["qu_id_stock"],
            masterdata["quantity_units"],
        ):
            qu = qq
            break

        options = [
            SelectOption(
                value=str(e["id"]),
                label=(
                    f"{product['name']} {e['amount']} "
                    f"{qu['name_plural'] if e['amount'] > 1 else qu['name']}, "
                    f"due: {e['best_before_date']}"
                ),
            )
            for e in stock_entries
        ]

        selected = (
            str(stock_entries[0]["id"])
            if stock_entries
            else None
        )

        return [
            FormField(
                key="stock_entry_id",
                field_type=FieldType.SELECT,
                required=True,
                suggested_value=selected,
                default=selected,
                options=options,
                select_mode=SelectMode.DROPDOWN,
            ),
        ]

    def build_transfer_input_fields(
        self,
        product: GrocyProduct,
        stock_entry: dict,
    ) -> list[FormField]:
        """Build fields for specifying transfer details."""

        masterdata = self._masterdata

        locations = [
            loc
            for loc in masterdata["locations"]
            if loc["id"] != stock_entry["location_id"]
            and (product["should_not_be_frozen"] == 0 or loc["is_freezer"] == 0)
        ]
        locations.sort(key=lambda loc: loc["name"])

        default_location = (
            str(locations[0]["id"]) if len(locations) > 0 else None
        )

        fields: list[FormField] = []

        if stock_entry["amount"] > 1:
            fields.append(
                FormField(
                    key="amount",
                    field_type=FieldType.NUMBER,
                    required=True,
                    suggested_value=stock_entry["amount"],
                    default=stock_entry["amount"],
                    number_mode=NumberMode.SLIDER,
                    step=product.get("quick_consume_amount", 1) or 1,
                    min_value=product.get("quick_consume_amount", 1) or 1,
                    max_value=stock_entry["amount"],
                ),
            )

        fields.append(
            FormField(
                key="location_to_id",
                field_type=FieldType.SELECT,
                required=True,
                suggested_value=default_location,
                default=default_location,
                options=[
                    SelectOption(value=str(loc["id"]), label=loc["name"])
                    for loc in locations
                ],
                select_mode=SelectMode.DROPDOWN,
            ),
        )

        return fields

    def build_scan_process_fields(
        self,
        _product: dict,
        price: Any,
        bestBeforeInDays: Any,
        shopping_location_id: Any,
        scan_options: dict[str, bool],
        current_recipe: dict | None,
        current_product_stock_info: dict | None,
        current_barcode: str | None,
    ) -> list[FormField]:
        """Build extra input fields for purchase mode.

        Returns an empty list when no extra input is required.
        """

        masterdata = self._masterdata
        fields: list[FormField] = []

        if (
            price is None
            and scan_options.get("input_price")
            and not current_recipe
        ):
            fields.append(
                FormField(
                    key="price",
                    field_type=FieldType.TEXT,
                    required=False,
                    suggested_value=price,
                ),
            )

        if scan_options.get("input_bestBeforeInDays"):
            fields.append(
                FormField(
                    key="bestBeforeInDays",
                    field_type=FieldType.TEXT,
                    required=False,
                    suggested_value=(
                        str(bestBeforeInDays) if bestBeforeInDays is not None else None
                    ),
                ),
            )

        if (
            shopping_location_id is None
            and scan_options.get("input_shoppingLocationId")
            and not current_recipe
        ):
            shopping_locations = sorted(
                masterdata.get("shopping_locations", []),
                key=lambda loc: loc["name"],
            )

            # Check default store on product barcode
            if current_product_stock_info and current_product_stock_info.get(
                "product_barcodes"
            ):
                for barcode in current_product_stock_info["product_barcodes"]:
                    if (
                        barcode.get("barcode", "").casefold()
                        == (current_barcode or "").casefold()
                    ):
                        shopping_location_id = barcode.get(
                            "shopping_location_id"
                        )
                        if shopping_location_id:
                            break

            if current_product_stock_info and not shopping_location_id:
                shopping_location_id = current_product_stock_info.get(
                    "default_shopping_location_id",
                    current_product_stock_info.get("product", {}).get(
                        "default_shopping_location_id"
                    ),
                )

            fields.append(
                FormField(
                    key="shopping_location_id",
                    field_type=FieldType.SELECT,
                    required=False,
                    suggested_value=(
                        str(shopping_location_id)
                        if shopping_location_id
                        else None
                    ),
                    options=[
                        SelectOption(value=str(loc["id"]), label=loc["name"])
                        for loc in shopping_locations
                    ],
                    select_mode=SelectMode.DROPDOWN,
                ),
            )

        return fields

    # ── Helper methods ───────────────────────────────────────────────

    def _location_options(self) -> list[SelectOption]:
        return [
            SelectOption(value=str(loc["id"]), label=loc["name"])
            for loc in self._masterdata["locations"]
        ]

    def _qu_options(self, include_blank: bool = False) -> list[SelectOption]:
        options = [
            SelectOption(value=str(qu["id"]), label=qu["name"])
            for qu in self._masterdata["quantity_units"]
        ]
        if include_blank:
            options.insert(0, SelectOption(value="", label=""))
        return options

    @staticmethod
    def _str_val(val: Any) -> str | None:
        """Convert a value to ``str`` for select field suggested values."""
        return str(val) if val is not None else None
