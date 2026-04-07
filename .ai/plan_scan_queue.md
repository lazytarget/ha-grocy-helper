# Plan: Persistent Scan Queue with Webhook & Auto-Resolve

## TL;DR
Add a persistent barcode queue to the coordinator, fed by an HA webhook. On arrival, attempt immediate auto-resolve by driving a headless ScanSession — using only `default` field values (never `suggested_value`). Items needing manual intervention are queued for processing via a new "Handle Queue" menu in OptionsFlow. A dynamic "current mode" variable persists alongside the queue, switchable by scanning mode barcodes. TDD approach: build test infrastructure first, write tests before implementing each component.

## Design Decisions
- Entry point: Webhook only
- Auto-resolve scope: Start simple — PURCHASE of known products where all required fields have defaults
- Persistence: HA `helpers.storage.Store`
- Mode: Optional in webhook payload; falls back to dynamic persisted "current mode" (initialized PURCHASE)
- Auto-resolve trigger: Immediate on webhook arrival
- Auto-resolver uses ONLY `default` values (not `suggested_value`) — defaults represent "correct when user doesn't intervene", suggested values are hints that may be wrong
- Queue limits: None initially
- Reuse ScanSession headlessly for auto-resolve
- TDD: pytest test infrastructure first, tests before implementation

## Webhook Payload
```json
// Single barcode, simple
{"barcode": "3392590205420"}
// Single barcode with mode
{"barcode": "3392590205420", "mode": "BBUDDY-P"}
// Multiple barcodes as array
{"barcodes": ["3392590205420", "7340011492900"]}
// Structured barcode format
{"barcode": "<3392590205420|q:2|p:25.0>"}
// Mixed array with structured barcodes
{"barcodes": ["3392590205420", "<7340011492900|q:3|u:st|p:15.0>"]}
// Mode barcode (switches the persistent current mode)
{"barcode": "BBUDDY-AS"}
```
Both `barcode` (string) and `barcodes` (array of strings) are accepted. Multiple barcodes in a single event are processed atomically (all queued, then auto-resolved sequentially) to avoid race conditions.

## Dynamic Mode Variable
- Persisted alongside queue in the same Store file
- Initialized to `SCAN_MODE.PURCHASE`
- When a webhook barcode value matches a `SCAN_MODE` member's string value (e.g. "BBUDDY-AS"), the mode variable updates to that mode and the barcode is NOT queued for product processing
- Subsequent barcodes without explicit `mode` in the webhook payload use this persisted mode
- This mimics physical workflow: scan a "mode card" → switch modes → scan products

## Auto-Resolve Failure Strategy
Auto-resolve wraps a headless ScanSession. Since ScanSession calls real Grocy APIs, partial execution can leave state:

**Side-effect inventory per step:**
- `_set_bbuddy_mode()` → sets BBuddy mode (reversible, stateless)
- `add_stock()` → creates purchase entry (NOT reversible)
- `consume_stock_product()` → removes stock (NOT reversible)
- `post_scan()` → BBuddy delegates to Grocy (NOT reversible)
- `print_label_*()` → prints label (cosmetic, not critical)

**Strategy: Fail-fast, no rollback**
1. Auto-resolve runs inside a try/except
2. On exception: mark item as FAILED with error message, log full traceback
3. No rollback attempt — partial Grocy state is acceptable (user can fix manually)
4. Failed items surface in "Handle Queue" with error details visible
5. User can retry (re-attempt auto-resolve) or process manually
6. Rationale: Grocy doesn't support transactions. Attempting rollback (e.g. undo a purchase) adds complexity and its own failure modes. Better to be transparent about what happened.

**Max iteration guard:** Headless loop caps at 15 steps. If exceeded → FAILED with "loop limit" error.

## FormField: suggested_value vs default — Audit & Changes
The auto-resolver uses ONLY `default`. Current field audit shows many required fields have NO default:

**Fields that need defaults added (Phase 0 prep work):**
- `build_scan_process_fields`: `best_before_in_days` — has `suggested_value` from product's `default_best_before_days`, but no `default`. Should set `default` to same value since it's the product's own configured default.
- `build_scan_process_fields`: `price` — suggested_value from meta or None. Leave without default (price is optional, auto-resolve skips it).
- `build_scan_process_fields`: `shopping_location_id` — suggested from meta or None. Leave without default (optional).
- `build_produce_fields`: `produce_servings`, `produce_amount`, `produce_location_id` — these have suggested_values derived from recipe/product. For auto-resolve of produce, these should get defaults too. BUT produce auto-resolve is out of scope for v1 (complex flow, requires recipe context).
- `build_produce_confirm_fields`: `produce_print` — already has default = auto_print. Good.

**Key principle**: If a required field has exactly one sensible value (derived from product/recipe config), set it as `default` too. If there are multiple plausible values, keep it as `suggested_value` only → forces manual intervention.

## Phases

### Phase 0: Test Infrastructure (TDD Foundation)
**Files to create:**
1. `tests/conftest.py` — shared fixtures:
   - `FakeGrocyAPI` — mock with configurable responses for `get_stock_product_by_barcode`, `add_stock_product`, etc.
   - `FakeBarcodeBuddyAPI` — mock with no-op methods
   - `fake_coordinator()` fixture — builds `GrocyHelperCoordinator`-like object with FakeGrocyAPI, fake master data, fake websession
   - `fake_scan_session()` fixture — builds `ScanSession` with fake coordinator
   - `fake_hass()` fixture — minimal HA mock (for Store, webhook)
2. `tests/__init__.py`
3. `pytest.ini` or `pyproject.toml` — pytest config
4. Update `requirements-dev.txt` — add `pytest`, `pytest-asyncio`, `aiohttp` (for test client)

**Folder structure:**
```
tests/
  __init__.py
  conftest.py                    # shared fixtures, fakes
  test_queue.py                  # Phase 1 tests
  test_webhook.py                # Phase 2 tests
  test_auto_resolver.py          # Phase 3 tests
  test_scan_session_queue.py     # Phase 4 tests (Handle Queue flow)
```

### Phase 1: Queue Infrastructure
**Write tests first** (`tests/test_queue.py`):
- test_add_item_to_queue
- test_get_pending_items_filters_resolved
- test_mark_resolved_updates_status
- test_mark_failed_updates_status_and_error
- test_remove_item
- test_persistence_round_trip (save → new instance → load → verify)
- test_mode_barcode_updates_current_mode (e.g. add "BBUDDY-AS" → current_mode changes, no item queued)
- test_current_mode_persists_across_reload
- test_current_mode_initializes_to_purchase
- test_add_item_uses_current_mode_when_no_explicit_mode

**Then implement** `custom_components/grocy_helper/queue.py`:
- `QueueStatus` enum: PENDING, RESOLVED, FAILED
- `QueueItem` dataclass: id, barcode, mode, added_at, status, error, result, metadata
- `ScanQueue` class:
  - `__init__(hass, store_key, store_version)`
  - `async_load()` → loads items + current_mode from Store
  - `async_save()` → persists items + current_mode
  - `current_mode: SCAN_MODE` property (getter/setter, triggers save)
  - `async_add(barcode: str, mode: str | None = None, metadata: dict | None = None) -> QueueItem | None`
    - If barcode matches a SCAN_MODE value → update current_mode, return None (no item)
    - Else → create QueueItem with mode = explicit or current_mode, append, save, return item
  - `get_pending_items() -> list[QueueItem]`
  - `get_failed_items() -> list[QueueItem]`
  - `async_remove(item_id) -> bool`
  - `async_mark_resolved(item_id, result_text)`
  - `async_mark_failed(item_id, error_text)`
  - `async_clear_resolved()` — housekeeping

**Modify** `coordinator.py` — add `self.queue: ScanQueue`
**Modify** `__init__.py` — init queue, call `async_load()` in `async_setup_entry`
**Modify** `const.py` — add `STORAGE_KEY_QUEUE`, `STORAGE_VERSION_QUEUE`

### Phase 2: Webhook Entry Point
**Write tests first** (`tests/test_webhook.py`):
- test_webhook_single_barcode_string
- test_webhook_multiple_barcodes_array
- test_webhook_structured_barcode
- test_webhook_mixed_array_structured_and_plain
- test_webhook_with_explicit_mode
- test_webhook_without_mode_uses_current_mode
- test_webhook_mode_barcode_switches_mode
- test_webhook_invalid_payload_returns_400
- test_webhook_empty_barcode_returns_400
- test_webhook_returns_status_per_barcode

**Then implement** webhook handler:
- In `__init__.py`: generate webhook_id (persist in entry.data), register in setup, unregister in unload
- Handler: parse payload, validate, call `queue.async_add()` per barcode, attempt auto-resolve, return response

### Phase 3: Auto-Resolve Engine
**Write tests first** (`tests/test_auto_resolver.py`):
- test_known_product_purchase_auto_resolves
- test_unknown_product_stays_pending
- test_product_needing_match_stays_pending (SCAN_MATCH_PRODUCT form)
- test_product_needing_creation_stays_pending (SCAN_ADD_PRODUCT form)
- test_required_field_without_default_stays_pending
- test_auto_resolve_loop_limit_prevents_infinite
- test_auto_resolve_api_error_marks_failed
- test_auto_resolve_partial_failure_logged

**Then implement** auto-resolver (in `queue.py` or `queue_resolver.py`):
- `AUTO_RESOLVABLE_STEPS`: steps where the resolver may auto-fill — `{SCAN_PROCESS}` for v1 (expand later)
- `async_try_auto_resolve(coordinator, api_bbuddy, config_entry_data, barcode, mode) -> AutoResolveResult`:
  1. Create temporary `ScanSession`
  2. `handle_step(SCAN_START, {"barcode_input": barcode, "scan_mode": mode})`
  3. Loop (max 15):
     - `CompletedResult` → success
     - `AbortResult` → failure
     - `FormRequest` on auto-resolvable step → build input from `field.default` for each field. If any required field has no default → failure ("manual input needed: {field.key}")
     - `FormRequest` on non-auto-resolvable step → failure ("step {step_id} requires manual input")
  4. Wrap in try/except, return structured result

### Phase 4: OptionsFlow "Handle Queue"
**Write tests first** (`tests/test_scan_session_queue.py`):
- test_handle_queue_shows_pending_count
- test_handle_queue_empty_shows_no_items
- test_handle_queue_feeds_barcodes_to_session
- test_handle_queue_removes_items_on_completion

**Then implement:**
- Add `HANDLE_QUEUE = "handle_queue"` to `Step` enum in `scan_types.py`
- Add to `MAIN_MENU` in `config_flow.py` (menu now renders with 2 items)
- Add `async_step_handle_queue` to `GrocyOptionsFlowHandler`
- Implement `_step_handle_queue` in `ScanSession`:
  - On first call: fetch pending items from `coordinator.queue`, show summary form (count + list)
  - On submit: populate `self.barcode_queue` from pending items, chain to `_step_scan_queue()`
  - On each barcode completion: call `coordinator.queue.async_mark_resolved(item_id, result)` or `async_remove(item_id)`
- Add translations for handle_queue step

### Phase 5: FormField Default Audit
- Review `build_scan_process_fields` — set `default` = `suggested_value` for `best_before_in_days` when the value comes from the product's `default_best_before_days` (this IS the product's configured default, not a guess)
- Other fields: leave as-is for v1 (price, shopping_location are genuinely optional)
- Document which steps are auto-resolvable and which fields gate them

## Relevant Files
- `tests/` — **NEW** directory with conftest.py, test_queue.py, test_webhook.py, test_auto_resolver.py, test_scan_session_queue.py
- `custom_components/grocy_helper/queue.py` — **NEW** — QueueItem, QueueStatus, ScanQueue, auto-resolver
- `custom_components/grocy_helper/coordinator.py` — add `self.queue: ScanQueue`
- `custom_components/grocy_helper/__init__.py` — webhook registration, queue init, webhook handler
- `custom_components/grocy_helper/config_flow.py` — MAIN_MENU update, async_step_handle_queue
- `custom_components/grocy_helper/scan_types.py` — HANDLE_QUEUE step
- `custom_components/grocy_helper/scan_session.py` — _step_handle_queue, queue completion hooks
- `custom_components/grocy_helper/scan_form_builders.py` — default audit, queue config fields
- `custom_components/grocy_helper/const.py` — storage key constants
- `custom_components/grocy_helper/translations/en.json` — new strings
- `requirements-dev.txt` — add pytest, pytest-asyncio
- `pyproject.toml` or `pytest.ini` — **NEW** — pytest config

## Verification
1. `pytest tests/test_queue.py` — queue CRUD, persistence, mode switching
2. `pytest tests/test_webhook.py` — all payload variants, validation
3. `pytest tests/test_auto_resolver.py` — auto-resolve success/failure cases
4. `pytest tests/test_scan_session_queue.py` — Handle Queue OptionsFlow integration
5. Manual: POST to webhook URL with curl → item auto-resolves or queues
6. Manual: Enter OptionsFlow → Handle Queue → see pending items → process
7. Manual: Restart HA → verify queue persists
8. Manual: Scan "BBUDDY-AS" barcode via webhook → verify mode switches, subsequent scans use ADD_TO_SHOPPING_LIST

## Open Considerations
1. **Failed item retry in Handle Queue**: Should "Handle Queue" show failed items too, with option to retry auto-resolve or process manually? Recommendation: Yes, show failed items with error message, offer retry.
2. **Notification/event on auto-resolve**: Fire an HA event (`grocy_helper_queue_resolved`) so automations can react (e.g. TTS "Product X purchased")? Recommendation: Nice-to-have, add in Phase 2 if easy.
3. **Webhook response granularity**: Should the webhook wait for auto-resolve to complete and return success/failure per barcode? Or always return "queued" immediately? Recommendation: Return synchronously with per-barcode status since auto-resolve is fast (single Grocy API call for known products).
