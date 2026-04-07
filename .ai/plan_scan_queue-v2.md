# ha-grocy-helper

- HACS integration for Home Assistant, uses OptionsFlow as UI
- Integrates: Grocy API, Barcode Buddy, ICA (Swedish grocery), OpenFoodFacts, Niimbot label printer (via Grocy webhook)
- Deploy script: copies `custom_components/grocy_helper` to `C:\HomeAssistant\config\custom_components\grocy_helper` then `docker-compose up`
- Key files: scan_session.py (workflow), scan_form_builders.py (forms), config_flow.py (HA adapter), grocyapi.py (API), const.py (URLs/enums)
- Grocy API spec at `resources/grocy-api-openapi-spec.json`
- Recipe produce flow: 2-form flow (SCAN_PROCESS → SCAN_PRODUCE_CONFIRM)
  - Consumes recipe via: update desired_servings → /recipes/{id}/consume → restore desired_servings
- `_produce_input` dict stashes form 1 values between the two steps
- Testing: pytest + pytest-asyncio, tests/ dir. Run `pytest` from repo root in venv.
- No pyproject.toml/setup.cfg existed before queue feature — created for pytest config.

## Architecture
- Coordinator: GrocyHelperCoordinator (DataUpdateCoordinator) — holds master data cache, API wrappers, stateless between scans
- ScanSession: per-OptionsFlow-session, owns barcode_queue (in-memory), ScanStateManager, form builders
- ScanStateManager: mutable per-barcode state (product, lookup, recipe context, stock entries)
- FormField: framework-agnostic field descriptor. `suggested_value`=hint (may be wrong), `default`=value to use if user doesn't intervene
- Steps: MAIN_MENU, SCAN_START, SCAN_QUEUE (internal), SCAN_MATCH_PRODUCT, SCAN_ADD_PRODUCT, SCAN_ADD_PRODUCT_PARENT, SCAN_ADD_PRODUCT_BARCODE, SCAN_CREATE_RECIPE, SCAN_UPDATE_PRODUCT_DETAILS, SCAN_TRANSFER_START, SCAN_TRANSFER_INPUT, SCAN_PRODUCE, SCAN_PRODUCE_CONFIRM, SCAN_PROCESS
- SCAN_MODE: CONSUME, CONSUME_SPOILED, CONSUME_ALL, PURCHASE, OPEN, INVENTORY, ADD_TO_SHOPPING_LIST, QUANTITY, SCAN_BBUDDY, TRANSFER, PROVISION
- 44 FormField instantiations, all in scan_form_builders.py. 12 have `default`, 42 have `suggested_value`.
- scan_options dict built from config_entry_data controls form display (input_price, input_bestBeforeInDays, etc.)

## Queue Feature (in progress)
- Persistent scan queue via HA Store, fed by webhook
- Auto-resolve on arrival: headless ScanSession, uses ONLY `default` field values
- Dynamic "current mode" persisted alongside queue (init PURCHASE, switchable by mode barcodes)
- Webhook accepts `barcode` (str) or `barcodes` (array), optional `mode`, structured format supported
- Fail-fast no-rollback strategy for auto-resolve failures
- Handle Queue menu item in OptionsFlow for manual processing of pending/failed items
- Fire `grocy_helper_queue_resolved` HA event on auto-resolve
- Webhook returns sync per-barcode status
- Failed items: show in Handle Queue with error, allow retry or manual processing
