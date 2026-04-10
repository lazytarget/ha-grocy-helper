"""Form field builders for barcode scanning workflow.

This module contains all the form field construction logic for the
ScanSession workflow. It's separated from ScanSession to reduce file
size and improve maintainability.
"""

from __future__ import annotations

from typing import Any

from .coordinator import GrocyHelperCoordinator
from .const import CONF_DEFAULT_LOCATION_FREEZER, CONF_DEFAULT_LOCATION_FRIDGE, CONF_DEFAULT_LOCATION_RECIPE_RESULT, CONF_DEFAULT_PRODUCT_GROUP_FOR_RECIPE_RESULT, CONF_ENABLE_AUTO_PRINT, CONF_ENABLE_PRICES, CONF_ENABLE_PRINTING, CONF_ENABLE_SHOPPING_LOCATIONS, DEV_CONST, SCAN_MODE
from .grocytypes import GrocyMasterData, GrocyProduct
from .scan_types import FieldType, FormField, NumberMode, SelectMode, SelectOption


class ScanFormBuilder:
    """Build form fields for the scanning workflow."""

    def __init__(self, coordinator: GrocyHelperCoordinator):
        """Initialize the form builder.

        Parameters
        ----------
        coordinator:
            A ``GrocyHelperCoordinator`` instance that handles persistence
            and masterdata cache updates.
        """
        self._coordinator = coordinator

    @property
    def _masterdata(self) -> GrocyMasterData:
        return self._coordinator.data

    def build_scan_start_fields(self, scan_mode: SCAN_MODE | None) -> list[FormField]:
        """Build fields for the scan-start form."""

        bbuddy_mode_str = scan_mode.name if scan_mode is not None else "Unknown"

        return [
            FormField(
                key="mode",
                field_type=FieldType.SELECT,
                required=False,
                suggested_value=DEV_CONST.get(
                    "default_scan_mode", SCAN_MODE.SCAN_BBUDDY
                ),
                select_mode=SelectMode.LIST,
                # translation_key="scan_mode",
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
                    SelectOption(
                        value=SCAN_MODE.TRANSFER, label="Transfer"
                    ),  # TODO: only add option if has more than 1 locations setup
                    SelectOption(value=SCAN_MODE.OPEN, label="Open"),
                    SelectOption(value=SCAN_MODE.INVENTORY, label="Inventory"),
                    # selector.SelectOptionDict(    # merge with Inventory-action
                    #     value="lookup-barcode",
                    #     label="Lookup"
                    # ),
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
                suggested_value=DEV_CONST.get("default_barcode", ""),
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

        child_products = [p for p in masterdata["products"] if p["parent_product_id"]]
        parent_product_ids = [p["parent_product_id"] for p in child_products]
        # TODO: NOTE CURRENT FLAW/FEATURE: If a product is not already a Parent, then cannot be chosen to be come a parent (actually logical to prevent children from becoming ones). Not an issue if ALL parents are provisioned via this flow...
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
                    suggested_values.get("product_id", suggested_products[0]["id"])
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
                    # TODO: ...or if product_alias matches another product WHICH has a parent, then suggest that parent
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
        product_group_options = self._product_group_options()
        qu_options = self._qu_options()

        fields: list[FormField] = [
            FormField(
                key="name",
                field_type=FieldType.TEXT,
                required=True,
                suggested_value=suggested.get("name"),
                # TODO: render as listbox with suggested values, but allow for custom text?
                # Example: Mango / Mango Fryst 250g ICA / Fryst mango
            ),
        ]
        fields.extend(
            [
                FormField(
                    key="product_group_id",
                    field_type=FieldType.SELECT,
                    required=False,
                    suggested_value=self._str_val(suggested.get("product_group_id")),
                    options=product_group_options,
                    select_mode=SelectMode.DROPDOWN,
                )
            ]
        )

        if not creating_parent:
            fields.extend(
                [
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
                ]
            )

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
            fields.extend(
                [
                    FormField(
                        key="qu_id_purchase",
                        field_type=FieldType.SELECT,
                        required=True,
                        suggested_value=self._str_val(
                            suggested.get("qu_id_purchase", suggested.get("qu_id"))
                        ),
                        options=qu_options,
                        select_mode=SelectMode.DROPDOWN,
                    ),
                    FormField(
                        key="qu_id_consume",
                        field_type=FieldType.SELECT,
                        required=True,
                        suggested_value=self._str_val(
                            suggested.get("qu_id_consume", suggested.get("qu_id"))
                        ),
                        options=qu_options,
                        select_mode=SelectMode.DROPDOWN,
                    ),
                ]
            )

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

    def build_create_barcode_fields(self, suggested: dict[str, Any]) -> list[FormField]:
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
            SelectOption(value=str(loc["id"]), label=loc["name"]) for loc in locations
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
                suggested_value=self._str_val(suggested.get("qu_id_product")),
                description="What quantity unit does the product package have?",
                options=qu_options,
                select_mode=SelectMode.DROPDOWN,
            ),
            # TODO: 'calories_per_100' could probably be hidden for Products produced by Recipes? As those should instead Summarize the Ingredients
            FormField(
                key="calories_per_100",
                field_type=FieldType.NUMBER,
                required=False,
                suggested_value=suggested.get("calories_per_100"),
                step=1,
            ),
        ]

        if not product.get("should_not_be_frozen", 0):
            fields.extend(
                [
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
                ]
            )

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
                # TODO: append current location name
            )
            for e in stock_entries
        ]

        selected = str(stock_entries[0]["id"]) if stock_entries else None

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

        default_location = str(locations[0]["id"]) if len(locations) > 0 else None

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
                    step=product.get("quick_consume_amount", 1)
                    or 1,  # follow consume amount for how many quantities can be transferred
                    min_value=product.get("quick_consume_amount", 1)
                    or 1,  # transfer at least 1
                    max_value=stock_entry["amount"],  # maximum allowed to move all
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

    def build_create_recipe_fields(
        self,
        suggestions: dict[str, Any] | None = None,
        printing_enabled: bool = False,
    ) -> list[FormField]:
        """Build fields for the create-recipe form."""
        suggestions = suggestions or {}
        fields: list[FormField] = [
            FormField(
                key="name",
                field_type=FieldType.TEXT,
                required=True,
                suggested_value=suggestions.get("name"),
            )
        ]
        if printing_enabled:
            fields.append(FormField(
                key="print",
                field_type=FieldType.BOOLEAN,
                required=False,
                default=False,
                suggested_value=suggestions.get("print", False),
            ))
        return fields

    def build_scan_process_fields(
        self,
        _product: dict,
        price: Any,
        best_before_in_days: Any,
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

        if price is None and scan_options.get(CONF_ENABLE_PRICES, True) and not current_recipe:
            fields.append(
                FormField(
                    key="price",
                    field_type=FieldType.TEXT,
                    required=False,
                    suggested_value=price,
                ),
            )

        bb_str = (
            str(best_before_in_days)
            if best_before_in_days is not None
            else None
        )
        # default is only set when the value is trustworthy:
        # >0 = configured days, -1 = never expires.
        # 0 = "expires today" (Grocy default) — suspicious, needs review.
        bb_default = (
            bb_str
            if best_before_in_days is not None and best_before_in_days != 0
            else None
        )
        fields.append(
            FormField(
                key="best_before_in_days",
                field_type=FieldType.TEXT,
                required=False,
                suggested_value=bb_str,
                default=bb_default,
            ),
        )

        if (
            shopping_location_id is None
            and scan_options.get(CONF_ENABLE_SHOPPING_LOCATIONS, True)
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
                        shopping_location_id = barcode.get("shopping_location_id")
                        if shopping_location_id:
                            break

            if current_product_stock_info and not shopping_location_id:
                shopping_location_id = current_product_stock_info.get(
                    "default_shopping_location_id",
                    current_product_stock_info.get("product", {}).get(
                        "default_shopping_location_id"
                    ),
                )

            sl_value = (
                str(shopping_location_id) if shopping_location_id else None
            )
            fields.append(
                FormField(
                    key="shopping_location_id",
                    field_type=FieldType.SELECT,
                    required=False,
                    suggested_value=sl_value,
                    default=sl_value,
                    options=[
                        SelectOption(value=str(loc["id"]), label=loc["name"])
                        for loc in shopping_locations
                        # TODO: Able to create new store? via ´custom_value=True,´
                    ],
                    select_mode=SelectMode.DROPDOWN,
                ),
            )
        return fields

    def build_produce_fields(
        self,
        product: dict,
        location_id: int | None,
        recipe_cost: float | None = None,
        base_servings: int = 1,
        scan_options: dict[str, Any] | None = None,
    ) -> list[FormField]:
        """Build input fields for the produce form (recipe → stock entries).

        Parameters
        ----------
        product:
            The producing product.
        location_id:
            Suggested default location for the produced items.
        recipe_cost:
            Total recipe cost from Grocy fulfillment endpoint.
        base_servings:
            Number of servings from the recipe definition.
        """

        loc_options = [
            SelectOption(value=str(loc["id"]), label=loc["name"])
            for loc in self._masterdata.get("locations", [])
            if loc.get("active") == 1
            and (product.get("should_not_be_frozen", 0) == 0 or loc["is_freezer"] == 0)
        ]

        fields: list[FormField] = [
            FormField(
                key="produce_consume_ingredients",
                field_type=FieldType.BOOLEAN,
                required=True,
                suggested_value=True,
            ),
            FormField(
                key="produce_servings",
                field_type=FieldType.NUMBER,
                required=True,
                suggested_value=base_servings,
                min_value=1,
                max_value=50,
                step=1,
                number_mode=NumberMode.BOX,
            ),
            FormField(
                key="produce_amount",
                field_type=FieldType.NUMBER,
                required=True,
                suggested_value=max(1, base_servings - 1),
                min_value=0,
                max_value=50,
                step=1,
                number_mode=NumberMode.BOX,
            ),
            FormField(
                key="produce_location_id",
                field_type=FieldType.SELECT,
                required=True,
                suggested_value=self._str_val(location_id),
                options=loc_options,
                select_mode=SelectMode.DROPDOWN,
            ),
        ]

        prices_enabled = (scan_options or {}).get(CONF_ENABLE_PRICES, True)
        if recipe_cost is not None and prices_enabled:
            fields.append(
                FormField(
                    key="produce_price",
                    field_type=FieldType.TEXT,
                    required=False,
                    suggested_value=str(round(recipe_cost, 2)) if recipe_cost > 0 else None,
                ),
            )

        return fields

    def build_produce_confirm_fields(
        self,
        printing_enabled: bool = False,
        auto_print: bool = False,
    ) -> list[FormField]:
        """Build fields for the produce confirmation form.

        Only contains the print toggle; the summary is rendered via
        description_placeholders.
        """
        fields: list[FormField] = []

        if printing_enabled:
            fields.append(
                FormField(
                    key="produce_print",
                    field_type=FieldType.BOOLEAN,
                    required=False,
                    default=auto_print,
                    suggested_value=auto_print,
                ),
            )

        return fields

    def build_scan_options_fields(
        self,
        suggested: dict[str, Any],
    ) -> list[FormField]:
        """Build fields for the scan options form."""
        loc_options = self._location_options(include_inactive=True)
        product_group_options = self._product_group_options()

        fields: list[FormField] = []
        fields.extend(
            [
                FormField(
                    key=CONF_DEFAULT_LOCATION_FRIDGE,
                    field_type=FieldType.SELECT,
                    required=False,
                    default=None, # Allow for clearing the value
                    suggested_value=self._str_val(suggested.get(CONF_DEFAULT_LOCATION_FRIDGE)),
                    options=loc_options,
                    select_mode=SelectMode.DROPDOWN,
                    multiple=False,
                    custom_value=False,
                ),
                FormField(
                    key=CONF_DEFAULT_LOCATION_FREEZER,
                    field_type=FieldType.SELECT,
                    required=False,
                    default=None, # Allow for clearing the value
                    suggested_value=self._str_val(suggested.get(CONF_DEFAULT_LOCATION_FREEZER)),
                    options=loc_options,
                    select_mode=SelectMode.DROPDOWN,
                    multiple=False,
                    custom_value=False,
                ),
                FormField(
                    key=CONF_DEFAULT_LOCATION_RECIPE_RESULT,
                    field_type=FieldType.SELECT,
                    required=False,
                    default=None, # Allow for clearing the value
                    suggested_value=self._str_val(suggested.get(CONF_DEFAULT_LOCATION_RECIPE_RESULT)),
                    options=loc_options,
                    select_mode=SelectMode.DROPDOWN,
                    multiple=False,
                    custom_value=False,
                ),
                FormField(
                    key=CONF_DEFAULT_PRODUCT_GROUP_FOR_RECIPE_RESULT,
                    field_type=FieldType.SELECT,
                    required=False,
                    default=None, # Allow for clearing the value
                    suggested_value=self._str_val(suggested.get(CONF_DEFAULT_PRODUCT_GROUP_FOR_RECIPE_RESULT)),
                    options=product_group_options,
                    select_mode=SelectMode.DROPDOWN,
                    multiple=False,
                    custom_value=False,
                ),
                FormField(
                    key=CONF_ENABLE_PRINTING,
                    field_type=FieldType.BOOLEAN,
                    required=False,
                    default=None, # Allow for clearing the value
                    suggested_value=suggested.get(CONF_ENABLE_PRINTING),
                ),
                FormField(
                    key=CONF_ENABLE_AUTO_PRINT,
                    field_type=FieldType.BOOLEAN,
                    required=False,
                    default=None, # Allow for clearing the value
                    suggested_value=suggested.get(CONF_ENABLE_AUTO_PRINT),
                ),
                FormField(
                    key=CONF_ENABLE_PRICES,
                    field_type=FieldType.BOOLEAN,
                    required=False,
                    default=None, # Allow for clearing the value
                    suggested_value=suggested.get(CONF_ENABLE_PRICES, True),
                ),
                FormField(
                    key=CONF_ENABLE_SHOPPING_LOCATIONS,
                    field_type=FieldType.BOOLEAN,
                    required=False,
                    default=None, # Allow for clearing the value
                    suggested_value=suggested.get(CONF_ENABLE_SHOPPING_LOCATIONS, True),
                ),
            ]
        )
        return fields

    # ── Helper methods ───────────────────────────────────────────────

    def _location_options(self, include_inactive: bool = False) -> list[SelectOption]:
        return [
            SelectOption(value=str(loc["id"]), label=loc["name"])
            for loc in self._masterdata.get("locations", [])
            if include_inactive or loc.get("active") == 1
        ]

    def _product_group_options(self) -> list[SelectOption]:
        return [
            SelectOption(value=str(pg["id"]), label=pg["name"])
            for pg in self._masterdata.get("product_groups", [])
            if pg.get("active") == 1
        ]

    def _qu_options(self, include_blank: bool = False) -> list[SelectOption]:
        options = [
            SelectOption(value=str(qu["id"]), label=qu["name"])
            for qu in self._masterdata.get("quantity_units", [])
            if qu["active"] == 1
        ]
        if include_blank:
            options.insert(0, SelectOption(value="", label=""))
        return options

    @staticmethod
    def _str_val(val: Any) -> str | None:
        """Convert a value to ``str`` for select field suggested values."""
        return str(val) if val is not None else None
