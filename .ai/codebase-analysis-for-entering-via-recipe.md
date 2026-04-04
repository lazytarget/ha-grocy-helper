Read complete: all requested files were fully read end-to-end, plus [custom_components/grocy_helper/barcodebuddyapi.py](custom_components/grocy_helper/barcodebuddyapi.py#L1) for mode mapping context. No files were modified.

**High-Level Answers**
1. Purchase flow today is a scan-queue workflow that may create/match products first, then executes either Barcode Buddy scan or direct Grocy stock add depending on shopping location behavior. Main logic lives in [custom_components/grocy_helper/scan_session.py](custom_components/grocy_helper/scan_session.py#L232).
2. Recipe interaction exists, but “produce” is mostly conceptual/partial. There is recipe creation, recipe barcode handling, recipe-to-product linking, and purchase-mode adjustments; there is no dedicated Grocy “produce endpoint” wrapper in this code.
3. Printing works via Grocy print-label endpoints only. No webhook-specific implementation exists in this repo.
4. OptionsFlow forms are framework-agnostic FormField models converted to HA selectors in [custom_components/grocy_helper/config_flow.py](custom_components/grocy_helper/config_flow.py#L422).

**Purchase Flow Step-by-Step**
1. Start step renders mode + barcode input in [custom_components/grocy_helper/scan_session.py](custom_components/grocy_helper/scan_session.py#L271), fields built by [custom_components/grocy_helper/scan_form_builders.py](custom_components/grocy_helper/scan_form_builders.py#L36).
2. Submitted barcode text is parsed into a queue; supports plain and structured forms like angle-wrapped payloads with metadata (q/u/p/s/n) in [custom_components/grocy_helper/scan_session.py](custom_components/grocy_helper/scan_session.py#L297) and [custom_components/grocy_helper/scan_session.py](custom_components/grocy_helper/scan_session.py#L1033).
3. For each barcode, queue step normalizes barcode, resets state if new barcode, stores metadata, and updates Barcode Buddy mode in [custom_components/grocy_helper/scan_session.py](custom_components/grocy_helper/scan_session.py#L320).
4. If lookup is needed, it attempts Grocy product load + external provider lookup via coordinator in [custom_components/grocy_helper/scan_session.py](custom_components/grocy_helper/scan_session.py#L1365) and [custom_components/grocy_helper/coordinator.py](custom_components/grocy_helper/coordinator.py#L108).
5. If product not found, it shows match form, optionally parent selection, then potentially create product, create barcode, optionally update product details, optionally create parent, then continue in [custom_components/grocy_helper/scan_session.py](custom_components/grocy_helper/scan_session.py#L441).
6. If in purchase mode, scan process may show extra fields (price, best_before_in_days, shopping_location_id) from [custom_components/grocy_helper/scan_form_builders.py](custom_components/grocy_helper/scan_form_builders.py#L560).
7. Request payload is built in [custom_components/grocy_helper/scan_product_builders.py](custom_components/grocy_helper/scan_product_builders.py#L428).
8. Execution branch:
1. If purchase mode and shopping_location_id present: direct Grocy add-stock call through coordinator, sets transaction_type=purchase, amount=1, converts bestBeforeInDays to best_before_date in [custom_components/grocy_helper/scan_session.py](custom_components/grocy_helper/scan_session.py#L1756).
2. Else: sends Barcode Buddy scan request in [custom_components/grocy_helper/scan_session.py](custom_components/grocy_helper/scan_session.py#L1788).
9. Success pops barcode and continues until queue empty; returns CompletedResult summary in [custom_components/grocy_helper/scan_session.py](custom_components/grocy_helper/scan_session.py#L1325).

**Recipes vs Purchase / Produce**
- Recipe creation is supported through step scan_create_recipe in [custom_components/grocy_helper/scan_session.py](custom_components/grocy_helper/scan_session.py#L877) and recipe builder in [custom_components/grocy_helper/scan_recipe_builders.py](custom_components/grocy_helper/scan_recipe_builders.py#L30).
- Barcode grcy:r creates recipe flow trigger; barcode grcy:r:id loads recipe and, if recipe has product_id, loads that product into current state in [custom_components/grocy_helper/scan_session.py](custom_components/grocy_helper/scan_session.py#L390).
- If a new product is created while current recipe has no product_id, recipe gets linked to created product in [custom_components/grocy_helper/scan_session.py](custom_components/grocy_helper/scan_session.py#L1288).
- In purchase extra-fields form, price and shopping location prompts are suppressed for current_recipe context in [custom_components/grocy_helper/scan_form_builders.py](custom_components/grocy_helper/scan_form_builders.py#L580).
- “Produce” status:
1. UI labels include “Purchase / Produce” in [custom_components/grocy_helper/scan_form_builders.py](custom_components/grocy_helper/scan_form_builders.py#L67).
2. Recipe-produced product defaults exist (location/group/best-before defaults) in [custom_components/grocy_helper/scan_session.py](custom_components/grocy_helper/scan_session.py#L1302).
3. Multiple TODOs explicitly describe future produce behavior for recipe barcodes.
4. No dedicated produce API method or separate produce transaction path currently implemented.

**Printing / Labels / Webhook**
- Config flags: enable printing + auto print in [custom_components/grocy_helper/const.py](custom_components/grocy_helper/const.py#L17).
- Recipe creation form can include print checkbox when printing enabled in [custom_components/grocy_helper/scan_form_builders.py](custom_components/grocy_helper/scan_form_builders.py#L535).
- On recipe creation, if print selected, calls print label for recipe in [custom_components/grocy_helper/scan_session.py](custom_components/grocy_helper/scan_session.py#L919).
- On direct Grocy purchase add-stock path, if printing+auto-print enabled, it resolves stock row and calls print label for stock entry in [custom_components/grocy_helper/scan_session.py](custom_components/grocy_helper/scan_session.py#L1772).
- Grocy print endpoints wrapped in [custom_components/grocy_helper/grocyapi.py](custom_components/grocy_helper/grocyapi.py#L197).
- HTTP layer has no webhook flow; it only does generic GET/POST/PUT/DELETE with headers in [custom_components/grocy_helper/http_requests.py](custom_components/grocy_helper/http_requests.py#L1).

**Scan States, Modes, Enums**
- Workflow steps enum Step in [custom_components/grocy_helper/scan_types.py](custom_components/grocy_helper/scan_types.py#L19):
1. main_menu
2. scan_start
3. scan_queue
4. scan_match_to_product
5. scan_add_product
6. scan_add_product_parent
7. scan_add_product_barcode
8. scan_create_recipe
9. scan_update_product_details
10. scan_transfer_start
11. scan_transfer_input
12. scan_process
- Field enums in [custom_components/grocy_helper/scan_types.py](custom_components/grocy_helper/scan_types.py#L44):
1. FieldType: text, number, select, boolean
2. NumberMode: box, slider
3. SelectMode: dropdown, list
- Scan modes enum in [custom_components/grocy_helper/const.py](custom_components/grocy_helper/const.py#L22):
1. BBuddy actions: consume, consume spoiled, consume all, purchase, open, inventory, add to shopping list, quantity
2. Custom: scan_bbuddy, transfer, provision
- Barcode Buddy mode mapping in [custom_components/grocy_helper/barcodebuddyapi.py](custom_components/grocy_helper/barcodebuddyapi.py#L38): consume=0, consume_spoiled=1, purchase=2, open=3, inventory=4, add_to_shopping_list=5, consume_all=6.

**OptionsFlow and Form Construction**
- OptionsFlow is a thin adapter around ScanSession in [custom_components/grocy_helper/config_flow.py](custom_components/grocy_helper/config_flow.py#L240).
- Each async_step_scan_* delegates to session.handle_step and converts StepResult in [custom_components/grocy_helper/config_flow.py](custom_components/grocy_helper/config_flow.py#L320).
- Form conversion pipeline:
1. ScanSession/Builder emits FormRequest/FormField in [custom_components/grocy_helper/scan_types.py](custom_components/grocy_helper/scan_types.py#L101).
2. Config flow maps each FormField to HA selector via _field_to_vol in [custom_components/grocy_helper/config_flow.py](custom_components/grocy_helper/config_flow.py#L438).
3. Then vol.Schema is returned from _form_request_to_schema in [custom_components/grocy_helper/config_flow.py](custom_components/grocy_helper/config_flow.py#L514).
- Reconfigure options form uses build_scan_options_fields in [custom_components/grocy_helper/scan_form_builders.py](custom_components/grocy_helper/scan_form_builders.py#L626), persists into config_entry.data in [custom_components/grocy_helper/config_flow.py](custom_components/grocy_helper/config_flow.py#L194).

**Grocy API Surface (especially purchase/produce/recipes/stock entries)**
From [custom_components/grocy_helper/grocyapi.py](custom_components/grocy_helper/grocyapi.py#L1):
- Master data:
1. get_locations
2. get_shopping_locations
3. get_quantityunits
4. get_products
5. get_product_groups
6. get_recipes
- Product and barcode:
1. get_product_by_id
2. add_product
3. update_product
4. get_product_barcode_by_id
5. add_product_barcode
- Stock:
1. get_stock_product_by_id
2. get_stock_product_by_barcode
3. get_stock_entries_by_product_id
4. get_stock_by_stock_id
5. add_stock_product (purchase stock add)
6. transfer_stock_entry
- Quantity conversions:
1. resolve_quantity_unit_conversions_for_product_id
2. add_product_quantity_unit_conversion
- Recipes:
1. create_recipe
2. update_recipe
- Printing:
1. print_label_for_product
2. print_label_for_stock_entry
3. print_label_for_recipe

There is no explicit produce endpoint wrapper. “Produce” behavior currently piggybacks purchase/add-stock semantics and recipe linking logic.

**State Manager Transitions**
From [custom_components/grocy_helper/scan_state_manager.py](custom_components/grocy_helper/scan_state_manager.py#L1):
- Holds current stock info, lookup payloads, matching products, parent, recipe, stock entries.
- load_product_by_id / load_product_by_barcode update canonical current stock info.
- ensure_stock_info_loaded upgrades minimal product state to full stock info.
- clear_all resets all state; clear_barcode_state resets barcode-scoped state but intentionally keeps current_recipe.

**Services**
- Only one integration service registered in [custom_components/grocy_helper/services.py](custom_components/grocy_helper/services.py#L53):
1. resolve_quantity_unit_conversion_for_product
- Service schema/fields documented in [custom_components/grocy_helper/services.yaml](custom_components/grocy_helper/services.yaml#L1).
- No service for produce, purchase, print, or recipe actions.

**Constants**
From [custom_components/grocy_helper/const.py](custom_components/grocy_helper/const.py#L1):
- Domain and config keys for Grocy/Barcode Buddy URLs/keys.
- Defaults for fridge/freezer/recipe-result location, recipe product group, printing flags.
- SCAN_MODE enum values.
- API URL constants for all Grocy + Barcode Buddy endpoints.
- OpenFoodFacts API URL and default fields.
- DEV_CONST defaults and NUMERIC_FIELDS set.

**Coordinator + Utils**
- Coordinator responsibilities in [custom_components/grocy_helper/coordinator.py](custom_components/grocy_helper/coordinator.py#L31):
1. Periodic masterdata refresh
2. External barcode lookup (ICA + OpenFoodFacts)
3. Create/update product and recipe
4. Add stock and transfer stock
5. Quantity conversion resolution
- Utility helpers in [custom_components/grocy_helper/utils.py](custom_components/grocy_helper/utils.py#L1):
1. parse_int / try_parse_int
2. transform_input merge precedence: user_input > persisted > suggested (with string conversion rules)

**Direct Answer: “entered via recipe” concept**
Yes, there is a stateful “recipe context” concept via current_recipe and recipe barcodes in [custom_components/grocy_helper/scan_session.py](custom_components/grocy_helper/scan_session.py#L390). It is not represented as a separate explicit mode/state enum like “entered_via_recipe”; it is inferred from current_recipe being set and barcode patterns like grcy:r:id.