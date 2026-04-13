"""Shared test fixtures and fakes for grocy_helper tests.

Provides lightweight fakes for GrocyAPI, BarcodeBuddyAPI, and
GrocyHelperCoordinator so that ScanSession (and the upcoming ScanQueue)
can be tested without Home Assistant or real HTTP calls.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from custom_components.grocy_helper.const import SCAN_MODE
from custom_components.grocy_helper.grocytypes import (
    BarcodeLookup,
    ExtendedGrocyProductStockInfo,
    GrocyLocation,
    GrocyMasterData,
    GrocyProduct,
    GrocyProductBarcode,
    GrocyQuantityUnit,
    GrocyRecipe,
)


# ── Sample data factories ───────────────────────────────────────────


def make_quantity_unit(
    id: int = 1,
    name: str = "Piece",
    **overrides: Any,
) -> GrocyQuantityUnit:
    return {
        "id": id,
        "name": name,
        "description": None,
        "row_created_timestamp": "2025-01-01 00:00:00",
        "name_plural": f"{name}s",
        "plural_forms": None,
        "active": 1,
        "userfields": None,
        **overrides,
    }


def make_location(
    id: int = 1,
    name: str = "Fridge",
    is_freezer: int = 0,
    **overrides: Any,
) -> GrocyLocation:
    return {
        "id": id,
        "name": name,
        "description": None,
        "row_created_timestamp": "2025-01-01 00:00:00",
        "is_freezer": is_freezer,
        "active": 1,
        "userfields": None,
        **overrides,
    }


def make_product(
    id: int = 1,
    name: str = "Test Product",
    location_id: int = 1,
    qu_id: int = 1,
    **overrides: Any,
) -> GrocyProduct:
    defaults = {
        "id": id,
        "name": name,
        "location_id": location_id,
        "qu_id_stock": qu_id,
        "qu_id_purchase": qu_id,
        "qu_id_consume": qu_id,
        "qu_id_price": qu_id,
        "row_created_timestamp": "2025-01-01 00:00:00",
        "description": None,
        "product_group_id": None,
        "active": 1,
        "shopping_location_id": None,
        "min_stock_amount": 0,
        "default_best_before_days": 5,
        "default_best_before_days_after_open": 0,
        "default_best_before_days_after_freezing": 0,
        "default_best_before_days_after_thawing": 0,
        "picture_file_name": None,
        "enable_tare_weight_handling": 0,
        "tare_weight": 0,
        "not_check_stock_fulfillment_for_recipes": 0,
        "parent_product_id": None,
        "calories": None,
        "cumulate_min_stock_amount_of_sub_products": 0,
        "due_type": 1,
        "quick_consume_amount": 1,
        "hide_on_stock_overview": 0,
        "default_stock_label_type": 0,
        "should_not_be_frozen": 0,
        "treat_opened_as_out_of_stock": 0,
        "no_own_stock": 0,
        "default_consume_location_id": None,
        "move_on_open": 0,
        "auto_reprint_stock_label": 0,
        "quick_open_amount": 0,
        "disable_open": 0,
        "default_purchase_price_type": 0,
        "userfields": None,
    }
    defaults.update(overrides)
    return defaults


def make_product_barcode(
    id: int = 1,
    product_id: int = 1,
    barcode: str = "1234567890123",
    **overrides: Any,
) -> GrocyProductBarcode:
    return {
        "id": id,
        "product_id": product_id,
        "barcode": barcode,
        "note": "",
        "qu_id": None,
        "amount": None,
        "shopping_location_id": None,
        "last_price": None,
        "row_created_timestamp": "2025-01-01 00:00:00",
        "userfields": None,
        **overrides,
    }


def make_stock_info(
    product: GrocyProduct | None = None,
    barcodes: list[GrocyProductBarcode] | None = None,
    **overrides: Any,
) -> ExtendedGrocyProductStockInfo:
    if product is None:
        product = make_product()
    if barcodes is None:
        barcodes = [make_product_barcode(product_id=product["id"])]
    return {
        "stock_amount": 0,
        "stock_value": 0,
        "last_purchased": "",
        "last_used": "",
        "product": product,
        "product_barcodes": barcodes,
        "default_shopping_location_id": None,
        **overrides,
    }


def make_recipe(
    id: int = 1,
    name: str = "Test Recipe",
    **overrides: Any,
) -> GrocyRecipe:
    return {
        "id": id,
        "name": name,
        "description": None,
        "row_created_timestamp": "2025-01-01 00:00:00",
        "picture_file_name": None,
        "base_servings": 4,
        "desired_servings": 4,
        "not_check_shoppinglist": 0,
        "type": "normal",
        "product_id": None,
        "userfields": None,
        **overrides,
    }


WELL_KNOWN_QU = {
    "Piece": make_quantity_unit(id=1, name="Piece"),
    "Pack": make_quantity_unit(id=2, name="Pack"),
    "g": make_quantity_unit(id=3, name="g"),
    "kg": make_quantity_unit(id=4, name="kg"),
    "ml": make_quantity_unit(id=5, name="ml"),
    "L": make_quantity_unit(id=6, name="L"),
}


def make_master_data(
    products: list[GrocyProduct] | None = None,
    recipes: list[GrocyRecipe] | None = None,
    **overrides: Any,
) -> GrocyMasterData:
    return {
        "locations": [
            make_location(id=1, name="Fridge"),
            make_location(id=2, name="Freezer", is_freezer=1),
        ],
        "shopping_locations": [],
        "quantity_units": list(WELL_KNOWN_QU.values()),
        "products": products or [],
        "product_groups": [],
        "recipes": recipes or [],
        "product_presets": None,
        "known_qu": WELL_KNOWN_QU,
        **overrides,
    }


# ── Fake GrocyAPI ───────────────────────────────────────────────────


class FakeGrocyAPI:
    """In-memory fake of GrocyAPI for testing.

    All methods are async. Configure responses by setting attributes
    or overriding individual methods.
    """

    def __init__(self) -> None:
        # Lookup tables keyed by barcode / product_id
        self._stock_by_barcode: dict[str, ExtendedGrocyProductStockInfo] = {}
        self._stock_by_id: dict[int, ExtendedGrocyProductStockInfo] = {}
        self._added_stock: list[tuple[int, dict]] = []
        self._next_product_id: int = 100
        self._user_settings: dict[str, Any] = {}

    def register_product(
        self,
        product: GrocyProduct,
        barcodes: list[str] | None = None,
        stock_amount: int = 0,
    ) -> None:
        """Register a product so barcode/id lookups find it."""
        barcode_objects = [
            make_product_barcode(id=i + 1, product_id=product["id"], barcode=bc)
            for i, bc in enumerate(barcodes or [])
        ]
        info = make_stock_info(
            product=product,
            barcodes=barcode_objects,
            stock_amount=stock_amount,
        )
        self._stock_by_id[product["id"]] = info
        for bc in barcodes or []:
            self._stock_by_barcode[bc] = info

    # ── API methods (async, matching GrocyAPI interface) ────────────

    async def get_locations(self):
        return [
            make_location(id=1, name="Fridge"),
            make_location(id=2, name="Freezer", is_freezer=1),
        ]

    async def get_shopping_locations(self):
        return []

    async def get_quantityunits(self):
        return list(WELL_KNOWN_QU.values())

    async def get_products(self):
        return [info["product"] for info in self._stock_by_id.values()]

    async def get_product_groups(self):
        return []

    async def get_recipes(self):
        return []

    async def get_user_settings(self):
        return self._user_settings

    async def get_stock_product_by_barcode(self, barcode: str):
        return self._stock_by_barcode.get(barcode)

    async def get_stock_product_by_id(self, product_id: int):
        return self._stock_by_id.get(product_id)

    async def get_product_by_id(self, product_id: int):
        info = self._stock_by_id.get(product_id)
        return info["product"] if info else None

    async def get_product_barcode_by_id(self, product_id: int):
        info = self._stock_by_id.get(product_id)
        return info["product_barcodes"] if info else []

    async def get_stock_entries_by_product_id(self, product_id: int):
        return []

    async def add_stock_product(self, product_id: int, data: dict):
        self._added_stock.append((product_id, data))
        return [{"id": len(self._added_stock)}]

    async def consume_stock_product(self, product_id: int, amount: float, **kwargs):
        return {}

    async def add_product(self, data: dict) -> dict:
        data["id"] = self._next_product_id
        self._next_product_id += 1
        return data

    async def update_product(self, product_id: int, data: dict):
        return {}

    async def add_product_barcode(self, data: dict):
        return {"created_object_id": 1}

    async def add_product_quantity_unit_conversion(self, data: dict):
        return {"created_object_id": 1}

    async def resolve_quantity_unit_conversions_for_product_id(self, product_id: int):
        return []

    async def transfer_stock_entry(self, product_id: int, data: dict):
        return {}

    async def create_recipe(self, data: dict):
        return {"created_object_id": str(self._next_product_id)}

    async def update_recipe(self, recipe_id: int, data: dict):
        return {}

    async def get_recipe_fulfillment(self, recipe_id: int):
        return {"costs": 0, "calories": 0}

    async def get_recipes_pos_resolved(self, recipe_id: int):
        return []

    async def consume_recipe(self, recipe_id: int):
        return None

    async def print_label_for_product(self, product_id: int):
        return {}

    async def print_label_for_stock_entry(self, stock_entry_id: int):
        return {}

    async def print_label_for_recipe(self, recipe_id: int):
        return {}

    async def get_stock_by_stock_id(self, stock_id: str):
        return None


# ── Fake BarcodeBuddyAPI ────────────────────────────────────────────


class FakeBarcodeBuddyAPI:
    """In-memory fake of BarcodeBuddyAPI.

    Replicates the mode conversion tables from the real implementation.
    Scan actions raise NotImplementedError (same as BarcodeBuddyAPI_Fake).
    """

    def __init__(self, initial_mode: int = 2) -> None:
        self.mode = initial_mode  # default = PURCHASE

    def convert_scan_mode_to_bbuddy_mode(self, mode: SCAN_MODE) -> int:
        _MAP = {
            SCAN_MODE.SCAN_BBUDDY: -1,
            SCAN_MODE.CONSUME: 0,
            SCAN_MODE.CONSUME_SPOILED: 1,
            SCAN_MODE.PURCHASE: 2,
            SCAN_MODE.OPEN: 3,
            SCAN_MODE.INVENTORY: 4,
            SCAN_MODE.ADD_TO_SHOPPING_LIST: 5,
            SCAN_MODE.CONSUME_ALL: 6,
        }
        return _MAP.get(mode, -1)

    def convert_bbuddy_mode_to_scan_mode(self, bb_mode: int) -> SCAN_MODE | None:
        _MAP = {
            -1: SCAN_MODE.SCAN_BBUDDY,
            0: SCAN_MODE.CONSUME,
            1: SCAN_MODE.CONSUME_SPOILED,
            2: SCAN_MODE.PURCHASE,
            3: SCAN_MODE.OPEN,
            4: SCAN_MODE.INVENTORY,
            5: SCAN_MODE.ADD_TO_SHOPPING_LIST,
            6: SCAN_MODE.CONSUME_ALL,
        }
        return _MAP.get(bb_mode)

    async def get_mode(self) -> int:
        return self.mode

    async def set_mode(self, mode: int) -> None:
        self.mode = mode

    async def post_scan(self, request: dict) -> dict:
        """Simulate a BBuddy scan.  Returns a simple success response."""
        return {"result": "OK", "barcode": request.get("barcode", "")}


# ── Fake Coordinator ────────────────────────────────────────────────


class FakeCoordinator:
    """Minimal stand-in for GrocyHelperCoordinator.

    Provides the attributes and methods that ScanSession and ScanQueue
    access on the coordinator. No Home Assistant dependency.
    """

    def __init__(
        self,
        grocy_api: FakeGrocyAPI | None = None,
        bbuddy_api: FakeBarcodeBuddyAPI | None = None,
        master_data: GrocyMasterData | None = None,
    ) -> None:
        self._api_grocy = grocy_api or FakeGrocyAPI()
        self._api_bbuddy = bbuddy_api or FakeBarcodeBuddyAPI()
        self.data = master_data or make_master_data()
        # Stub hass-related attributes that coordinator normally has
        self._hass = MagicMock()
        self._hass.services.has_service.return_value = False

    async def fetch_data(self) -> GrocyMasterData:
        return self.data

    async def async_request_refresh(self) -> None:
        pass

    async def lookup_barcode(self, code: str) -> BarcodeLookup:
        return {
            "barcode": code,
            "off": None,
            "ica": None,
            "lookup_output": "",
            "product_aliases": [],
        }

    async def create_product(self, user_input: dict) -> GrocyProduct:
        product = await self._api_grocy.add_product(user_input)
        if self.data and "products" in self.data:
            self.data["products"].append(product)
        return product

    async def update_product(self, product_id: int, changes: dict) -> dict:
        return await self._api_grocy.update_product(product_id, changes)

    async def create_product_barcode(self, data: dict) -> dict:
        return await self._api_grocy.add_product_barcode(data)

    async def create_quantity_unit_conversion(self, data: dict) -> dict:
        return await self._api_grocy.add_product_quantity_unit_conversion(data)

    async def transfer_stock_entry(self, product_id: int, data: dict) -> dict:
        return await self._api_grocy.transfer_stock_entry(product_id, data)

    async def add_stock(self, product_id: int, data: dict) -> dict:
        return await self._api_grocy.add_stock_product(product_id, data)

    async def create_recipe(self, data: dict) -> dict:
        return await self._api_grocy.create_recipe(data)

    async def update_recipe(self, recipe_id: int, changes: dict) -> dict:
        return await self._api_grocy.update_recipe(recipe_id, changes)

    async def convert_quantity_for_product(
        self, product_id, from_qu_id, to_qu_id, amount
    ):
        return {"to_amount": amount, "factor": 1.0}


# ── Fake HA Store ────────────────────────────────────────────────────


class FakeStore:
    """In-memory fake of homeassistant.helpers.storage.Store.

    Supports async_load / async_save without filesystem access.
    """

    def __init__(self, version: int = 1, key: str = "test") -> None:
        self._data: dict | None = None
        self.version = version
        self.key = key

    async def async_load(self) -> dict | None:
        return self._data

    async def async_save(self, data: dict) -> None:
        self._data = data


# ── Pytest fixtures ──────────────────────────────────────────────────


@pytest.fixture
def fake_grocy_api() -> FakeGrocyAPI:
    return FakeGrocyAPI()


@pytest.fixture
def fake_bbuddy_api() -> FakeBarcodeBuddyAPI:
    return FakeBarcodeBuddyAPI()


@pytest.fixture
def fake_coordinator(
    fake_grocy_api: FakeGrocyAPI,
    fake_bbuddy_api: FakeBarcodeBuddyAPI,
) -> FakeCoordinator:
    return FakeCoordinator(
        grocy_api=fake_grocy_api,
        bbuddy_api=fake_bbuddy_api,
    )


@pytest.fixture
def fake_store() -> FakeStore:
    return FakeStore()
