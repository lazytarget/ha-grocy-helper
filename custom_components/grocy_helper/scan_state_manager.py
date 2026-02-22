"""State management for scan session workflow.

This module manages the current product, stock info, and other workflow state,
ensuring consistency and proper cache updates.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .grocyapi import GrocyAPI
    from .grocytypes import (
        BarcodeLookup,
        ExtendedGrocyProductStockInfo,
        GrocyProduct,
        GrocyRecipe,
        GrocyStockEntry,
        OpenFoodFactsProduct,
    )

_LOGGER = logging.getLogger(__name__)


class ScanStateManager:
    """Manages workflow state for a scan session.

    This class ensures that product and stock information stays in sync,
    handles product loading, and updates the masterdata cache when products
    are created or modified.
    """

    def __init__(
        self,
        api_grocy: GrocyAPI,
    ) -> None:
        """Initialize state manager.

        Parameters
        ----------
        api_grocy:
            Grocy API instance for loading products
        """
        self._api_grocy = api_grocy

        # Primary product state - stock info contains full details
        self._current_stock_info: ExtendedGrocyProductStockInfo | None = None

        # External lookup data
        self.current_lookup: BarcodeLookup | None = None
        self.current_product_openfoodfacts: OpenFoodFactsProduct | None = None
        self.current_product_ica: dict | None = None

        # Product matching
        self.matching_products: list[GrocyProduct] = []

        # Parent product (when creating hierarchies)
        self.current_parent: GrocyProduct | None = None

        # Recipe integration
        self.current_recipe: GrocyRecipe | None = None

        # Transfer workflow
        self.current_stock_entries: list[GrocyStockEntry] = []

    @property
    def current_product(self) -> GrocyProduct | None:
        """Get current product.

        This always returns the product from stock_info if available,
        ensuring consistency.
        """
        if self._current_stock_info:
            return self._current_stock_info.get("product")
        return None

    @property
    def current_stock_info(self) -> ExtendedGrocyProductStockInfo | None:
        """Get current extended stock information."""
        return self._current_stock_info

    def set_product(self, product: GrocyProduct | None) -> None:
        """Set current product without stock info.

        Use this when you only have basic product data (e.g., during creation).
        For existing products, prefer load_product_by_id() or load_product_by_barcode().
        """
        if product is None:
            self._current_stock_info = None
        else:
            # Wrap in minimal stock info structure
            self._current_stock_info = {"product": product}

    def set_stock_info(self, stock_info: ExtendedGrocyProductStockInfo | None) -> None:
        """Set current stock info (which includes product).

        This is the preferred way to update product state for existing products.
        """
        self._current_stock_info = stock_info

    async def load_product_by_id(self, product_id: int) -> GrocyProduct | None:
        """Load product by ID and update state.

        Returns the product and updates current_stock_info.
        """
        try:
            stock_info = await self._api_grocy.get_stock_product_by_id(product_id)
            self.set_stock_info(stock_info)
            return self.current_product
        except Exception as ex:
            _LOGGER.error("Failed to load product by ID %s: %s", product_id, ex)
            return None

    async def load_product_by_barcode(self, barcode: str) -> GrocyProduct | None:
        """Load product by barcode and update state.

        Returns the product, or None if not found.
        """
        try:
            stock_info = await self._api_grocy.get_stock_product_by_barcode(barcode)
            self.set_stock_info(stock_info)
            return self.current_product
        except Exception as ex:
            _LOGGER.error("Failed to load product by barcode %s: %s", barcode, ex)
            return None

    async def ensure_stock_info_loaded(self) -> bool:
        """Ensure full stock info is loaded for current product.

        If we only have basic product data, this loads the full stock info.
        Returns True if stock info is available after the operation.
        """
        if not self.current_product:
            return False

        # If we already have extended info, we're good
        if self._current_stock_info and len(self._current_stock_info) > 1:
            return True

        # Need to load full info
        product_id = self.current_product.get("id")
        if not product_id:
            return False

        _LOGGER.debug("Loading full stock info for product #%s", product_id)
        await self.load_product_by_id(product_id)
        return self._current_stock_info is not None

    def update_current_product(self, changes: dict) -> None:
        """Update current product with changes (session state only).

        This only updates the local session state. For persistent changes,
        call the coordinator's update_product() method.
        """
        if not self.current_product:
            _LOGGER.warning("Cannot update product - no current product set")
            return

        self.current_product.update(changes)

    def clear_all(self) -> None:
        """Clear all state (for new barcode processing)."""
        self._current_stock_info = None
        self.current_lookup = None
        self.current_product_openfoodfacts = None
        self.current_product_ica = None
        self.current_parent = None
        self.current_recipe = None
        self.matching_products = []
        self.current_stock_entries = []

    def clear_barcode_state(self) -> None:
        """Clear state when moving to a new barcode.

        Keeps recipe info if set, clears everything else.
        """
        self._current_stock_info = None
        self.current_lookup = None
        self.current_product_openfoodfacts = None
        self.current_product_ica = None
        self.current_parent = None

        # Don't clear recipe state - it may apply to next product
        # self.current_recipe = None

        self.matching_products = []
        self.current_stock_entries = []
