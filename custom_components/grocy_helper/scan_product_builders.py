"""Product data building and transformation utilities.

This module contains functions for building product dictionaries from user
input, parsing external data sources (OpenFoodFacts), validating product
data, and transforming data for API requests.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from .coordinator import GrocyHelperCoordinator
from .const import NUMERIC_FIELDS
from .grocytypes import GrocyMasterData

_LOGGER = logging.getLogger(__name__)


class ProductDataBuilder:
    """Builds and transforms product data structures."""

    def __init__(self, coordinator: GrocyHelperCoordinator):
        """Initialize with Grocy masterdata.

        Parameters
        ----------
        coordinator:
            GrocyHelperCoordinator instance containing masterdata
        """
        self._coordinator = coordinator

    @property
    def _masterdata(self) -> GrocyMasterData:
        return self._coordinator.data

    @staticmethod
    def merge_product_values(
        user_input: dict[str, Any],
        product: dict[str, Any],
        keys: list[str],
    ) -> dict[str, Any]:
        """Merge user input with product state for form suggested values.

        User input takes precedence. Non-numeric values are converted
        to ``str`` (required for select-field suggested values).

        Parameters
        ----------
        user_input:
            User input dictionary from form submission
        product:
            Existing product data
        keys:
            List of keys to merge

        Returns
        -------
            Dictionary with merged values
        """
        # TODO: Obsolete, by transform_input method
        suggested: dict[str, Any] = {}
        for k in keys:
            val = user_input.get(k, product.get(k))
            if k not in NUMERIC_FIELDS:
                val = str(val) if val is not None else None
            suggested[k] = val
        return suggested

    @staticmethod
    def build_product_from_input(
        user_input: dict[str, Any], base_product: dict[str, Any]
    ) -> dict[str, Any]:
        """Build a new product dict from user input.

        Parameters
        ----------
        user_input:
            User input from the create product form
        base_product:
            Base product dictionary to start from

        Returns
        -------
            Complete product dictionary ready for API submission
        """
        product = base_product.copy()
        product["name"] = user_input["name"]
        product["location_id"] = user_input["location_id"]
        product["should_not_be_frozen"] = (
            1 if user_input.get("should_not_be_frozen", False) else 0
        )

        # Optional fields
        if val := user_input.get("default_consume_location_id"):
            product["default_consume_location_id"] = int(val)
        if val := user_input.get("default_best_before_days"):
            product["default_best_before_days"] = int(val)
        if val := user_input.get("default_best_before_days_after_open"):
            product["default_best_before_days_after_open"] = int(val)
        if val := user_input.get("default_best_before_days_after_freezing"):
            product["default_best_before_days_after_freezing"] = int(val)
        if val := user_input.get("default_best_before_days_after_thawing"):
            product["default_best_before_days_after_thawing"] = int(val)

        # Quantity units
        product["qu_id_stock"] = user_input.get("qu_id_stock", user_input.get("qu_id"))
        product["qu_id_purchase"] = user_input.get(
            "qu_id_purchase", user_input.get("qu_id")
        )
        product["qu_id_consume"] = user_input.get(
            "qu_id_consume", user_input.get("qu_id")
        )
        product["qu_id_price"] = user_input.get("qu_id_price", user_input.get("qu_id"))

        product["description"] = user_input.get(
            "description", product.get("description")
        )
        product["parent_product_id"] = user_input.get(
            "parent_product_id", product.get("parent_product_id")
        )

        return product

    def validate_product_location(self, product: dict[str, Any]) -> dict[str, str]:
        """Validate product location constraints.

        Parameters
        ----------
        product:
            Product dictionary with location_id and should_not_be_frozen

        Returns
        -------
            Dictionary of errors (empty if valid)
        """
        errors: dict[str, str] = {}

        loc = next(
            (
                loc
                for loc in self._masterdata["locations"]
                if str(loc["id"]) == str(product["location_id"])
            ),
            None,
        )

        if not loc:
            errors["location_id"] = "invalid_location"
        elif product.get("should_not_be_frozen") == 1 and loc.get("is_freezer") == 1:
            errors["location_id"] = "location_is_freezer"

        return errors

    def build_parent_product_suggested_values(
        self,
        new_product: dict,
        user_input: dict,
        creating_parent: bool,
        current_product: dict | None = None,
    ) -> dict[str, Any]:
        """Build suggested values for parent product form.

        Parameters
        ----------
        new_product:
            The new product being created
        user_input:
            User input from previous form
        creating_parent:
            Whether creating a parent for existing product
        current_product:
            Current product (when creating parent for existing)

        Returns
        -------
            Dictionary of suggested values for form fields
        """
        masterdata = self._masterdata

        # Keys for parent product form
        parent_keys = ["name", "qu_id_stock", "qu_id_price"]
        if not creating_parent:
            parent_keys.extend(
                [
                    "location_id",
                    "should_not_be_frozen",
                    "default_best_before_days",
                    "default_best_before_days_after_open",
                    "qu_id_purchase",
                    "qu_id_consume",
                ]
            )

        # Merge values - copy from child product when creating parent
        suggested: dict[str, Any] = {}
        for k in parent_keys:
            val = user_input.get(k, new_product.get(k))
            if not val and creating_parent and current_product:
                if k not in ("id", "name", "description"):
                    _LOGGER.warning(
                        "COPY prop to parent: %s=%s", k, current_product.get(k)
                    )
                    val = current_product.get(k)
            if k not in NUMERIC_FIELDS:
                val = str(val) if val is not None else None
            suggested[k] = val

        # Adjust QU for parent - prefer price QU over piece/pack
        piece_qu = masterdata["known_qu"].get("Piece")
        pack_qu = masterdata["known_qu"].get("Pack")
        piece_id = (
            piece_qu.get("id")
            if isinstance(piece_qu, dict)
            else getattr(piece_qu, "id", None)
        )
        pack_id = (
            pack_qu.get("id")
            if isinstance(pack_qu, dict)
            else getattr(pack_qu, "id", None)
        )
        if (int(suggested.get("qu_id_stock") or -99) in [piece_id, pack_id]) and (
            int(suggested.get("qu_id_price") or -99) not in [piece_id, pack_id]
        ):
            _LOGGER.warning(
                "Copying qu_id_price into qu_id_stock: %s. Known: %s",
                suggested,
                masterdata["known_qu"],
            )
            suggested["qu_id_stock"] = suggested["qu_id_price"]

        return suggested

    @staticmethod
    def build_parent_product_from_input(
        user_input: dict,
        new_product: dict,
        creating_parent: bool,
        current_product: dict | None = None,
    ) -> dict:
        """Build parent product data from user input.

        Parameters
        ----------
        user_input:
            User input from parent product form
        new_product:
            Base product data
        creating_parent:
            Whether creating a parent for existing product
        current_product:
            Current product (for default values)

        Returns
        -------
            Parent product dictionary ready for API submission
        """
        new_product["name"] = user_input["name"]
        # TODO: Location not super relevant for Parent products, perhaps set value as per child. But don't render field for it?
        new_product["location_id"] = user_input.get(
            "location_id",
            current_product["location_id"] if current_product else None,
        )
        new_product["should_not_be_frozen"] = (
            1
            if user_input.get(
                "should_not_be_frozen",
                (current_product or {}).get("should_not_be_frozen", False),
            )
            else 0
        )

        # TODO: Since not handling any physical products with the Parent product, perhaps the due date-fields are irrelevant? (Set value as per child). Don't render field for it, to simplify?
        if val := user_input.get("default_best_before_days"):
            new_product["default_best_before_days"] = int(val)
        if val := user_input.get("default_best_before_days_after_open"):
            new_product["default_best_before_days_after_open"] = int(val)

        new_product["qu_id_stock"] = user_input.get(
            "qu_id_stock", user_input.get("qu_id")
        )
        new_product["qu_id_purchase"] = user_input.get(
            "qu_id_purchase",
            new_product.get("qu_id_stock"),
            # ...this unit is not really for parents, but will set as field is required
        )
        new_product["qu_id_consume"] = user_input.get(
            "qu_id_consume",
            new_product.get("qu_id_stock"),
            # ...this unit is not really for parents, but will set as field is required
        )
        new_product["qu_id_price"] = user_input.get(
            "qu_id_price",
            user_input.get("qu_id"),
            # TODO: clear this value if is Piece/Pack, since it is best with a unit for Liquid / Weight
        )
        new_product["row_created_timestamp"] = dt.datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        if creating_parent:
            new_product["description"] = user_input.get(
                "description", new_product.get("description")
            )
            new_product["no_own_stock"] = 1
            new_product["hide_on_stock_overview"] = 1
            new_product["disable_open"] = 1
            new_product["cumulate_min_stock_amount_of_sub_products"] = 1
            new_product["parent_product_id"] = None
        else:
            new_product["description"] = user_input.get(
                "description", new_product.get("description")
            )
            new_product["parent_product_id"] = user_input.get(
                "parent_product_id", new_product.get("parent_product_id")
            )

        _LOGGER.info("user_input: %s", user_input)
        _LOGGER.info("new_product: %s", new_product)

        return new_product

    @staticmethod
    def initialize_product_details_input(
        user_input: dict, current_product: dict | None
    ) -> dict:
        """Initialize input with defaults from current product.

        Parameters
        ----------
        user_input:
            User input dictionary
        current_product:
            Current product to get defaults from

        Returns
        -------
            User input dictionary with defaults filled in
        """
        # TODO: Remove method, or call `self.transform_input` with passed keys
        for key in (
            "should_not_be_frozen",
            "default_consume_location_id",
            "default_best_before_days_after_freezing",
            "default_best_before_days_after_thawing",
        ):
            val = user_input.get(key, (current_product or {}).get(key))
            if key not in NUMERIC_FIELDS:
                val = str(val) if val is not None else None
            user_input[key] = val
        return user_input

    def parse_openfoodfacts_data(
        self,
        user_input: dict,
        current_product_openfoodfacts: dict | None,
    ) -> tuple[float | None, int | None, bool, bool, float | None]:
        """Parse OpenFoodFacts data for quantity, unit, and calories.

        Parameters
        ----------
        user_input:
            User input dictionary (will be modified with calories)
        current_product_openfoodfacts:
            OpenFoodFacts product data

        Returns
        -------
            Tuple of (product_quantity, product_quantity_unit,
                     is_liquid, is_weight, calories)
        """
        masterdata = self._masterdata
        product_quantity = None
        product_quantity_unit: int | None = None
        product_quantity_unit_as_liquid = False
        product_quantity_unit_as_weight = False

        if current_product_openfoodfacts is not None:
            product_quantity = user_input.get(
                "product_quantity",
                current_product_openfoodfacts.get("product_quantity"),
            )

            #         # TODO: compare qu, against the defaulted "qu_id_purchase" or "qui_id_stock"
            #         # TODO: make conversion, if necessary...
            unit = current_product_openfoodfacts.get("product_quantity_unit")
            if unit:
                for qq in filter(
                    lambda qu: qu.get("name") == unit,
                    masterdata["quantity_units"],
                ):
                    product_quantity_unit = qq["id"]
                    _LOGGER.warning("Unit: %s, QQ: %s", unit, qq)
                    product_quantity_unit_as_liquid = qq["name"] in [
                        "ml",
                        "cl",
                        "dl",
                        "l",
                        "L",
                    ]
                    product_quantity_unit_as_weight = qq["name"] in [
                        "g",
                        "hg",
                        "kg",
                    ]

        # TODO: fill in info from ICA

        kcal = user_input.get("calories_per_100") or (
            current_product_openfoodfacts or {}
        ).get("nutriments", {}).get("energy_kcal_100g")
        user_input["calories_per_100"] = kcal
        if kcal:
            kcal = float(kcal)

        return (
            product_quantity,
            product_quantity_unit,
            product_quantity_unit_as_liquid,
            product_quantity_unit_as_weight,
            kcal,
        )

    @staticmethod
    def build_scan_request(
        code: str,
        in_purchase_mode: bool,
        price: str | None,
        best_before_in_days: int | None,
        shopping_location_id: str | None,
    ) -> dict[str, Any]:
        """Build request dict for scan action.

        Parameters
        ----------
        code:
            Barcode string
        in_purchase_mode:
            Whether in purchase mode
        price:
            Price value (if any)
        best_before_in_days:
            Best before days (if any)
        shopping_location_id:
            Shopping location ID (if any)

        Returns
        -------
            Request dictionary for API call
        """
        request: dict[str, Any] = {"barcode": str(code)}

        if in_purchase_mode:
            if price is not None and len(str(price)) > 0:
                request["price"] = float(price)
            if best_before_in_days is not None and len(str(best_before_in_days)) > 0:
                request["bestBeforeInDays"] = int(best_before_in_days)
            if shopping_location_id is not None and int(shopping_location_id) > 0:
                request["shopping_location_id"] = int(shopping_location_id)

        return request
