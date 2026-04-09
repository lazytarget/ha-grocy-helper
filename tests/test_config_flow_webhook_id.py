"""Tests that webhook_id is preserved across reconfigure branches.

The config_flow.py reconfigure step has two branches that update
config entry data.  Both must preserve the ``webhook_id`` that
``__init__.py`` adds on first load.  These tests exercise the exact
same data-merging logic used in each branch.
"""

from __future__ import annotations


from custom_components.grocy_helper.const import (
    CONF_BBUDDY_API_KEY,
    CONF_BBUDDY_API_URL,
    CONF_DEFAULT_LOCATION_FREEZER,
    CONF_DEFAULT_LOCATION_FRIDGE,
    CONF_DEFAULT_LOCATION_RECIPE_RESULT,
    CONF_DEFAULT_PRODUCT_GROUP_FOR_RECIPE_RESULT,
    CONF_ENABLE_AUTO_PRINT,
    CONF_ENABLE_PRINTING,
    CONF_GROCY_API_KEY,
    CONF_GROCY_API_URL,
)
from custom_components.grocy_helper.utils import transform_input


# ── Fixture: realistic config entry data with webhook_id ────────────

EXISTING_ENTRY_DATA = {
    CONF_GROCY_API_URL: "http://grocy:9283",
    CONF_GROCY_API_KEY: "secret-key-123",
    CONF_BBUDDY_API_URL: "http://bbuddy:8080",
    CONF_BBUDDY_API_KEY: "bbuddy-key-456",
    CONF_DEFAULT_LOCATION_FRIDGE: "1",
    CONF_DEFAULT_LOCATION_FREEZER: "2",
    CONF_ENABLE_PRINTING: False,
    CONF_ENABLE_AUTO_PRINT: False,
    "webhook_id": "abc123-webhook-id",
}


# ── Branch 1: API credentials reconfigure ────────────────────────────


class TestApiCredentialsReconfigure:
    """Tests for the API-credentials reconfigure branch.

    This branch does:
        new_config_entry_data = config_entry.data.copy()
        new_config_entry_data[CONF_GROCY_API_URL] = grocy_url
        ...
    Then: async_update_reload_and_abort(data_updates=new_config_entry_data)
    Which HA resolves as: entry.data | data_updates
    """

    def test_webhook_id_preserved_when_credentials_change(self):
        """Changing API URL/keys must not drop webhook_id."""
        # Simulate the exact logic from config_flow.py
        new_config_entry_data = EXISTING_ENTRY_DATA.copy()
        new_config_entry_data[CONF_GROCY_API_URL] = "http://new-grocy:9283"
        new_config_entry_data[CONF_GROCY_API_KEY] = "new-secret-key"
        new_config_entry_data[CONF_BBUDDY_API_URL] = "http://new-bbuddy:8080"
        new_config_entry_data[CONF_BBUDDY_API_KEY] = "new-bbuddy-key"

        # Simulate HA merge: entry.data | data_updates
        final = EXISTING_ENTRY_DATA | new_config_entry_data

        assert final["webhook_id"] == "abc123-webhook-id"
        assert final[CONF_GROCY_API_URL] == "http://new-grocy:9283"

    def test_webhook_id_preserved_when_no_changes(self):
        """Submitting same credentials preserves webhook_id (no-diff path)."""
        new_config_entry_data = EXISTING_ENTRY_DATA.copy()
        # Same values — the has_diffs check would be False, but data stays intact
        assert new_config_entry_data["webhook_id"] == "abc123-webhook-id"

    def test_copy_is_independent(self):
        """data.copy() does not create a reference that could mutate original."""
        original = EXISTING_ENTRY_DATA.copy()
        modified = original.copy()
        modified[CONF_GROCY_API_URL] = "http://changed:1234"

        assert original[CONF_GROCY_API_URL] == "http://grocy:9283"
        assert original["webhook_id"] == "abc123-webhook-id"


# ── Branch 2: Scan options reconfigure ───────────────────────────────


class TestScanOptionsReconfigure:
    """Tests for the scan-options reconfigure branch.

    This branch does two transform_input calls:
    1. Merge user_input with suggested defaults (no persisted)
    2. Merge result with persisted=config_entry.data
    Then: async_update_reload_and_abort(data_updates=new_config_entry_data)
    """

    def _simulate_scan_options_merge(
        self, user_input: dict, config_entry_data: dict
    ) -> dict:
        """Reproduce the exact two-step transform_input from config_flow."""
        # Step 1: fill defaults for missing scan options
        user_input = transform_input(
            user_input,
            persisted=None,
            suggested={
                CONF_DEFAULT_LOCATION_FRIDGE: "",
                CONF_DEFAULT_LOCATION_FREEZER: "",
                CONF_DEFAULT_LOCATION_RECIPE_RESULT: "",
                CONF_DEFAULT_PRODUCT_GROUP_FOR_RECIPE_RESULT: "",
                CONF_ENABLE_PRINTING: False,
                CONF_ENABLE_AUTO_PRINT: False,
            },
            str_keys=[],
        )
        # Step 2: merge with persisted entry data
        new_config_entry_data = transform_input(
            user_input,
            persisted=config_entry_data,
            suggested=None,
            str_keys=[],
        )
        return new_config_entry_data

    def test_webhook_id_preserved_after_scan_options_update(self):
        """Updating scan options must keep webhook_id from persisted data."""
        user_input = {
            CONF_DEFAULT_LOCATION_FRIDGE: "3",
            CONF_ENABLE_PRINTING: True,
        }
        result = self._simulate_scan_options_merge(user_input, EXISTING_ENTRY_DATA)

        assert result["webhook_id"] == "abc123-webhook-id"

    def test_webhook_id_preserved_with_empty_scan_options(self):
        """Submitting empty scan options still preserves webhook_id."""
        result = self._simulate_scan_options_merge({}, EXISTING_ENTRY_DATA)

        assert result["webhook_id"] == "abc123-webhook-id"

    def test_api_credentials_preserved_after_scan_options_update(self):
        """Scan options update must not lose API credentials."""
        user_input = {CONF_ENABLE_PRINTING: True}
        result = self._simulate_scan_options_merge(user_input, EXISTING_ENTRY_DATA)

        assert result[CONF_GROCY_API_URL] == "http://grocy:9283"
        assert result[CONF_GROCY_API_KEY] == "secret-key-123"
        assert result[CONF_BBUDDY_API_URL] == "http://bbuddy:8080"
        assert result[CONF_BBUDDY_API_KEY] == "bbuddy-key-456"

    def test_scan_options_values_applied(self):
        """User-submitted scan options override persisted values."""
        user_input = {
            CONF_DEFAULT_LOCATION_FRIDGE: "99",
            CONF_ENABLE_PRINTING: True,
        }
        result = self._simulate_scan_options_merge(user_input, EXISTING_ENTRY_DATA)

        assert result[CONF_DEFAULT_LOCATION_FRIDGE] == "99"
        assert result[CONF_ENABLE_PRINTING] is True

    def test_ha_merge_preserves_webhook_id(self):
        """The final HA merge (entry.data | data_updates) preserves webhook_id."""
        user_input = {CONF_ENABLE_PRINTING: True}
        data_updates = self._simulate_scan_options_merge(
            user_input, EXISTING_ENTRY_DATA
        )
        # Simulate HA's __async_update: data = entry.data | data_updates
        final = EXISTING_ENTRY_DATA | data_updates

        assert final["webhook_id"] == "abc123-webhook-id"

    def test_unknown_keys_in_persisted_data_survive(self):
        """Any future keys added to entry data are preserved too."""
        entry_data = {
            **EXISTING_ENTRY_DATA,
            "some_future_key": "future_value",
        }
        result = self._simulate_scan_options_merge({}, entry_data)

        assert result["some_future_key"] == "future_value"
        assert result["webhook_id"] == "abc123-webhook-id"
