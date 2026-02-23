"""Recipe data building and transformation utilities.

This module contains functions for building recipe dictionaries from user
input, parsing external data sources, validating recipe
data, and transforming data for API requests.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from .coordinator import GrocyHelperCoordinator
from .grocytypes import GrocyMasterData, GrocyRecipe


class RecipeDataBuilder:
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
    def build_recipe_from_input(
        user_input: dict[str, Any], base_recipe: dict[str, Any] | None = None
    ) -> GrocyRecipe:
        """Build a new recipe dict from user input.

        Parameters
        ----------
        user_input:
            User input from the create recipe form
        base_recipe:
            Base recipe dictionary to start from

        Returns
        -------
            Complete recipe dictionary ready for API submission
        """
        base_recipe = base_recipe or {}
        recipe = GrocyRecipe(base_recipe.copy())
        recipe["name"] = user_input["name"]
        recipe["base_servings"] = user_input.get("base_servings", 4)
        recipe["desired_servings"] = user_input.get("desired_servings", 1)
        recipe["description"] = user_input.get("description", "")
        recipe["type"] = user_input.get("type", "normal")

        recipe["row_created_timestamp"] = dt.datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        return recipe
