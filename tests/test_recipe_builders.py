"""Tests for RecipeDataBuilder (Area 8).

Covers:
- 8a: build_recipe_from_input happy path
- 8b: build_recipe_from_input edge cases for missing optional fields
"""

from __future__ import annotations

import datetime as dt

from custom_components.grocy_helper.scan_recipe_builders import RecipeDataBuilder


def test_build_recipe_from_input_happy_path_sets_expected_fields():
    """Happy path: explicit user input is copied into the recipe payload."""
    user_input = {
        "name": "Tomato Soup",
        "base_servings": 6,
        "desired_servings": 3,
        "description": "Simple and quick",
        "type": "normal",
    }
    base_recipe = {
        "id": 17,
        "product_id": 55,
    }

    recipe = RecipeDataBuilder.build_recipe_from_input(user_input, base_recipe)

    assert recipe["name"] == "Tomato Soup"
    assert recipe["base_servings"] == 6
    assert recipe["desired_servings"] == 3
    assert recipe["description"] == "Simple and quick"
    assert recipe["type"] == "normal"
    # Existing base fields are preserved
    assert recipe["id"] == 17
    assert recipe["product_id"] == 55



def test_build_recipe_from_input_defaults_optional_fields_when_missing():
    """Missing optional fields should fall back to documented defaults."""
    user_input = {
        "name": "Overnight Oats",
    }

    recipe = RecipeDataBuilder.build_recipe_from_input(user_input)

    assert recipe["name"] == "Overnight Oats"
    assert recipe["base_servings"] == 4
    assert recipe["desired_servings"] == 1
    assert recipe["description"] == ""
    assert recipe["type"] == "normal"



def test_build_recipe_from_input_overwrites_existing_base_values():
    """User input values override existing base recipe values for mapped keys."""
    user_input = {
        "name": "Curry",
        "base_servings": 2,
        "desired_servings": 2,
        "description": "Spicy",
        "type": "normal",
    }
    base_recipe = {
        "name": "Old Name",
        "base_servings": 9,
        "desired_servings": 9,
        "description": "Old",
        "type": "old-type",
    }

    recipe = RecipeDataBuilder.build_recipe_from_input(user_input, base_recipe)

    assert recipe["name"] == "Curry"
    assert recipe["base_servings"] == 2
    assert recipe["desired_servings"] == 2
    assert recipe["description"] == "Spicy"
    assert recipe["type"] == "normal"



def test_build_recipe_from_input_sets_timestamp_in_expected_format():
    """row_created_timestamp is emitted in '%Y-%m-%d %H:%M:%S' format."""
    before = dt.datetime.now()
    recipe = RecipeDataBuilder.build_recipe_from_input({"name": "Pizza"})
    after = dt.datetime.now()

    parsed = dt.datetime.strptime(recipe["row_created_timestamp"], "%Y-%m-%d %H:%M:%S")

    # Second-level precision is enough for this helper.
    assert before.replace(microsecond=0) <= parsed <= after.replace(microsecond=0)
